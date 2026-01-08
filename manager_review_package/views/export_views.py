"""
Export Views

This module contains views for exporting test cases to Excel and HTML formats,
including snapshot functionality for historical exports.
"""

from datetime import datetime
from functools import reduce
from operator import or_

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Case, When, IntegerField, Value
from django.db.models.functions import Cast
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from ..models import (
    TestExecution, TestCase, SheetMeta, ProjectOverview,
    TestExecutionSnapshot
)
from testmanager.services import get_active_instance
from ..excel_export import build_testcase_export_workbook, _get_most_recently_created_version
from testmanager.constants import (
    PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
)
from testmanager.decorators import manager_required, is_manager
from testmanager.version_service import (
    sort_test_cases_by_version,
    parse_version
)
from testmanager.services import (
    check_all_tests_completed,
)
from testmanager.utils import get_requirement_id
from testmanager.logging_utils import log_error


# RISK REMOVAL: Using service function instead of local _check_all_tests_completed()
_check_all_tests_completed = check_all_tests_completed


def _create_snapshot_and_reset(sheet_filter="", sw="", version="", user=None, snapshot_name=""):
    """
    Create historical snapshot of latest-version executions.
    
    This function:
    - Works ONLY on most recently created version per SW part number
    - Validates all tests are completed before creating snapshot
    - Preserves all execution data (does NOT reset anything)
    - Creates read-only historical reference
    
    CRITICAL: This function does NOT reset or modify any live TestExecution data.
    It only creates a snapshot for historical reference.
    """
    # ==================================================
    # 1. ENFORCE LATEST VERSION ONLY (MANDATORY) + ACTIVE INSTANCE
    # ==================================================
    # Get active instance - only work with active instance data
    active_instance = get_active_instance()
    
    # Build base queryset with sheet and sw filters only (active instance only)
    qs = TestCase.objects.filter(instance=active_instance)
    if sheet_filter:
        qs = qs.filter(sheet_name=sheet_filter)
    if sw:
        qs = qs.filter(sw_part_number=sw)
    
    # Determine most recently created version PER SW part number
    sw_part_numbers = list(qs.values_list('sw_part_number', flat=True).distinct())
    sw_part_numbers = [sw_num for sw_num in sw_part_numbers if sw_num]
    
    if not sw_part_numbers:
        # No SW part numbers found
        return None
    
    # Get latest versions automatically (backend decides)
    latest_versions = _get_most_recently_created_version(sw_part_numbers)
    
    if not latest_versions:
        # No versions found
        return None
    
    # Filter test cases to ONLY latest versions
    version_filters_list = []
    for sw_num, version_val in latest_versions.items():
        version_filters_list.append(Q(sw_part_number=sw_num) & Q(app_sw_version=version_val))
    
    if version_filters_list:
        version_filter = reduce(or_, version_filters_list)
        qs = qs.filter(version_filter)
    else:
        # No valid versions to snapshot
        return None
    
    # ==================================================
    # 2. VALIDATE COMPLETION BEFORE SNAPSHOT
    # ==================================================
    # Check if all test cases of latest version are executed (active instance only)
    test_case_ids = list(qs.values_list('id', flat=True))
    executions = TestExecution.objects.filter(instance=active_instance, test_case_id__in=test_case_ids)
    
    # Filter executions to latest versions only
    exec_version_filters_list = [
        Q(sw_part_number=sw_num) & Q(app_sw_version=version_val)
        for sw_num, version_val in latest_versions.items()
    ]
    if exec_version_filters_list:
        exec_version_filter = reduce(or_, exec_version_filters_list)
        executions = executions.filter(exec_version_filter)
    
    # Count test cases with executions (non-empty status)
    executed_count = executions.exclude(status__isnull=True).exclude(status__exact="").count()
    total_count = qs.count()
    
    if total_count == 0:
        return None
    
    # Validate: ALL test cases must be executed
    if executed_count != total_count:
        # Not all tests completed - abort snapshot
        return None
    
    # ==================================================
    # 3. PREPARE SNAPSHOT DATA (READ-ONLY HISTORY)
    # ==================================================
    execution_data = []
    total_passed = 0
    total_failed = 0
    total_not_executed = 0
    
    for exec in executions.select_related('test_case', 'user'):
        tc = exec.test_case
        # Derive requirement_id from test_case_id if empty
        display_requirement_id = get_requirement_id(tc.test_case_id, tc.requirement_id)
        
        # Save complete test case data + execution data
        exec_dict = {
            # Test Case Details (ALL fields)
            'test_case_id': tc.test_case_id,
            'test_case_db_id': tc.id,  # Store DB ID for reference
            'sheet_name': tc.sheet_name,
            'sl_no': tc.sl_no,
            'sw_part_number': tc.sw_part_number,
            'feature': tc.feature,
            'requirement_id': display_requirement_id,  # Use derived requirement_id
            'requirement_description': tc.requirement_description,
            'test_case_summary': tc.test_case_summary,
            'pre_conditions': tc.pre_conditions,
            'inputs': tc.inputs,
            'periodic_time': tc.periodic_time,
            'test_steps': tc.test_steps,
            'expected_result': tc.expected_result,
            'app_sw_version': tc.app_sw_version,  # Test case version
            'status': tc.status,  # Test case design status
            'reports': tc.reports,  # Test case reports
            'comments': tc.comments,  # Test case comments
            
            # Execution Details
            'execution_status': exec.status,
            'execution_reports': exec.reports,
            'execution_comments': exec.comments,
            'execution_sw_part_number': exec.sw_part_number,
            'execution_app_sw_version': exec.app_sw_version,
            'executed_at': exec.executed_at.isoformat() if exec.executed_at else None,
            'executed_by': exec.user.username if exec.user else None,
            'executed_by_id': exec.user.id if exec.user else None,
        }
        execution_data.append(exec_dict)
        
        # Count based on execution status
        status_upper = (exec.status or "").upper()
        if status_upper == "PASS":
            total_passed += 1
        elif status_upper == "FAIL":
            total_failed += 1
        else:
            total_not_executed += 1
    
    # ==================================================
    # 4. GENERATE EXPORT ID & NAME (KEEP AS-IS)
    # ==================================================
    # Generate sequential export_id (export_1, export_2, etc.)
    last_snapshot = TestExecutionSnapshot.objects.order_by('-exported_at').first()
    if last_snapshot and last_snapshot.export_id:
        # Extract number from last export_id (e.g., "export_5" -> 5)
        try:
            last_num = int(last_snapshot.export_id.split('_')[-1])
            export_id = f"export_{last_num + 1}"
        except (ValueError, IndexError):
            # If parsing fails, count existing snapshots
            export_id = f"export_{TestExecutionSnapshot.objects.count() + 1}"
    else:
        # First export
        export_id = "export_1"
    
    # Create snapshot name
    if not snapshot_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"Export_{timestamp}"
        if sheet_filter:
            snapshot_name += f"_{sheet_filter}"
        if sw:
            snapshot_name += f"_{sw}"
        # Note: version is determined automatically, not from argument
    
    # Determine version string for snapshot (use first version if multiple)
    version_string = list(latest_versions.values())[0] if latest_versions else ""
    
    # ==================================================
    # 5. CREATE SNAPSHOT (READ-ONLY HISTORY)
    # ==================================================
    snapshot = TestExecutionSnapshot.objects.create(
        instance=active_instance,  # Link snapshot to active instance
        export_id=export_id,
        snapshot_name=snapshot_name,
        sheet_name=sheet_filter or "",
        sw_part_number=sw or "",
        app_sw_version=version_string,
        execution_data=execution_data,
        exported_by=user,
        total_test_cases=total_count,
        total_executed=len(execution_data),
        total_passed=total_passed,
        total_failed=total_failed,
        total_not_executed=total_not_executed,
    )
    
    # ==================================================
    # 6. STORE TIMESTAMP (KEEP AS-IS)
    # ==================================================
    # Store last export timestamp in ProjectOverview
    ProjectOverview.objects.update_or_create(
        key=PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
        defaults={"value": timezone.now().isoformat()}
    )
    
    # ==================================================
    # 7. CREATE NEW VERSION AND MARK OLD VERSION AS INACTIVE
    # ==================================================
    # BUG FIX: After snapshot, create NEW version and update SWVersionMapping
    # This ensures old versions disappear from UI and new version shows with blank execution data
    
    from ..models import SWVersionMapping
    
    with transaction.atomic():  # type: ignore
        new_version_mappings = {}
        
        # For each SW part number, create a new version
        for sw_num, old_version in latest_versions.items():
            # Increment version number
            # Try to parse version and increment (e.g., "V2.1" -> "V2.2", "2.1" -> "2.2")
            new_version = _increment_version(old_version)
            
            # Clone all test cases for this SW part number with new version
            old_test_cases = TestCase.objects.filter(
                instance=active_instance,
                sw_part_number=sw_num,
                app_sw_version=old_version
            )
            
            new_test_cases = []
            for old_tc in old_test_cases:
                # Build new test_case_id with new version
                base_test_case_id = old_tc.test_case_id
                # Remove old version suffix if exists
                if old_version and base_test_case_id.endswith(f"_{old_version}"):
                    base_test_case_id = base_test_case_id[:-len(f"_{old_version}")]
                
                new_test_case_id = f"{base_test_case_id}_{new_version}" if new_version else base_test_case_id
                
                # Check if new test case already exists
                if TestCase.objects.filter(
                    instance=active_instance,
                    test_case_id=new_test_case_id
                ).exists():
                    continue  # Skip if already exists
                
                # Clone test case with new version
                new_tc = TestCase(
                    instance=active_instance,
                    sheet_name=old_tc.sheet_name,
                    sl_no=old_tc.sl_no,
                    sw_part_number=sw_num,
                    feature=old_tc.feature,
                    requirement_id=old_tc.requirement_id,
                    requirement_description=old_tc.requirement_description,
                    test_case_id=new_test_case_id,
                    test_case_summary=old_tc.test_case_summary,
                    pre_conditions=old_tc.pre_conditions,
                    inputs=old_tc.inputs,
                    periodic_time=old_tc.periodic_time,
                    test_steps=old_tc.test_steps,
                    expected_result=old_tc.expected_result,
                    app_sw_version=new_version,  # NEW VERSION
                    # Reset execution data
                    status="",
                    reports="",
                    comments="",
                )
                new_test_cases.append(new_tc)
            
            # Bulk create new test cases
            if new_test_cases:
                TestCase.objects.bulk_create(new_test_cases, batch_size=500)
                
                # Create new TestExecution rows for new version (with empty status/reports/comments)
                new_executions = []
                for new_tc in new_test_cases:
                    # Check if execution already exists
                    if not TestExecution.objects.filter(
                        instance=active_instance,
                        test_case_id=new_tc.id,
                        sw_part_number=sw_num,
                        app_sw_version=new_version
                    ).exists():
                        new_executions.append(
                            TestExecution(
                                instance=active_instance,
                                test_case_id=new_tc.id,
                                sw_part_number=sw_num,
                                app_sw_version=new_version,
                                status="",  # Empty - new cycle
                                reports="",  # Empty - new cycle
                                comments="",  # Empty - new cycle
                            )
                        )
                
                if new_executions:
                    TestExecution.objects.bulk_create(new_executions, batch_size=500)
            
            # Update SWVersionMapping to point to NEW version (marks old version as inactive)
            # This is the key: updating SWVersionMapping makes old version disappear from UI
            # STRICT VERSION LIFECYCLE: Set is_active=True for new version
            SWVersionMapping.objects.update_or_create(
                instance=active_instance,
                sw_part_number=sw_num,
                defaults={
                    "version": new_version,
                    "is_active": True,  # STRICT: Ensure new version is active
                    "updated_at": timezone.now()
                }
            )
            
            new_version_mappings[sw_num] = new_version
        
        # DO NOT reset old execution data - keep it for history
        # Old versions remain accessible via snapshot/history links
    
    return snapshot


def _increment_version(version_str):
    """
    Increment version number.
    Examples:
    - "V2.1" -> "V2.2"
    - "2.1" -> "2.2"
    - "V2.1.0" -> "V2.1.1"
    - "2" -> "3"
    """
    if not version_str:
        return "V1.0"
    
    # Try to parse version using version_service
    try:
        parts_tuple = parse_version(version_str)
        if parts_tuple and parts_tuple != (999999, 0, 0):  # Valid parsed version
            major, minor, patch = parts_tuple
            
            # Increment patch version
            patch += 1
            
            # Reconstruct version string
            prefix = ""
            if version_str.startswith("V") or version_str.startswith("v"):
                prefix = "V"
            
            if patch > 0:
                version_str = f"{prefix}{major}.{minor}.{patch}"
            elif minor > 0:
                version_str = f"{prefix}{major}.{minor}"
            else:
                version_str = f"{prefix}{major}"
            
            return version_str
    except Exception:
        pass
    
    # Fallback: simple increment
    # Try to find last number and increment
    import re
    match = re.search(r'(\d+)(?!.*\d)', version_str)
    if match:
        last_num = int(match.group(1))
        new_num = last_num + 1
        return version_str[:match.start()] + str(new_num) + version_str[match.end():]
    
    # If no number found, append ".1"
    return f"{version_str}.1"


@login_required
@manager_required
def export_excel(request):
    """
    Export test cases to Excel.
    
    STRICT EXPORT RULE: Export includes ONLY the most recently ACTIVE version (is_active=True) for each SW Part Number.
    Version selection is automatic - backend resolves to active version only.
    
    Behavior:
    - Automatically exports ALL test cases from active version only
    - Automatically selects active version (is_active=True) for each SW Part Number
    - IGNORES all request parameters (sheet, sw, version, feature, query)
    - Version parameter is completely ignored - no manual version selection allowed
    - No prompts, no user inputs
    - Immediately downloads Excel file via direct HTTP response
    - Old and executed versions are completely excluded from export
    """
    # Pre-export validation: Check if all tests are completed
    # Check ALL sheets, ALL SW part numbers, ALL versions (export uses latest automatically)
    all_completed, executed_count, total_count = check_all_tests_completed(
        sheet_filter="",  # Check all sheets
        sw="",  # Check all SW part numbers
        version_obj=""  # Check all versions (export uses latest automatically)
    )
    
    if not all_completed:
        messages.error(request, f"Cannot export: {executed_count}/{total_count} tests completed. All tests must be executed before exporting.")
        return redirect("testcase_list")
    
    # Build workbook - STRICT: IGNORE all request parameters including version
    # Version selection is automatic - backend resolves to active version (is_active=True) only
    # Backend decides export scope internally - ONLY active versions exported
    try:
        wb = build_testcase_export_workbook(
            sheet_filter="",  # Export all sheets (ignore request parameters)
            sw="",  # Export all SW part numbers (ignore request parameters)
            app_sw_version="",  # STRICT: IGNORED - always uses active version (is_active=True)
            feature="",  # Export all features (ignore request parameters)
            query="",  # Export all test cases (ignore request parameters)
            versions_list=[],  # STRICT: IGNORED - always uses active version
            latest_versions_only=True,  # STRICT: Always True - ONLY active versions exported
        )
    except Exception as e:
        log_error("views.py:export_excel", "Error creating workbook", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        messages.error(request, f"Error creating export: {str(e)}")
        return redirect("testcase_list")
    
    # Create HTTP response
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=TestCases_Export.xlsx"
    wb.save(response)
    
    # Store last export timestamp
    ProjectOverview.objects.update_or_create(
        key=PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
        defaults={"value": timezone.now().isoformat()}
    )
    
    # Set session flag for post-export modal
    request.session['export_completed'] = True
    return response


@manager_required
def export_html(request):
    """
    Export test cases as HTML view-only page with charts
    
    STRICT EXPORT RULES:
    - Only MANAGER can trigger HTML export
    - HTML report generated ONLY AFTER all test cases of ACTIVE VERSION (is_active=True) are completed
    - Uses ONLY the active version (is_active=True) - no manual version selection allowed
    - Filters (sheet, sw, feature) are for VIEW/GROUPING only - do not change version or execution scope
    - Version parameter is completely ignored - backend auto-resolves to active version
    - Old and executed versions are completely excluded from export
    """
    from django.urls import reverse
    
    # Accept filters from request (for VIEW/GROUPING only - backend enforces active version)
    selected_sheet = request.GET.get("sheet", "").strip()
    selected_sw = request.GET.get("sw", "").strip()
    selected_feature = request.GET.get("feature", "").strip()
    create_snapshot = request.GET.get("create_snapshot", "").strip() == "1"
    # STRICT: Version parameter is completely ignored - backend always uses active version (is_active=True)
    # No manual version selection allowed - export always includes only active version data
    
    # Get active instance - all exports must show only active instance data
    active_instance = get_active_instance()
    
    # Pre-export validation: Check if all tests are completed for latest version
    # First, determine latest versions, then check completion (active instance only)
    all_test_cases = TestCase.objects.filter(instance=active_instance)
    sw_part_numbers = list(all_test_cases.values_list('sw_part_number', flat=True).distinct())
    sw_part_numbers = [sw for sw in sw_part_numbers if sw]
    
    # Get most recently created versions using same logic as Excel export
    latest_versions = _get_most_recently_created_version(sw_part_numbers)
    
    # Check if all tests are completed for latest version only
    all_completed, executed_count, total_count = check_all_tests_completed(
        sheet_filter="",  # Check all sheets
        sw="",  # Check all SW part numbers
        version_obj=""  # Check latest version (handled internally by check_all_tests_completed)
    )
    
    # Handle snapshot creation request
    if create_snapshot:
        # Validate: user must be authenticated and manager
        if not request.user.is_authenticated:
            messages.error(request, "You must be logged in to create snapshots.")
            return redirect("testcase_list")
        
        if not (is_manager(request.user) or request.user.is_superuser):
            messages.error(request, "Only managers can create snapshots.")
            return redirect("testcase_list")
        
        # Validate: all tests must be completed
        if not all_completed:
            messages.error(request, "All test cases must be completed before creating a snapshot.")
            return redirect("testcase_list")
        
        # Create snapshot (this will also reset execution data)
        snapshot = _create_snapshot_and_reset(
            sheet_filter=selected_sheet,
            sw=selected_sw,
            version="",  # Version determined automatically by function
            user=request.user
        )
        
        if snapshot:
            # Redirect to snapshot view
            messages.success(request, f"Snapshot '{snapshot.snapshot_name}' created successfully. Execution data has been reset for the next cycle.")
            return redirect("export_html_snapshot", export_id=snapshot.export_id)
        else:
            messages.error(request, "Failed to create snapshot. Please ensure all tests are completed.")
            return redirect("testcase_list")
    
    if not all_completed:
        messages.error(request, "Complete all test cases to generate HTML report.")
        return redirect("testcase_list")
    
    # Build base queryset with view-only filters (sheet, sw, feature)
    # STRICT: Filter by active instance only - exports only include active instance data
    base_qs = TestCase.objects.filter(instance=active_instance)
    
    if selected_sheet:
        base_qs = base_qs.filter(sheet_name=selected_sheet)
    if selected_sw:
        base_qs = base_qs.filter(sw_part_number=selected_sw)
    if selected_feature:
        base_qs = base_qs.filter(feature=selected_feature)
    
    # STRICT: Filter to ONLY active versions (is_active=True) - like Excel export
    # This ensures consistency with Excel export - only active version data is exported
    # Old and executed versions are completely excluded
    if latest_versions:
        version_filters_list = []
        for sw_num, version in latest_versions.items():
            version_filters_list.append(Q(sw_part_number=sw_num) & Q(app_sw_version=version))
        
        if version_filters_list:
            version_filter = reduce(or_, version_filters_list)
            base_qs = base_qs.filter(version_filter)
    
    # --- Bar Chart Data (matches home page logic) ---
    # CASE 1: NO FILTER → Sheet-wise totals
    if not selected_sheet:
        bar_mode = "total_per_sheet"
        sheet_counts = (
            base_qs
            .values("sheet_name")
            .annotate(count=Count("id"))
            .order_by("sheet_name")
        )
        sheet_labels = [row["sheet_name"] for row in sheet_counts]
        sheet_values = [row["count"] for row in sheet_counts]
    
    # CASE 2: SHEET SELECTED → SW Part Number-wise totals
    elif selected_sheet and not selected_sw:
        bar_mode = "total_per_sw"
        sw_counts = (
            base_qs
            .filter(sheet_name=selected_sheet)
            .exclude(sw_part_number__isnull=True)
            .exclude(sw_part_number__exact="")
            .values("sw_part_number")
            .annotate(count=Count("id"))
            .order_by("sw_part_number")
        )
        sheet_labels = [row["sw_part_number"] for row in sw_counts]
        sheet_values = [row["count"] for row in sw_counts]
    
    # CASE 3: SHEET + SW SELECTED → Status overview (PASS, FAIL, NOT RELEVANT)
    else:
        bar_mode = "status_overview"
        # Get executions for this specific sheet and SW
        sw_qs = base_qs.filter(
            sheet_name=selected_sheet,
            sw_part_number=selected_sw
        )
        sw_test_case_ids = sw_qs.values_list('id', flat=True)
        # CRITICAL: Filter by instance to ensure instance isolation
        sw_executions = TestExecution.objects.filter(
            instance=active_instance,  # CRITICAL: Filter by instance
            test_case_id__in=sw_test_case_ids
        )
        
        # Filter executions to latest version for this SW
        if selected_sw in latest_versions:
            sw_executions = sw_executions.filter(
                sw_part_number=selected_sw,
                app_sw_version=latest_versions[selected_sw]
            )
        
        # Count by status from executions (for bar chart only)
        bar_pass_count = sw_executions.filter(status__iexact="pass").count()
        bar_fail_count = sw_executions.filter(status__iexact="fail").count()
        bar_not_relevant_count = sw_executions.filter(
            Q(status__iexact="not relevant") | Q(status__iexact="not_relevant")
        ).count()
        bar_total_count = sw_qs.count()
        bar_not_executed_count = bar_total_count - bar_pass_count - bar_fail_count - bar_not_relevant_count
        
        sheet_labels = ["Total Test Cases", "Pass", "Fail", "NOT RELEVANT"]
        sheet_values = [bar_total_count, bar_pass_count, bar_fail_count, bar_not_relevant_count]
    
    # Order by sheet_name and sl_no (numerically) before attaching executions
    base_qs = base_qs.annotate(
        sl_no_int=Case(
            When(sl_no__regex=r"^\d+$", then=Cast("sl_no", IntegerField())),
            default=Value(999999),
            output_field=IntegerField(),
        )
    ).order_by("sheet_name", "sl_no_int")
    
    # STRICT: Get executions for table - filter to ONLY active versions (is_active=True)
    # Old and executed versions are completely excluded from export
    test_case_ids = base_qs.values_list('id', flat=True)
    executions = TestExecution.objects.filter(instance=active_instance, test_case__in=test_case_ids).select_related("test_case")
    
    # STRICT: Filter executions to ONLY active versions (is_active=True) - consistent with Excel export
    if latest_versions:
        exec_version_filters_list = [
            Q(sw_part_number=sw_num) & Q(app_sw_version=version)
            for sw_num, version in latest_versions.items()
        ]
        if exec_version_filters_list:
            exec_version_filters = reduce(or_, exec_version_filters_list)
            executions = executions.filter(exec_version_filters)
    
    # Apply view-only filters (do not change version scope)
    if selected_sheet:
        executions = executions.filter(test_case__sheet_name=selected_sheet)
    if selected_sw:
        executions = executions.filter(sw_part_number=selected_sw)
    
    # STRICT: Build execution map for active versions only (is_active=True)
    # Key is (test_case.id, sw_part_number, app_sw_version) to ensure version isolation
    # Old and executed versions are completely excluded from export
    execution_map = {}
    for e in executions:
        tc_id = e.test_case.id
        sw_num = e.sw_part_number or ""
        exec_version = e.app_sw_version or ""
        key = (tc_id, sw_num, exec_version)
        execution_map[key] = e
    
    # Convert queryset to list for sorting
    test_cases_list = list(base_qs)
    
    # STRICT: Create map by test_case.id matching test case's version (active version only)
    # Only active version execution data is included in export
    execution_map_by_tc = {}
    for tc in test_cases_list:
        tc_id = tc.id
        tc_sw = tc.sw_part_number or ""
        tc_version = tc.app_sw_version or ""
        exec_key = (tc_id, tc_sw, tc_version)
        if exec_key in execution_map:
            execution_map_by_tc[tc_id] = execution_map[exec_key]
    
    # Attach executions to test cases and clean test_case_id for export
    for tc in test_cases_list:
        # Use execution_map_by_tc for lookup
        tc.exec = execution_map_by_tc.get(tc.id)
        # Remove version suffix from test_case_id for export display
        if tc.test_case_id and tc.app_sw_version:
            if tc.test_case_id.endswith(f"_{tc.app_sw_version}"):
                tc.display_test_case_id = tc.test_case_id[:-len(f"_{tc.app_sw_version}")]
            elif "_" in tc.test_case_id:
                parts = tc.test_case_id.rsplit("_", 1)
                if len(parts) == 2 and ("." in parts[1] or parts[1].replace(".", "").isdigit()):
                    tc.display_test_case_id = parts[0]
                else:
                    tc.display_test_case_id = tc.test_case_id
            else:
                tc.display_test_case_id = tc.test_case_id
        else:
            tc.display_test_case_id = tc.test_case_id or ""
        # Auto-derive requirement_id from test_case_id if requirement_id is empty
        tc.display_requirement_id = get_requirement_id(tc.test_case_id, tc.requirement_id)
    
    # Sort by version (newest first), then by sl_no
    test_cases_list = sort_test_cases_by_version(test_cases_list)
    
    # Summary counts - use execution data, not test case status
    total_test_cases = len(test_cases_list)
    
    # Count executions by status - use the executions we already filtered for latest versions
    execution_status_counts = {}
    for exec in executions:
        status_upper = (exec.status or "").upper()
        if status_upper == "PASS":
            execution_status_counts["PASS"] = execution_status_counts.get("PASS", 0) + 1
        elif status_upper == "FAIL":
            execution_status_counts["FAIL"] = execution_status_counts.get("FAIL", 0) + 1
        elif status_upper in ("NOT RELEVANT", "NOT_RELEVANT"):
            execution_status_counts["NOT RELEVANT"] = execution_status_counts.get("NOT RELEVANT", 0) + 1
        else:
            execution_status_counts["NOT EXECUTED"] = execution_status_counts.get("NOT EXECUTED", 0) + 1
    
    passed_count = execution_status_counts.get("PASS", 0)
    failed_count = execution_status_counts.get("FAIL", 0)
    not_relevant_count = execution_status_counts.get("NOT RELEVANT", 0)
    not_executed_count = total_test_cases - passed_count - failed_count - not_relevant_count
    total_executed = passed_count + failed_count
    
    # Calculate percentages
    pass_percentage = (passed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    fail_percentage = (failed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    not_exec_percentage = (not_executed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    executed_percentage = (total_executed / total_test_cases * 100) if total_test_cases > 0 else 0
    
    # Status Distribution (Pie Chart Data) - use execution counts from latest version only
    status_labels = ["PASS", "FAIL", "NOT RELEVANT", "NOT EXECUTED"]
    status_values = [passed_count, failed_count, not_relevant_count, not_executed_count]
    
    # Get Project Overview data (same as Excel export)
    project_overview_data = {}
    for po in ProjectOverview.objects.exclude(key__in=['last_export_timestamp']):
        project_overview_data[po.key] = po.value
    
    # Get filter lists for dropdowns (view-only filters)
    sheet_names = list(
        SheetMeta.objects.values_list("sheet_name", flat=True).distinct().order_by("sheet_name")
    )
    if not sheet_names:
        sheet_names = list(
            TestCase.objects.values_list("sheet_name", flat=True).distinct().order_by("sheet_name")
        )
    
    sw_list = []
    if selected_sheet:
        sw_raw = TestCase.objects.filter(instance=active_instance, sheet_name=selected_sheet).values_list("sw_part_number", flat=True)
        sw_list = sorted(set([str(s).strip() for s in sw_raw if s and str(s).strip()]))
    else:
        sw_raw = TestCase.objects.exclude(sw_part_number__isnull=True).exclude(sw_part_number__exact="").values_list("sw_part_number", flat=True)
        sw_list = sorted(set([str(s).strip() for s in sw_raw if s and str(s).strip()]))
    
    # Feature list for filter dropdown
    feature_list = []
    if selected_sheet and selected_sw:
        feature_raw = TestCase.objects.filter(
            instance=active_instance, sheet_name=selected_sheet, sw_part_number=selected_sw
        ).values_list("feature", flat=True)
        feature_list = sorted(set(f for f in feature_raw if f and str(f).strip()))
    
    # Check if user is manager (for button visibility)
    is_manager_user = is_manager(request.user) or request.user.is_superuser
    
    # Build context for template
    context = {
        # Project Overview data
        "project_code": project_overview_data.get("project_code", ""),
        "vcu_platform": project_overview_data.get("vcu_platform", ""),
        "hw_part_number": project_overview_data.get("hw_part_number", ""),
        "sw_part_number": project_overview_data.get("sw_part_number", ""),
        "project_stage": project_overview_data.get("project_stage", ""),
        "developer": project_overview_data.get("developer", ""),
        "test_engineer": project_overview_data.get("test_engineer", ""),
        "app_sw_version": project_overview_data.get("app_sw_version", ""),
        "bootloader_sw_version": project_overview_data.get("bootloader_sw_version", ""),
        "checksum_value": project_overview_data.get("checksum_value", ""),
        "dbc_test_it": project_overview_data.get("dbc_test_it", ""),
        
        # Test case data
        "tests": test_cases_list,
        
        # Filter values (view-only)
        "selected_sheet": selected_sheet,
        "selected_sw": selected_sw,
        "selected_feature": selected_feature,
        
        # Chart data
        "status_labels": status_labels,
        "status_values": status_values,
        "sheet_labels": sheet_labels,
        "sheet_values": sheet_values,
        "bar_mode": bar_mode,
        
        # Summary statistics
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_relevant_count": not_relevant_count,
        "not_executed_count": not_executed_count,
        "total_executed": total_executed,
        "total_test_cases": total_test_cases,
        "pass_percentage": round(pass_percentage, 2),
        "fail_percentage": round(fail_percentage, 2),
        "not_exec_percentage": round(not_exec_percentage, 2),
        "executed_percentage": round(executed_percentage, 2),
        
        # Filter options for dropdowns
        "sheet_names": sheet_names,
        "sw_list": sw_list,
        "feature_list": feature_list,
        
        # Snapshot button visibility
        "all_completed": all_completed,
        "executed_count": executed_count,
        "total_count": total_count,
        "is_manager": is_manager_user,
        "is_snapshot": False,
        "now": timezone.now(),
    }
    
    html_content = render(request, "testmanager/export_html.html", context)
    
    return html_content


def export_html_snapshot(request, export_id):
    """
    View historical snapshot of test executions by export_id
    Reconstructs test cases and executions from saved snapshot data (not from live database)
    """
    try:
        snapshot = TestExecutionSnapshot.objects.get(export_id=export_id)
    except TestExecutionSnapshot.DoesNotExist:
        messages.error(request, f"Snapshot '{export_id}' not found.")
        return redirect("export_html")
    
    # Reconstruct test cases and executions from snapshot data (NOT from live database)
    execution_data = snapshot.execution_data
    
    # Create mock test case objects from snapshot data
    class MockTestCase:
        def __init__(self, data):
            self.id = data.get('test_case_db_id', 0)  # Use DB ID if available, otherwise 0
            self.test_case_id = data.get('test_case_id', '')
            self.sheet_name = data.get('sheet_name', '')
            self.sl_no = data.get('sl_no', '')
            self.sw_part_number = data.get('sw_part_number', '')
            self.feature = data.get('feature', '')
            self.requirement_id = data.get('requirement_id', '')
            self.requirement_description = data.get('requirement_description', '')
            self.test_case_summary = data.get('test_case_summary', '')
            self.pre_conditions = data.get('pre_conditions', '')
            self.inputs = data.get('inputs', '')
            self.periodic_time = data.get('periodic_time', '')
            self.test_steps = data.get('test_steps', '')
            self.expected_result = data.get('expected_result', '')
            self.app_sw_version = data.get('app_sw_version', '')
            self.status = data.get('status', '')  # Test case design status
            self.reports = data.get('reports', '')  # Test case reports
            self.comments = data.get('comments', '')  # Test case comments
            
            # Handle display_test_case_id
            if self.test_case_id and self.app_sw_version:
                if self.test_case_id.endswith(f"_{self.app_sw_version}"):
                    self.display_test_case_id = self.test_case_id[:-len(f"_{self.app_sw_version}")]
                else:
                    self.display_test_case_id = self.test_case_id
            else:
                self.display_test_case_id = self.test_case_id or ""
            # Auto-derive requirement_id from test_case_id if requirement_id is empty
            self.display_requirement_id = get_requirement_id(self.test_case_id, self.requirement_id)
            
            self.exec = None  # Will be set below
    
    # Create mock execution object
    class MockExecution:
        def __init__(self, data):
            # Use execution data (preferred) or fallback to test case data
            self.status = data.get('execution_status', '') or data.get('status', '')
            self.reports = data.get('execution_reports', '') or data.get('reports', '')
            self.comments = data.get('execution_comments', '') or data.get('comments', '')
            self.sw_part_number = data.get('execution_sw_part_number', '') or data.get('sw_part_number', '')
            self.app_sw_version = data.get('execution_app_sw_version', '') or data.get('app_sw_version', '')
            self.executed_at = data.get('executed_at')
            self.user = None
            if data.get('executed_by'):
                try:
                    from django.contrib.auth.models import User
                    self.user = User.objects.get(username=data.get('executed_by'))
                except:
                    pass
    
    # Reconstruct test cases from snapshot data
    test_cases = []
    for exec_data in execution_data:
        mock_tc = MockTestCase(exec_data)
        mock_exec = MockExecution(exec_data)
        mock_tc.exec = mock_exec
        test_cases.append(mock_tc)
    
    # Sort by sheet_name and sl_no (convert sl_no to int for proper sorting)
    def sort_key(tc):
        try:
            sl_no_int = int(tc.sl_no) if tc.sl_no and tc.sl_no.isdigit() else 999999
        except:
            sl_no_int = 999999
        return (tc.sheet_name or '', sl_no_int)
    
    test_cases.sort(key=sort_key)
    
    # Calculate statistics from snapshot
    passed_count = snapshot.total_passed
    failed_count = snapshot.total_failed
    not_executed_count = snapshot.total_not_executed
    total_test_cases = snapshot.total_test_cases
    total_executed = passed_count + failed_count
    
    # Calculate percentages
    pass_percentage = (passed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    fail_percentage = (failed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    not_exec_percentage = (not_executed_count / total_test_cases * 100) if total_test_cases > 0 else 0
    executed_percentage = (total_executed / total_test_cases * 100) if total_test_cases > 0 else 0
    
    # Status distribution for pie chart
    status_map = {
        "PASS": passed_count,
        "FAIL": failed_count,
        "NOT EXECUTED": not_executed_count,
        "OTHER": 0
    }
    status_labels = list(status_map.keys())
    status_values = [status_map[k] for k in status_labels]
    
    # Bar chart data
    if snapshot.sheet_name and snapshot.sw_part_number:
        bar_mode = "status_overview"
        sheet_labels = ["Total Test Cases", "Pass", "Fail", "Not Executed"]
        sheet_values = [total_test_cases, passed_count, failed_count, not_executed_count]
    elif snapshot.sheet_name:
        bar_mode = "total_per_sw"
        # Get SW distribution from snapshot data
        sw_counts = {}
        for exec_data in execution_data:
            sw = exec_data.get('sw_part_number', '')
            if sw:
                sw_counts[sw] = sw_counts.get(sw, 0) + 1
        sheet_labels = sorted(sw_counts.keys())
        sheet_values = [sw_counts[sw] for sw in sheet_labels]
    else:
        bar_mode = "total_per_sheet"
        # Get sheet distribution from test cases
        sheet_counts = {}
        for tc in test_cases:
            sheet = tc.sheet_name
            if sheet:
                sheet_counts[sheet] = sheet_counts.get(sheet, 0) + 1
        sheet_labels = sorted(sheet_counts.keys())
        sheet_values = [sheet_counts[sheet] for sheet in sheet_labels]
    
    # Trend data - not available for snapshots, use empty
    trend_labels = []
    trend_data = []
    
    # Get filter lists
    sheet_names = list(
        SheetMeta.objects.values_list("sheet_name", flat=True).distinct().order_by("sheet_name")
    )
    if not sheet_names:
        sheet_names = list(
            TestCase.objects.values_list("sheet_name", flat=True).distinct().order_by("sheet_name")
        )
    
    sw_list = []
    if snapshot.sheet_name:
        # Get SW list from snapshot data
        sw_set = set()
        for tc in test_cases:
            if tc.sheet_name == snapshot.sheet_name and tc.sw_part_number:
                sw_set.add(str(tc.sw_part_number).strip())
        sw_list = sorted([s for s in sw_set if s])
    
    version_list = []
    if snapshot.sheet_name and snapshot.sw_part_number:
        # Get versions from snapshot data, not from live database
        version_set = set()
        for tc in test_cases:
            if tc.sheet_name == snapshot.sheet_name and tc.sw_part_number == snapshot.sw_part_number:
                # Check execution version
                if tc.exec and tc.exec.app_sw_version:
                    version_set.add(tc.exec.app_sw_version)
                # Also check test case version
                elif tc.app_sw_version:
                    version_set.add(tc.app_sw_version)
        version_list = sorted(list(version_set))
    
    from django.utils import timezone
    
    context = {
        "tests": test_cases,  # Already sorted above
        "selected_sheet": snapshot.sheet_name,
        "selected_sw": snapshot.sw_part_number,
        "selected_version": snapshot.app_sw_version,
        "selected_feature": "",
        "search_query": "",
        "status_labels": status_labels,
        "status_values": status_values,
        "sheet_labels": sheet_labels,
        "sheet_values": sheet_values,
        "bar_mode": bar_mode,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_executed_count": not_executed_count,
        "total_executed": total_executed,
        "total_test_cases": total_test_cases,
        "pass_percentage": round(pass_percentage, 2),
        "fail_percentage": round(fail_percentage, 2),
        "not_exec_percentage": round(not_exec_percentage, 2),
        "executed_percentage": round(executed_percentage, 2),
        "trend_labels": trend_labels,
        "trend_data": trend_data,
        "sheets": sheet_names,
        "sw_list": sw_list,
        "version_list": version_list,
        "all_completed": True,  # Snapshots are always completed
        "executed_count": total_executed,
        "total_count": total_test_cases,
        "snapshot_created": snapshot,
        "is_snapshot": True,
        "current_export_id": export_id,
        "snapshot_name": snapshot.snapshot_name,
        "exported_at": snapshot.exported_at,
        "exported_by": snapshot.exported_by,
        # FIX: Do NOT use timezone.now() for snapshots - use exported_at only
        # This ensures the timestamp is immutable and doesn't change on refresh
        "now": snapshot.exported_at,  # Use exported_at as immutable timestamp
        "recent_snapshots": TestExecutionSnapshot.objects.exclude(export_id=export_id).order_by('-exported_at')[:10],
    }
    
    html_content = render(request, "testmanager/export_html.html", context)
    
    return html_content

