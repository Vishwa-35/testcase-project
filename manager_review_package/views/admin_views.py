"""
Home and Dashboard Views

This module contains views for the home page/dashboard and post-login redirects.
"""

import os
import pandas as pd
from functools import reduce
from operator import or_

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.urls import reverse
import json

from ..models import (
    ActivityLog, TestExecution, TestCase, SheetMeta, SWVersionMapping, 
    TestCaseVersion, TestCaseSheet, ProjectOverview
)
from testmanager.constants import ACTIVITY_ACTION_EDIT
from testmanager.decorators import is_manager, is_developer, manager_required, can_view_history, can_export_reports, can_create_instance
from testmanager.version_service import sort_versions, sort_test_cases_by_version
from testmanager.services import get_active_instance


@login_required
def post_login_redirect(request):
    """
    Redirect users after login:
    - Superuser or Manager → Admin
    - Others → Home
    """
    user = request.user
    
    # Check if user is Manager (via profile) or superuser
    is_manager_check = is_manager(user)

    if user.is_superuser or is_manager_check:
        return redirect("/admin/")
    return redirect("home")


# -------------------------------------------------------------------------------------
# HOME / DASHBOARD
# -------------------------------------------------------------------------------------
@login_required
def home(request):
    """
    Dashboard:
      - Reads project overview (unchanged)
      - Accepts optional filters: ?sheet=... & ?sw=...
      - Groups status case-insensitively and reduces to buckets:
           PASS, FAIL, NOT EXECUTED, OTHER
      - Provides sheet list and sw list for filter selects
    """
    activity_logs = ActivityLog.objects.order_by("-timestamp")[:20]

    context = {}

    # REMOVED: ProjectOverview usage - home page derives data ONLY from:
    # - TestCaseVersion
    # - TestCaseSheet
    # - TestExecution
    # Do NOT store summary data separately

    # VERSION-SPECIFIC DASHBOARD: Get filters strictly from URL parameters
    selected_sheet = (request.GET.get("sheet") or "").strip()
    selected_sw = (request.GET.get("sw") or "").strip()
    selected_version = (request.GET.get("version") or "").strip()
    selected_feature = (request.GET.get("feature") or "").strip()

    # Get active instance - all queries must filter by active instance only
    active_instance = get_active_instance()
    
    # VERSION-SPECIFIC DASHBOARD: Build base queryset with filters
    # STRICT: TestCase.sheet_name and sl_no DO NOT EXIST - removed after hierarchy refactor
    # Sheet filtering MUST be done via: TestCase → TestCaseVersion → TestCaseSheet → sheet_name
    # CRITICAL: Override model's default ordering (Meta.ordering includes sheet_name/sl_no which don't exist)
    # Use explicit order_by to prevent Django from using model's default ordering
    # Order by id (primary key) instead of deprecated sl_no field
    base_qs = TestCase.objects.filter(instance=active_instance).order_by('id')

    # CRITICAL: Sheet filtering must use TestCaseSheet FK relationship
    # TestCase doesn't have direct FK to TestCaseSheet, so we match via TestCaseVersion
    if selected_sheet:
        from ..models import TestCaseSheet
        # Find TestCaseSheet objects with this sheet_name
        sheets = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=selected_sheet
        ).select_related('version')
        # Get versions from these sheets and filter TestCase by matching sw_part_number + app_sw_version
        version_filters_list = []
        for sheet in sheets:
            version_filters_list.append(Q(
                sw_part_number=sheet.version.sw_part_number,
                app_sw_version=sheet.version.app_sw_version
            ))
        if version_filters_list:
            version_filters = reduce(or_, version_filters_list)
            base_qs = base_qs.filter(version_filters)

    if selected_sw:
        base_qs = base_qs.filter(sw_part_number=selected_sw)
    
    # FEATURE FILTERING: Filter by TestCase.feature (feature belongs to TestCase, not Version/Sheet/Execution)
    if selected_feature:
        base_qs = base_qs.filter(feature=selected_feature)

    # VERSION-SPECIFIC DASHBOARD: Filter by version
    # If version is selected, show ONLY that version's test cases
    # Otherwise, show latest versions only
    sw_part_numbers = base_qs.values_list('sw_part_number', flat=True).distinct()
    latest_versions = {}
    sw_with_mappings = set()
    
    # Check if user is Manager (needed for role-based version filtering)
    is_manager_check = is_manager(request.user)
    if request.user.is_superuser:
        is_manager_check = True
    
    # SECURITY: For non-managers, resolve active version ONCE using role-based filtering
    from testmanager.version_service import get_active_version_for_user, get_versions_for_user
    active_version = None
    if not is_manager_check:
        # Non-manager: MUST use only the most recent active version
        if selected_sw:
            active_version = get_active_version_for_user(request.user, active_instance, selected_sw)
        else:
            # If no SW selected, get the most recent active version across all SW
            versions = get_versions_for_user(request.user, active_instance)
            active_version = versions.filter(is_active=True).first()
        
        # Force selected_version to active version for non-managers
        if active_version:
            selected_version = active_version.app_sw_version
            if not selected_sw:
                selected_sw = active_version.sw_part_number
    
    if selected_version:
        # Manager: Can select any version
        # Non-manager: selected_version is already set to active version above
        pass  # Version filtering will be done via execution queries
    else:
        # ROLE-BASED VERSION FILTERING: No version selected
        # Managers: Show all versions (no filtering by active status)
        # Non-managers: Show only latest active versions (is_active=True)
        if is_manager_check:
            # Manager: Show all versions (no filtering by active status)
            # Filter by latest versions only - show only the most recent version for each SW Part Number
            for sw_num in sw_part_numbers:
                if sw_num:
                    mapping = SWVersionMapping.objects.filter(
                        instance=active_instance,  # PART 1: Only active instance
                        sw_part_number=sw_num
                    ).order_by('-updated_at').first()
                    if mapping:
                        latest_versions[sw_num] = mapping.version
                        sw_with_mappings.add(sw_num)
        else:
            # Non-manager: Show only latest active versions (is_active=True)
            for sw_num in sw_part_numbers:
                if sw_num:
                    mapping = SWVersionMapping.objects.filter(
                        instance=active_instance,  # PART 1: Only active instance
                        sw_part_number=sw_num,
                        is_active=True  # Only active versions for non-managers
                    ).first()
                    if mapping:
                        latest_versions[sw_num] = mapping.version
                        sw_with_mappings.add(sw_num)
        
        # Filter test cases to only include those with latest versions
        # Only filter SW part numbers that have mappings; others show all versions
        if sw_part_numbers:
            version_filters_list = []
            
            # Add filters for SW with mappings (latest version only)
            # CRITICAL: Filter by sw_part_number + app_sw_version to match TestCaseVersion
            for sw_num, version_str in latest_versions.items():
                # Resolve TestCaseVersion to ensure it exists
                version_obj = TestCaseVersion.objects.filter(
                    instance=active_instance,
                    sw_part_number=sw_num,
                    app_sw_version=version_str
                ).first()
                if version_obj:
                    # Filter TestCase by matching sw_part_number + app_sw_version
                    version_q = Q(sw_part_number=sw_num, app_sw_version=version_str)
                    version_filters_list.append(version_q)
            
            # Also include SW part numbers that don't have mappings (show all their test cases)
            sw_without_mappings = set(sw_part_numbers) - sw_with_mappings
            for sw_num in sw_without_mappings:
                if sw_num:
                    version_filters_list.append(Q(sw_part_number=sw_num))
            
            if version_filters_list:
                version_filters = reduce(or_, version_filters_list)
                base_qs = base_qs.filter(version_filters)

    execution_qs = TestExecution.objects.filter(instance=active_instance)
    
    # SECURITY: Filter by version FK with role-based access check
    if selected_sw and selected_version:
        # SECURITY: Use role-based version filtering
        from testmanager.version_service import can_user_access_version
        versions = get_versions_for_user(request.user, active_instance, selected_sw)
        version_obj = versions.filter(app_sw_version=selected_version).first()
        
        # CRITICAL: Block access if user cannot access this version
        if version_obj and can_user_access_version(request.user, version_obj):
            execution_qs = execution_qs.filter(version=version_obj)  # Use explicit FK
        else:
            # User cannot access this version - return empty queryset
            execution_qs = execution_qs.none()
    elif latest_versions:
        # Resolve all active versions using explicit FK
        version_ids = []
        for sw_num, version_str in latest_versions.items():
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_num,
                app_sw_version=version_str,
                is_active=True
            ).first()
            if version_obj:
                version_ids.append(version_obj.id)
        if version_ids:
            execution_qs = execution_qs.filter(version_id__in=version_ids)  # Use explicit FK
    
    test_case_ids = base_qs.values_list('id', flat=True)
    execution_qs = execution_qs.filter(test_case_id__in=test_case_ids)
    
    # FEATURE FILTERING: Filter executions by test_case__feature (feature belongs to TestCase)
    if selected_feature:
        execution_qs = execution_qs.filter(test_case__feature=selected_feature)
    
    # VERSION-SPECIFIC DASHBOARD: Count executions by status for pie chart
    # Status categories: PASS, FAIL, NOT RELEVANT, NOT EXECUTED
    status_map = {"PASS": 0, "FAIL": 0, "NOT RELEVANT": 0, "NOT EXECUTED": 0}
    for exec in execution_qs:
        status_upper = (exec.status or "").strip().upper()
        if status_upper == "PASS":
            status_map["PASS"] += 1
        elif status_upper == "FAIL":
            status_map["FAIL"] += 1
        elif status_upper in ("NOT RELEVANT", "NOT_RELEVANT", "NOT RELEVANT"):
            status_map["NOT RELEVANT"] += 1
        elif status_upper in ("NOT EXECUTED", "NOT_EXECUTED", "NA", ""):
            status_map["NOT EXECUTED"] += 1
        else:
            # Other statuses count as NOT EXECUTED
            status_map["NOT EXECUTED"] += 1
    
    # VERSION-SPECIFIC DASHBOARD: Count test cases without executions for selected version
    # These are test cases that haven't been executed yet for this version
    test_cases_with_executions = execution_qs.values_list('test_case_id', flat=True).distinct()
    test_cases_without_executions = base_qs.exclude(id__in=test_cases_with_executions).count()
    status_map["NOT EXECUTED"] += test_cases_without_executions

    # VERSION-SPECIFIC DASHBOARD: Pie chart data with mandatory status order and colors
    # Status order: PASS, FAIL, NOT RELEVANT, NOT EXECUTED
    # Colors: PASS → Green, FAIL → Red, NOT RELEVANT → Yellow, NOT EXECUTED → Gray
    status_labels = ["PASS", "FAIL", "NOT RELEVANT", "NOT EXECUTED"]
    status_values = []
    for label in status_labels:
        value = status_map.get(label, 0)
        if value is None:
            value = 0
        try:
            status_values.append(int(value))
        except (ValueError, TypeError):
            status_values.append(0)

    # VERSION-SPECIFIC DASHBOARD: Bar chart data must reflect ONLY selected version
    # Bar chart uses the same filtered base_qs which is already filtered by version
    # --- BAR GRAPH LOGIC (VERSION-AWARE) ---

# CASE 1: ALL SHEETS SELECTED (default dashboard)
    if not selected_sheet:
        bar_mode = "total_per_sheet"

        # STRICT: Do NOT use test_case__sheet_name - get sheet names from SheetMeta
        # Group executions by sheet using SheetMeta as source of truth
        sheet_names_from_meta = list(SheetMeta.objects.values_list("sheet_name", flat=True).distinct().order_by("sheet_name"))
        sheet_labels = []
        sheet_values = []
        
        # Count executions per sheet
        # STRICT: Since TestCase no longer has sheet_name, we cannot filter by sheet directly
        # This will be updated once TestCaseVersion/TestCaseSheet models are in place with proper relationships
        # For now, get sheet names from SheetMeta and show empty counts
        # TODO: Update to use TestCase → TestCaseVersion → TestCaseSheet → sheet_name relationship
        if sheet_names_from_meta:
            for sheet_name in sheet_names_from_meta:
                sheet_labels.append(sheet_name)
                # Placeholder: will be updated with proper relationship-based counting via versions__sheets__sheet_name
                sheet_values.append(0)
        
        if not sheet_labels:
            sheet_labels = []
            sheet_values = []


# CASE 2: SHEET SELECTED, NO SW SELECTED
    elif selected_sheet and not selected_sw:
        bar_mode = "total_per_sw"

        # CRITICAL: Filter executions by version FK's sw_part_number, not legacy CharField
        # Group by version FK's sw_part_number
        sw_execution_qs = execution_qs.filter(
            test_case__in=base_qs
        ).exclude(version__sw_part_number__isnull=True).exclude(version__sw_part_number__exact="").values("version__sw_part_number").annotate(count=Count("id")).order_by("version__sw_part_number")
        sheet_labels = [row["version__sw_part_number"] for row in sw_execution_qs if row.get("version__sw_part_number")]
        sheet_values = [row["count"] for row in sw_execution_qs if row.get("version__sw_part_number")]
        
        if not sheet_labels:
            sheet_labels = []
            sheet_values = []


# CASE 3: SHEET + SW SELECTED → STATUS SPLIT
    else:
        bar_mode = "status_overview"

        # STRICT: Do NOT use sheet_name directly - base_qs is already filtered by sheet
        sw_qs = base_qs.filter(
            sw_part_number=selected_sw
        )
        
        # CRITICAL: Filter by version FK's sw_part_number, not legacy CharField
        sw_execution_qs = execution_qs.filter(
            test_case__in=base_qs,
            version__sw_part_number=selected_sw
        )
        
        pass_count = sw_execution_qs.filter(status__iexact="pass").count()
        fail_count = sw_execution_qs.filter(status__iexact="fail").count()
        not_relevant_count = sw_execution_qs.filter(
            Q(status__iexact="not relevant") | Q(status__iexact="not_relevant")
        ).count()
        sw_test_cases_with_executions = sw_execution_qs.values_list('test_case_id', flat=True).distinct()
        not_exec_count = sw_qs.exclude(id__in=sw_test_cases_with_executions).count()

        total_count = pass_count + fail_count + not_relevant_count + not_exec_count

        # VERSION-SPECIFIC DASHBOARD: Bar chart labels and values for selected version
        sheet_labels = [
            "Total Test Cases",
            "Pass",
            "Fail",
            "Not Relevant",
            "Not Executed"
        ]

        sheet_values = [
            total_count,
            pass_count,
            fail_count,
            not_relevant_count,
            not_exec_count
        ]

   # ---------------- SUMMARY COUNTS ----------------
    # Use TestExecution status, not TestCase.status
    passed_count = status_map.get("PASS", 0)
    failed_count = status_map.get("FAIL", 0)
    not_executed_count = status_map.get("NOT EXECUTED", 0)
    total_executed = passed_count + failed_count

    # --- Lists for filter selects --- (filtered by active instance)
    # CRITICAL: Get sheet names from TestCaseSheet, ordered by most recent first
    from ..models import TestCaseSheet
    sheet_names = list(
        TestCaseSheet.objects.filter(version__instance=active_instance)
        .values_list("sheet_name", flat=True)
        .distinct()
        .order_by("-created_at")
    )
    # Fallback to SheetMeta if no TestCaseSheet records exist
    if not sheet_names:
        sheet_names = list(SheetMeta.objects.values_list("sheet_name", flat=True).distinct().order_by("-id"))

    sw_list = []
    if selected_sheet:
        # CRITICAL: Sheet-driven dropdown logic - SW Part Numbers MUST be filtered by selected sheet
        # Query path: TestCaseSheet → version → sw_part_number
        # Show ONLY SW Part Numbers that exist under the selected sheet
        sw_raw = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=selected_sheet
        ).values_list("version__sw_part_number", flat=True).distinct()
        sw_list = sorted(set([str(s).strip() for s in sw_raw if s and str(s).strip()]))
    else:
        # If no sheet selected, show all SW part numbers from active instance
        sw_raw = TestCase.objects.filter(instance=active_instance).values_list("sw_part_number", flat=True)
        sw_list = sorted(set([str(s).strip() for s in sw_raw if s and str(s).strip()]))

    # ROLE-BASED VERSION VISIBILITY: Version list
    # Managers: Show ALL versions (latest, executed, old) - can access history
    # Non-managers: Show ONLY active versions (is_active=True) - single version experience
    version_list = []
    if selected_sheet:
        # PART 1 & 6: Fetch versions based on user role
        if selected_sw:
            if is_manager_check:
                # Manager: Get ALL versions from TestCase in active instance, ordered newest → oldest
                # STRICT: Do NOT use sheet_name - filter via base_qs which respects sheet selection
                # STRICT: app_sw_version does NOT exist on TestCase - use versions__app_sw_version relationship
                # Get versions from TestCaseVersion via relationship
                # TODO: base_qs.filter(sw_part_number=selected_sw).values_list("versions__app_sw_version", flat=True)
                # For now, get versions from SWVersionMapping instead
                testcase_versions = set()
                mapping = SWVersionMapping.objects.filter(
                    instance=active_instance,
                    sw_part_number=selected_sw
                ).order_by('-updated_at').values_list('version', flat=True).distinct()
                testcase_versions = set(mapping)
                version_list = sort_versions(testcase_versions)
            else:
                # Non-manager: Get active version only
                mapping = SWVersionMapping.objects.filter(
                    instance=active_instance,
                    sw_part_number=selected_sw,
                    is_active=True
                ).first()
                if mapping:
                    version_list = [mapping.version]
        else:
            if is_manager_check:
                # Manager: Get ALL versions from TestCase in active instance (all SW part numbers)
                # STRICT: Do NOT use sheet_name - filter via base_qs which respects sheet selection
                # STRICT: app_sw_version does NOT exist on TestCase - use versions__app_sw_version relationship
                # Get versions from TestCaseVersion via relationship
                # TODO: base_qs.values_list("versions__app_sw_version", flat=True).distinct()
                # For now, get versions from SWVersionMapping instead
                testcase_versions = set()
                mappings = SWVersionMapping.objects.filter(
                    instance=active_instance
                ).values_list('version', flat=True).distinct()
                testcase_versions = set(mappings)
                version_list = sort_versions(testcase_versions)
            else:
                # Non-manager: Get active versions for all SW part numbers in this sheet
                # STRICT: Do NOT use sheet_name - filter via base_qs which respects sheet selection
                sw_numbers = base_qs.values_list('sw_part_number', flat=True).distinct()
                
                active_versions = set()
                for sw_num in sw_numbers:
                    if sw_num:
                        mapping = SWVersionMapping.objects.filter(
                            instance=active_instance,
                            sw_part_number=sw_num,
                            is_active=True
                        ).first()
                        if mapping:
                            active_versions.add(mapping.version)
                version_list = sort_versions(active_versions)
    # If no sheet selected, don't show versions (user must select sheet first)

    # --- Get sheet content when sheet is selected --- (active instance only)
    sheet_test_cases = []
    sheet_execution_map = {}
    if selected_sheet:
        # STRICT: Do NOT use sheet_name - use base_qs which is already filtered by sheet
        sheet_qs = base_qs
        if selected_sw:
            sheet_qs = sheet_qs.filter(sw_part_number=selected_sw)
        
        # VERSION-SPECIFIC DASHBOARD: Filter test cases by version
        # If version is selected, show ONLY that version's test cases
        # Otherwise, show latest versions only
        # IMPORTANT: Get distinct SW part numbers BEFORE slicing to avoid ORM error
        sheet_sw_part_numbers_for_exec = None
        if selected_version:
            # STRICT: app_sw_version does NOT exist on TestCase - use versions__app_sw_version relationship
            # Version filtering MUST be done via: TestCase → TestCaseVersion → app_sw_version
            # TODO: sheet_qs.filter(versions__app_sw_version=selected_version)
            # For now, version filtering is disabled to prevent SQL errors
            pass  # Version filtering disabled until TestCaseVersion model exists
            # Get distinct SW part numbers BEFORE slicing (for execution filtering later)
            sheet_sw_part_numbers_for_exec = list(sheet_qs.values_list('sw_part_number', flat=True).distinct())
        else:
            # VERSION-SPECIFIC DASHBOARD: No version selected - filter by latest versions
            sheet_sw_part_numbers = sheet_qs.values_list('sw_part_number', flat=True).distinct()
            if sheet_sw_part_numbers:
                sheet_version_filters_list = []
                
                # Add filters for SW with mappings (latest version only)
                # STRICT: app_sw_version does NOT exist on TestCase - use versions__app_sw_version relationship
                for sw_num, version in latest_versions.items():
                    if sw_num in sheet_sw_part_numbers:
                        # Version filtering via relationship: versions__app_sw_version
                        # TODO: Q(sw_part_number=sw_num) & Q(versions__app_sw_version=version)
                        # For now, filter only by sw_part_number (version filtering disabled)
                        sheet_version_filters_list.append(Q(sw_part_number=sw_num))
                
                # Also include SW part numbers that don't have mappings (show all their test cases)
                sheet_sw_without_mappings = set(sheet_sw_part_numbers) - sw_with_mappings
                for sw_num in sheet_sw_without_mappings:
                    if sw_num:
                        sheet_version_filters_list.append(Q(sw_part_number=sw_num))
                
                if sheet_version_filters_list:
                    sheet_version_filters = reduce(or_, sheet_version_filters_list)
                    sheet_qs = sheet_qs.filter(sheet_version_filters)
            
            # Get distinct SW part numbers BEFORE slicing (for execution filtering later)
            sheet_sw_part_numbers_for_exec = list(sheet_qs.values_list('sw_part_number', flat=True).distinct())
        
        # Slice AFTER getting distinct values
        # STRICT: sl_no does NOT exist - removed after hierarchy refactor
        # Order by id (primary key) instead of deprecated sl_no field
        # CRITICAL: Explicitly order by id to override model's default ordering (which includes sheet_name/sl_no)
        sheet_qs = sheet_qs.order_by('id')[:10]  # Show first 10 test cases - ordered by id to avoid deprecated fields
        
        sheet_test_cases = list(sheet_qs)
        # Sort by version (newest first), then by id
        sheet_test_cases = sort_test_cases_by_version(sheet_test_cases)
        
        # VERSION-SPECIFIC DASHBOARD: Get executions for these test cases - filter STRICTLY by version and instance
        sheet_test_case_ids = [tc.id for tc in sheet_test_cases]
        if sheet_test_case_ids:
            executions = TestExecution.objects.filter(
                test_case_id__in=sheet_test_case_ids,
                instance=active_instance
            ).select_related("test_case")
            
            # Apply filters
            if selected_sw:
                executions = executions.filter(sw_part_number=selected_sw)
            
            # CRITICAL: Filter by version FK, not legacy app_sw_version CharField
            if selected_sw and selected_version:
                # Resolve version using explicit FK
                version_obj = TestCaseVersion.objects.filter(
                    instance=active_instance,
                    sw_part_number=selected_sw,
                    app_sw_version=selected_version
                ).first()
                if version_obj:
                    executions = executions.filter(version=version_obj)  # Use explicit FK
            elif latest_versions and sheet_sw_part_numbers_for_exec:
                # Resolve all active versions using explicit FK
                version_ids = []
                for sw_num, version_str in latest_versions.items():
                    if sw_num in sheet_sw_part_numbers_for_exec:
                        version_obj = TestCaseVersion.objects.filter(
                            instance=active_instance,
                            sw_part_number=sw_num,
                            app_sw_version=version_str,
                            is_active=True
                        ).first()
                        if version_obj:
                            version_ids.append(version_obj.id)
                if version_ids:
                    executions = executions.filter(version_id__in=version_ids)  # Use explicit FK
            
            # CRITICAL: Also filter by version FK's sw_part_number if selected_sw
            if selected_sw:
                executions = executions.filter(version__sw_part_number=selected_sw)
            
            # FEATURE FILTERING: Filter executions by test_case__feature (feature belongs to TestCase)
            if selected_feature:
                executions = executions.filter(test_case__feature=selected_feature)
            
            # CRITICAL: Build execution map using version FK, not legacy fields
            # Match executions to test cases by exact (test_case.id, version_id)
            sheet_execution_map = {}
            for e in executions:
                # CRITICAL: Verify execution belongs to active instance
                if e.instance != active_instance:
                    continue  # Skip executions from other instances
                
                tc_id = e.test_case_id
                version_id = e.version_id if e.version else None
                # CRITICAL: Use version FK ID in key to ensure exact match
                key = (tc_id, version_id)
                sheet_execution_map[key] = e
            
            # CRITICAL: Attach executions to test cases using version FK
            # Match executions to test cases by exact (test_case.id, version_id)
            for tc in sheet_test_cases:
                # CRITICAL: Verify test case belongs to active instance
                if tc.instance != active_instance:
                    tc.exec = None  # No execution for test cases from other instances
                    continue
                
                tc_id = tc.id
                # Resolve version for this test case
                version_obj = None
                if selected_sw and selected_version:
                    version_obj = TestCaseVersion.objects.filter(
                        instance=active_instance,
                        sw_part_number=selected_sw,
                        app_sw_version=selected_version
                    ).first()
                elif selected_sw and latest_versions:
                    version_str = latest_versions.get(selected_sw)
                    if version_str:
                        version_obj = TestCaseVersion.objects.filter(
                            instance=active_instance,
                            sw_part_number=selected_sw,
                            app_sw_version=version_str,
                            is_active=True
                        ).first()
                
                if version_obj:
                    key = (tc_id, version_obj.id)
                    if key in sheet_execution_map:
                        tc.exec = sheet_execution_map[key]
                    else:
                        tc.exec = None
                else:
                    tc.exec = None
                # Always display base_test_case_id (version suffix never shown in UI)
                tc.display_test_case_id = tc.base_test_case_id or tc.test_case_id

    # Check if user is manager/developer (for role-based permissions)
    user = request.user
    is_manager_check = is_manager(user)
    is_developer_check = is_developer(user)
    can_edit_project_overview = is_manager_check or is_developer_check or user.is_superuser
    
    # CRITICAL: Fetch ProjectOverview based on version FK ONLY
    # For non-managers, use the resolved active_version
    # Do NOT use legacy fields (software_part_number, application_sw_version)
    project_overview = None
    project_overview_locked = False
    
    # Resolve version for ProjectOverview
    version_obj_for_overview = None
    if not is_manager_check and active_version:
        # Non-manager: MUST use only active version
        version_obj_for_overview = active_version
    elif selected_sw and selected_version:
        # Manager: Can select any version
        version_obj_for_overview = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=selected_sw,
            app_sw_version=selected_version
        ).first()
    
    if version_obj_for_overview:
        # Get ProjectOverview linked to this version via FK
        project_overview = ProjectOverview.objects.filter(version=version_obj_for_overview).first()
        project_overview_locked = version_obj_for_overview.is_locked
    
    # Get URLs for Project Overview endpoints
    project_overview_urls = {
        "get_url": reverse("get_project_overview"),
        "save_url": reverse("save_project_overview"),
        "update_url": reverse("update_project_overview"),
    }
    
    # --- Role-based permissions for UI ---
    # CRITICAL: These determine what buttons/features are visible to the user
    can_view_history_check = can_view_history(user)  # Manager only
    can_export_reports_check = can_export_reports(user)  # Manager only
    can_create_instance_check = can_create_instance(user)  # Tester and above
    
    # FEATURE LIST: Get distinct features via TestExecution → TestCase → Feature
    # STRICT: Feature source is TestCase.feature ONLY
    # Feature list must be derived via TestExecution → TestCase → Feature
    # Scope feature list by selected TestCaseVersion (FK) and TestCaseSheet (FK)
    feature_execution_qs = TestExecution.objects.filter(instance=active_instance)
    
    # Filter by selected version (FK)
    if selected_version and selected_sw:
        # Resolve TestCaseVersion object
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=selected_sw,
            app_sw_version=selected_version
        ).first()
        if version_obj:
            feature_execution_qs = feature_execution_qs.filter(version=version_obj)
    elif latest_versions and not selected_version:
        # Filter to latest versions only (if no version selected)
        version_ids = []
        for sw_num, version_str in latest_versions.items():
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_num,
                app_sw_version=version_str,
                is_active=True
            ).first()
            if version_obj:
                version_ids.append(version_obj.id)
        if version_ids:
            feature_execution_qs = feature_execution_qs.filter(version_id__in=version_ids)
    
    # Filter by selected sheet (FK)
    if selected_sheet:
        # Resolve TestCaseSheet objects with this sheet_name
        from ..models import TestCaseSheet
        sheets = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=selected_sheet
        ).select_related('version')
        
        # If version is selected, further filter sheets by that version
        if selected_version and selected_sw:
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=selected_sw,
                app_sw_version=selected_version
            ).first()
            if version_obj:
                sheets = sheets.filter(version=version_obj)
        elif latest_versions and not selected_version:
            # Filter sheets by latest versions
            version_ids = []
            for sw_num, version_str in latest_versions.items():
                version_obj = TestCaseVersion.objects.filter(
                    instance=active_instance,
                    sw_part_number=sw_num,
                    app_sw_version=version_str,
                    is_active=True
                ).first()
                if version_obj:
                    version_ids.append(version_obj.id)
            if version_ids:
                sheets = sheets.filter(version_id__in=version_ids)
        
        # Filter executions by sheet FK
        sheet_ids = list(sheets.values_list('id', flat=True))
        if sheet_ids:
            feature_execution_qs = feature_execution_qs.filter(sheet_id__in=sheet_ids)
    
    # Get distinct features from TestCase via TestExecution
    # Feature source: TestCase.feature ONLY
    feature_list = sorted(set(
        feature_execution_qs
        .exclude(test_case__feature__isnull=True)
        .exclude(test_case__feature__exact="")
        .values_list('test_case__feature', flat=True)
        .distinct()
    ))
    
    # --- Update context and render ---
    context.update({
        "status_labels": status_labels,
        "status_values": status_values,
        "sheet_labels": sheet_labels,
        "sheet_values": sheet_values,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_executed_count": not_executed_count,
        "total_executed": total_executed,
        "sheets": sheet_names,
        "sw_list": sw_list,
        "version_list": version_list,
        "feature_list": feature_list,  # NEW: Feature list for dropdown
        "selected_sheet": selected_sheet,
        "selected_sw": selected_sw,
        "selected_version": selected_version,
        "selected_feature": selected_feature,  # NEW: Selected feature
        "bar_mode": bar_mode,
        "sheet_test_cases": sheet_test_cases,
        "is_manager": is_manager_check,  # For role-based version visibility in template
        "latest_versions": latest_versions,  # SW Part Number → Version mapping for non-managers
        "project_overview": project_overview,  # ProjectOverview for selected version/SW
        "project_overview_locked": project_overview_locked,  # Whether version is locked
        "can_edit_project_overview": can_edit_project_overview,  # Permission to edit
        "project_overview_urls": project_overview_urls,  # URLs for Project Overview endpoints
        # Role-based UI permissions
        "can_view_history": can_view_history_check,  # Manager only - history page access
        "can_export_reports": can_export_reports_check,  # Manager only - export HTML/Excel
        "can_create_instance": can_create_instance_check,  # Tester and above - create new test instance
    })

    return render(request, "testmanager/home.html", context)


# -------------------------------------------------------------------------------------
# VERSION-SPECIFIC PROJECT OVERVIEW ENDPOINTS
# -------------------------------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def get_project_overview(request):
    """
    Fetch version-specific Project Overview with all structured fields.
    
    CRITICAL: Fetches ProjectOverview using selected Version and SW Part Number.
    Do NOT use global or latest() logic.
    
    Inputs: sw_part_number, version (from GET parameters)
    Returns: All project overview fields as JSON, plus lock status
    """
    try:
        sw_part_number = request.GET.get("sw_part_number", "").strip()
        app_sw_version = request.GET.get("version", "").strip()
        
        empty_response = {
            "ok": True,
            "project_code": "",
            "vcu_platform": "",
            "hardware_part_number": "",
            "software_part_number": sw_part_number or "",
            "project_stage": "",
            "developer": "",
            "test_engineer": "",
            "application_sw_version": app_sw_version or "",
            "bootloader_sw_version": "",
            "checksum_value": "",
            "dbc_test_it": "",
            "is_locked": False,
        }
        
        if not sw_part_number or not app_sw_version:
            return JsonResponse(empty_response)
        
        active_instance = get_active_instance()
        if not active_instance:
            return JsonResponse(empty_response)
        
        # CRITICAL: Fetch ProjectOverview using TestCaseVersion (version-aware)
        # Try to get TestCaseVersion first
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version
        ).first()
        
        project_overview = None
        is_locked = False
        
        if version_obj:
            # Get ProjectOverview linked to this version
            project_overview = ProjectOverview.objects.filter(version=version_obj).first()
            is_locked = version_obj.is_locked
        else:
            # Fallback: try to get by instance, sw_part_number, and app_sw_version
            project_overview = ProjectOverview.objects.filter(
                instance=active_instance,
                software_part_number=sw_part_number,
                application_sw_version=app_sw_version
            ).first()
        
        if project_overview:
            return JsonResponse({
                "ok": True,
                "project_code": project_overview.project_code or "",
                "vcu_platform": project_overview.vcu_platform or "",
                "hardware_part_number": project_overview.hardware_part_number or "",
                "software_part_number": project_overview.software_part_number or project_overview.sw_part_number or "",
                "project_stage": project_overview.project_stage or "",
                "developer": project_overview.developer or "",
                "test_engineer": project_overview.test_engineer or "",
                "application_sw_version": project_overview.application_sw_version or project_overview.app_sw_version or "",
                "bootloader_sw_version": project_overview.bootloader_sw_version or "",
                "checksum_value": project_overview.checksum_value or "",
                "dbc_test_it": project_overview.dbc_test_it or "",
                "is_locked": project_overview.is_locked or is_locked,
            })
        else:
            return JsonResponse(empty_response)
        
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": f"Error fetching Project Overview: {str(e)}"
        }, status=500)


@login_required
@require_http_methods(["POST"])
def save_project_overview(request):
    """
    Save or update version-specific Project Overview with all structured fields.
    
    PERMISSIONS: Only Manager and Developer can edit.
    Locked versions cannot be edited (even by Manager/Developer).
    
    CRITICAL: Links ProjectOverview to TestCaseVersion explicitly.
    """
    # Check permissions: Only Manager and Developer can edit
    is_manager_check = is_manager(request.user)
    is_developer_check = is_developer(request.user)
    if not (is_manager_check or is_developer_check or request.user.is_superuser):
        return JsonResponse({
            "ok": False,
            "error": "Permission denied. Only Manager and Developer can edit Project Overview."
        }, status=403)
    
    try:
        data = json.loads(request.body)
        
        sw_part_number = data.get("sw_part_number", "").strip()
        app_sw_version = data.get("version", "").strip()
        
        if not sw_part_number or not app_sw_version:
            return JsonResponse({
                "ok": False,
                "error": "SW Part Number and Version are required."
            }, status=400)
        
        active_instance = get_active_instance()
        if not active_instance:
            return JsonResponse({
                "ok": False,
                "error": "No active test instance found."
            }, status=400)
        
        # CRITICAL: Get or create TestCaseVersion first
        version_obj, _ = TestCaseVersion.objects.get_or_create(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version,
            defaults={"is_active": True, "is_locked": False}
        )
        
        # CRITICAL: Check if version is locked - locked versions cannot be edited
        if version_obj.is_locked:
            return JsonResponse({
                "ok": False,
                "error": "This version is locked and cannot be modified."
            }, status=403)
        
        # Save or update version-specific project overview linked to TestCaseVersion
        project_overview, created = ProjectOverview.objects.update_or_create(
            version=version_obj,  # CRITICAL: Link to TestCaseVersion
            defaults={
                "instance": active_instance,
                "software_part_number": sw_part_number,
                "application_sw_version": app_sw_version,
                "sw_part_number": sw_part_number,  # Legacy field
                "app_sw_version": app_sw_version,  # Legacy field
                "project_code": data.get("project_code", "").strip(),
                "vcu_platform": data.get("vcu_platform", "").strip(),
                "hardware_part_number": data.get("hardware_part_number", "").strip(),
                "project_stage": data.get("project_stage", "").strip(),
                "developer": data.get("developer", "").strip(),
                "test_engineer": data.get("test_engineer", "").strip(),
                "bootloader_sw_version": data.get("bootloader_sw_version", "").strip(),
                "checksum_value": data.get("checksum_value", "").strip(),
                "dbc_test_it": data.get("dbc_test_it", "").strip(),
            }
        )
        
        # Set created_by only if this is a new record
        if created:
            project_overview.created_by = request.user
            project_overview.save()
        
        # Log the action
        ActivityLog.objects.create(
            user=request.user,
            action=ACTIVITY_ACTION_EDIT,
            reference=f"Project Overview: {sw_part_number} → {app_sw_version}",
            remarks=f"{'Created' if created else 'Updated'} version-specific project overview",
            content_type="ProjectOverview"
        )
        
        return JsonResponse({
            "ok": True,
            "message": "Project Overview saved successfully."
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            "ok": False,
            "error": "Invalid request data."
        }, status=400)
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": f"Error saving Project Overview: {str(e)}"
        }, status=500)

