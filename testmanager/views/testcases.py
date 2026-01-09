"""
Test Case Management Views

This module contains views for listing, creating, viewing, and executing test cases.
"""

from functools import reduce
from operator import or_
from urllib.parse import urlencode, parse_qs, urlparse

from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Count, Max, Case, When, IntegerField, Value
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.contrib.auth.decorators import permission_required, login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.urls import reverse
from django.utils.safestring import mark_safe

from ..models import (
    ActivityLog, TestExecution, TestCase, SheetMeta, SWVersionMapping, TestCaseVersion
)
from ..services import get_active_instance, get_current_active_version
from ..utils import clean, clean_slno, get_requirement_id
from ..constants import (
    ACTIVITY_ACTION_ADD,
    ACTIVITY_ACTION_EDIT,
)
from ..decorators import (
    is_manager, is_developer, is_manager_or_developer, manager_required,
    can_view_history, can_export_reports, can_create_instance
)
from ..version_service import (
    sort_versions,
    sort_test_cases_by_version,
    get_base_test_case_id,
    is_version_current,
    get_execution_for_version,
    can_edit_execution_version,
)
from ..logging_utils import log_debug, log_error


@login_required
def testcase_list(request):
    log_debug("views.py:testcase_list", "Function entry")
    selected_sheet = (request.GET.get("sheet") or "").strip()
    selected_sw = request.GET.get("sw", "")
    selected_version = request.GET.get("version", "").strip()
    selected_feature = request.GET.get("feature", "")
    search_query = request.GET.get("q", "")
    page_number = request.GET.get("page")

    executions = TestCase.objects.none()
    execution_map = {}

    # Get active instance - all queries must filter by active instance only
    active_instance = get_active_instance()
    
    # Check if user is Manager or Developer (needed for role-based filtering)
    is_manager_check = is_manager(request.user)
    is_developer_check = is_developer(request.user)
    if request.user.is_superuser:
        is_manager_check = True
        is_developer_check = True  # Superusers have all permissions
    
    # CRITICAL: For non-managers, resolve active version ONCE and use for ALL data
    active_version = None
    if not is_manager_check:
        # Non-manager: MUST use only the most recent active version
        # Ignore version parameter - always use active version
        selected_version = ""
        
        # SECURITY: Use role-based version filtering
        from ..version_service import get_active_version_for_user, get_versions_for_user
        if selected_sw:
            active_version = get_active_version_for_user(request.user, active_instance, selected_sw)
        else:
            # If no SW selected, get the most recent active version across all SW
            # For non-managers, this returns the ONLY version they can access
            versions = get_versions_for_user(request.user, active_instance)
            active_version = versions.filter(is_active=True).first()
        
        # Set selected_version and selected_sw from active_version
        if active_version:
            selected_version = active_version.app_sw_version
            if not selected_sw:
                selected_sw = active_version.sw_part_number
    
    qs = TestCase.objects.filter(instance=active_instance)

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
            qs = qs.filter(version_filters)
    if selected_sw:
        qs = qs.filter(sw_part_number=selected_sw)
    
    # ROLE-BASED VERSION FILTERING: Filter test cases by selected version
    # Managers: Can see all versions (latest, executed, old) and can select specific versions
    # Non-managers: Can see ONLY the latest active version (is_active=True) - version is auto-resolved
    if selected_version and selected_sw and is_manager_check:
        # CRITICAL: Filter by sw_part_number + app_sw_version to match TestCaseVersion
        # Resolve TestCaseVersion first to ensure it exists
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=selected_sw,
            app_sw_version=selected_version
        ).first()
        if version_obj:
            qs = qs.filter(
                sw_part_number=selected_sw,
                app_sw_version=selected_version
            )
    else:
        if is_manager_check:
            # Manager: Show all versions (no filtering by active status)
            # Filter by latest versions only - show only the most recent version for each SW Part Number
            sw_part_numbers = qs.values_list('sw_part_number', flat=True).distinct()
            latest_versions = {}
            sw_with_mappings = set()
            for sw_num in sw_part_numbers:
                if sw_num:
                    mapping = SWVersionMapping.objects.filter(
                        instance=active_instance,
                        sw_part_number=sw_num
                    ).order_by('-updated_at').first()
                    if mapping:
                        latest_versions[sw_num] = mapping.version
                        sw_with_mappings.add(sw_num)
            
            if sw_part_numbers:
                version_filters_list = []
                # CRITICAL: Filter by sw_part_number + app_sw_version to match TestCaseVersion
                for sw_num, version_str in latest_versions.items():
                    # Resolve TestCaseVersion to ensure it exists
                    version_obj = TestCaseVersion.objects.filter(
                        instance=active_instance,
                        sw_part_number=sw_num,
                        app_sw_version=version_str
                    ).first()
                    if version_obj:
                        version_q = Q(sw_part_number=sw_num, app_sw_version=version_str)
                        version_filters_list.append(version_q)
                
                sw_without_mappings = set(sw_part_numbers) - sw_with_mappings
                for sw_num in sw_without_mappings:
                    if sw_num:
                        version_filters_list.append(Q(sw_part_number=sw_num))
                
                if version_filters_list:
                    version_filters = reduce(or_, version_filters_list)
                    qs = qs.filter(version_filters)
        else:
            # Non-manager: ALWAYS filter to active versions only (is_active=True)
            # SECURITY: Use explicit filtering via TestCaseVersion FK relationships
            from ..version_service import filter_queryset_by_latest_versions
            sw_part_numbers = list(qs.values_list('sw_part_number', flat=True).distinct())
            sw_part_numbers = [sw for sw in sw_part_numbers if sw]
            if sw_part_numbers:
                qs = filter_queryset_by_latest_versions(qs, sw_part_numbers, include_empty_versions=True, active_instance=active_instance)
            else:
                # If no SW part numbers, return empty queryset for non-managers
                qs = qs.none()
    
    if selected_feature:
        qs = qs.filter(feature=selected_feature)

    if search_query:
        search_filters = [
            # STRICT: sl_no does NOT exist on TestCase - removed from search
            Q(sw_part_number__icontains=search_query),
            Q(feature__icontains=search_query),
            Q(requirement_id__icontains=search_query),
            Q(requirement_description__icontains=search_query),
            Q(base_test_case_id__icontains=search_query),  # Search base_test_case_id (version suffix never shown)
            Q(test_case_summary__icontains=search_query),
            Q(pre_conditions__icontains=search_query),
            Q(inputs__icontains=search_query),
            Q(periodic_time__icontains=search_query),
            Q(test_steps__icontains=search_query),
            Q(expected_result__icontains=search_query),
            Q(status__icontains=search_query),
            Q(reports__icontains=search_query),
            Q(comments__icontains=search_query)
        ]
        qs = qs.filter(reduce(or_, search_filters))

    # CRITICAL: Version dropdown MUST depend on selected Sheet + SW Part Number
    # ROLE-BASED VERSION VISIBILITY: Version list
    # Managers: Show ALL versions (active + inactive) - can access history
    # Developers/Testers: Show ONLY active version
    version_list = []
    active_version_for_sw = {}  # For non-managers to show read-only version text
    version_list_with_status = []  # List of dicts with version and is_active status

    # CRITICAL: Non-managers must NOT see version dropdown
    # They always work with the active version (already resolved above)
    if not is_manager_check:
        # Non-manager: Set version_list to empty (no dropdown)
        # active_version_for_sw will be populated below to show read-only version text
        if active_version:
            active_version_for_sw[active_version.sw_part_number] = active_version.app_sw_version
    elif selected_sheet and selected_sw:
        # CRITICAL: Version dropdown MUST depend on selected Sheet + SW Part Number
        # Query pattern: TestCaseVersion.objects.filter(sheets__sheet_name=selected_sheet, sw_part_number=selected_sw)
        from ..version_service import get_versions_for_user
        from ..models import TestCaseSheet
        
        # Get versions that have the selected sheet
        versions_with_sheet = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=selected_sw,
            sheets__sheet_name=selected_sheet
        ).distinct()
        
        # BUG FIX: Only show active versions in dropdowns, even for managers
        # Old versions NEVER shown in dropdowns (requirement #11)
        # Live UI (/home, /testcases/) → ACTIVE INSTANCE ONLY, ACTIVE VERSIONS ONLY
        versions = versions_with_sheet.filter(is_active=True).order_by('-created_at')  # BUG FIX: Only active versions
        version_list = sort_versions([v.app_sw_version for v in versions])
        if versions.exists():
            first_version = versions.first()
            if first_version:
                active_version_for_sw[selected_sw] = first_version.app_sw_version
    elif selected_sheet:
        # Sheet selected but no SW selected - show versions for all SW in that sheet
        from ..version_service import get_versions_for_user
        from ..models import TestCaseSheet
        
        versions_with_sheet = TestCaseVersion.objects.filter(
            instance=active_instance,
            sheets__sheet_name=selected_sheet
        ).distinct()
        
        # BUG FIX: Only show active versions in dropdowns, even for managers
        # Old versions NEVER shown in dropdowns (requirement #11)
        versions = versions_with_sheet.filter(is_active=True).order_by('-created_at')  # BUG FIX: Only active versions
        version_list = sort_versions([v.app_sw_version for v in versions])
        for v in versions:
            active_version_for_sw[v.sw_part_number] = v.app_sw_version
    # If no sheet selected, don't show versions (user must select sheet first)

    highlight_id = request.GET.get("highlight") or request.session.pop("highlight_id", "")
    
    # Check if export was completed (from session)
    # Check for export_completed flag - can be from session (Excel) or URL parameter (both Excel and HTML)
    # Use pop to clear session flag, but keep URL parameter check
    export_completed = request.session.pop('export_completed', False) or request.GET.get('export_completed') is not None
    
    # FIXED: Get sheet names ONLY from SheetMeta (source of truth)
    # Sheet dropdown must list unique sheet names, not dependent on SW part numbers
    sheet_names = list(
        SheetMeta.objects.all().order_by("sheet_name").values_list("sheet_name", flat=True)
    )

    # CRITICAL: Sheet-driven dropdown logic - SW Part Numbers MUST be filtered by selected sheet
    sw_list = []
    if selected_sheet:
        # STRICT: Query path: TestCaseSheet → version → sw_part_number
        # Show ONLY SW Part Numbers that exist under the selected sheet
        sw_raw = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=selected_sheet
        ).values_list("version__sw_part_number", flat=True).distinct()
        sw_list = sorted(set(s for s in sw_raw if s and str(s).strip()))
    else:
        # If no sheet selected, show all SW part numbers from active instance
        sw_raw = TestCase.objects.filter(instance=active_instance).values_list("sw_part_number", flat=True)
        sw_list = sorted(set(s for s in sw_raw if s and str(s).strip()))

    # FEATURE LIST: Get distinct features from TestCase (feature belongs to TestCase)
    # Filter by selected sheet, SW, and version if provided
    feature_qs = TestCase.objects.filter(instance=active_instance)
    
    if selected_sheet:
        # Filter via TestCaseSheet relationship
        from ..models import TestCaseSheet
        sheets = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=selected_sheet
        ).select_related('version')
        version_filters_list = []
        for sheet in sheets:
            version_filters_list.append(Q(
                sw_part_number=sheet.version.sw_part_number,
                app_sw_version=sheet.version.app_sw_version
            ))
        if version_filters_list:
            version_filters = reduce(or_, version_filters_list)
            feature_qs = feature_qs.filter(version_filters)
    
    if selected_sw:
        feature_qs = feature_qs.filter(sw_part_number=selected_sw)
    
    if selected_version and selected_sw:
        feature_qs = feature_qs.filter(
            sw_part_number=selected_sw,
            app_sw_version=selected_version
        )
    elif not is_manager_check and active_version:
        # Non-manager: Filter to active version only
        feature_qs = feature_qs.filter(
            sw_part_number=active_version.sw_part_number,
            app_sw_version=active_version.app_sw_version
        )
    
    feature_list = sorted(set(
        feature_qs.exclude(feature__isnull=True)
        .exclude(feature__exact="")
        .values_list("feature", flat=True)
        .distinct()
    ))
    
    # CRITICAL: Query executions using version FK, not legacy app_sw_version CharField
    test_case_ids = list(qs.values_list('id', flat=True))

    # Get all executions for these test cases
    executions = TestExecution.objects.filter(
        test_case__in=test_case_ids, 
        instance=active_instance
    ).select_related("test_case", "version")
    
    # FEATURE FILTERING: Filter executions by test_case__feature (feature belongs to TestCase)
    if selected_feature:
        executions = executions.filter(test_case__feature=selected_feature)
    
    # CRITICAL: Filter by version FK
    # For non-managers, MUST use only active version
    version_obj = None
    if not is_manager_check and active_version:
        # Non-manager: MUST use only active version
        version_obj = active_version
        executions = executions.filter(version=active_version)  # Use explicit FK
    elif selected_sw and selected_version:
        # SECURITY: Use role-based version access check
        from ..version_service import get_versions_for_user, can_user_access_version
        versions = get_versions_for_user(request.user, active_instance, selected_sw)
        version_obj = versions.filter(app_sw_version=selected_version).first()
        
        # CRITICAL: Block access if user cannot access this version
        if version_obj and can_user_access_version(request.user, version_obj):
            executions = executions.filter(version=version_obj)  # Use explicit FK
        else:
            # User cannot access this version - return empty queryset
            executions = executions.none()
    
    # CRITICAL: Build execution map using version FK, not legacy fields
    # Match executions to test cases by exact (test_case.id, version_id)
    execution_map = {}
    for e in executions:
        # CRITICAL: Verify execution belongs to active instance
        if e.instance != active_instance:
            continue  # Skip executions from other instances
        
        tc_id = e.test_case.id
        version_id = e.version_id if e.version else None
        
        # CRITICAL: Use version FK ID in key to ensure exact match
        key = (tc_id, version_id)
        
        # Store execution in map - we'll match it to test cases below
        if key not in execution_map:
            execution_map[key] = e
    
    # Now create a lookup map by test_case.id for easy access
    # This will be used when attaching executions to test cases
    # CRITICAL: execution_map keys are (tc_id, version_id) tuples, not 4-value tuples
    execution_map_by_tc_id = {}
    for (tc_id, version_id), exec_obj in execution_map.items():
        # Store by test_case.id for easy lookup
        # Note: Multiple executions per test_case are possible (different versions)
        # We'll match by version_id when attaching to test cases
        if tc_id not in execution_map_by_tc_id:
            execution_map_by_tc_id[tc_id] = {}
        execution_map_by_tc_id[tc_id][version_id] = exec_obj

    # CRITICAL: Order by sw_part_number then sl_no (sl_no is scoped per sw_part_number)
    # Convert sl_no to integer for proper numeric ordering
    from django.db.models import Case, When, IntegerField, Value
    from django.db.models.functions import Cast
    qs = qs.annotate(
        sl_no_int=Case(
            When(sl_no__isnull=True, then=Value(0)),
            When(sl_no__exact="", then=Value(0)),
            default=Cast('sl_no', IntegerField()),
            output_field=IntegerField()
        )
    ).order_by('sw_part_number', 'sl_no_int')
    
    # Convert to list and sort by version (newest first), then by id
    # STRICT: sheet_name and sl_no do NOT exist on TestCase - removed from sorting
    test_cases_list = list(qs)
    test_cases_list = sort_test_cases_by_version(test_cases_list)
    
    # Re-sort to maintain version grouping (sheet_name grouping removed - sheet_name doesn't exist)
    # The sort_test_cases_by_version already handles version sorting
    # Now paginate the sorted list
    paginator = Paginator(test_cases_list, 20)
    page_obj = paginator.get_page(page_number)
    
    # CRITICAL: Set execution data using version FK, not legacy fields
    # SECURITY: Use role-based version access check
    version_obj = None
    if selected_sw and selected_version:
        from ..version_service import get_versions_for_user, can_user_access_version
        versions = get_versions_for_user(request.user, active_instance, selected_sw)
        version_obj = versions.filter(app_sw_version=selected_version).first()
        
        # CRITICAL: Block access if user cannot access this version
        if version_obj and not can_user_access_version(request.user, version_obj):
            version_obj = None  # User cannot access this version
    
    for tc in page_obj:
        # CRITICAL: Verify test case belongs to active instance
        if tc.instance != active_instance:
            tc.exec = None  # No execution for test cases from other instances
            continue
        
        # CRITICAL: Match execution using version FK
        tc_id = tc.id
        exec_obj = None
        
        if version_obj:
            # Use version FK ID to match
            key = (tc_id, version_obj.id)
            if key in execution_map:
                exec_obj = execution_map[key]
                # CRITICAL: Double-check instance matches (extra safety)
                if exec_obj.instance != active_instance:
                    exec_obj = None
        
        tc.exec = exec_obj
        # Ensure manager_approved attribute exists for template
        if exec_obj:
            # manager_approved is already a field on TestExecution model, so it should be available
            pass
        # Always display base_test_case_id (version suffix never shown in UI)
        tc.display_test_case_id = tc.base_test_case_id or tc.test_case_id
        # Auto-derive requirement_id from base_test_case_id if requirement_id is empty
        tc.display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)

    # Role-based UI permissions
    can_view_history_check = can_view_history(request.user)  # Manager only
    can_export_reports_check = can_export_reports(request.user)  # Manager only
    can_create_instance_check = can_create_instance(request.user)  # Tester and above
    
    return render(request, "testmanager/list.html", {
        "tests": page_obj,
        "page_obj": page_obj,
        "sheets": sheet_names,
        "sw_list": sw_list,
        "feature_list": feature_list,
        "selected_sheet": selected_sheet,
        "selected_sw": selected_sw,
        "version_list": version_list,
        "version_list_with_status": version_list_with_status,  # For Managers: includes is_active status
        "selected_version": selected_version,
        "selected_feature": selected_feature,
        "search_query": search_query,
        "highlight_id": highlight_id,
        "export_completed": export_completed,
        "is_manager": is_manager_check,
        "is_developer": is_developer_check,
        "active_version_for_sw": active_version_for_sw,  # For non-managers to show read-only version
        # Role-based UI permissions
        "can_view_history": can_view_history_check,  # Manager only - history page access
        "can_export_reports": can_export_reports_check,  # Manager only - export HTML/Excel
        "can_create_instance": can_create_instance_check,  # Tester and above - create new test instance
    })


# -------------------------------------------------------------------------------------
# CREATE TEST CASE
# -------------------------------------------------------------------------------------
@permission_required("testmanager.create_testcases", raise_exception=True)
@login_required
def create_testcases(request):
    sw_part_number = request.GET.get("sw", "").strip()
    selected_version = request.GET.get("version", "").strip()
    
    # Get all unique SW part numbers
    sw_list = sorted(set([
        str(sw).strip() for sw in 
        TestCase.objects.exclude(sw_part_number__isnull=True)
                       .exclude(sw_part_number__exact="")
                       .values_list("sw_part_number", flat=True)
                       .distinct()
        if sw and str(sw).strip()
    ]))
    
    return render(request, "testmanager/create_testcases.html", {
        "sw_part_number": sw_part_number,
        "selected_version": selected_version,
        "sw_list": sw_list,
    })

# -------------------------------------------------------------------------------------
# ADD TEST CASE
# -------------------------------------------------------------------------------------
@login_required
def testcase_add(request):
    # Check if user is Manager or Developer
    is_manager_check = is_manager_or_developer(request.user)
    
    # Allow access if user is manager, developer, or has the permission
    if not (is_manager_check or request.user.has_perm("testmanager.add_testcase")):
        raise PermissionDenied("You don't have permission to add test cases. Manager or Developer access required.")
    
    active_instance = get_active_instance()
    
    # Get available data for dropdowns (no external dependency)
    # Get all available sheets from SheetMeta (for dropdown suggestions)
    from ..models import SheetMeta
    # FIXED: Get sheet names ONLY from SheetMeta (source of truth)
    available_sheets = list(SheetMeta.objects.all().order_by("sheet_name").values_list("sheet_name", flat=True))
    
    # Get all SW part numbers from TestCase (active instance) - for dropdown suggestions
    sw_list = sorted(set([
        str(sw).strip() for sw in 
        TestCase.objects.filter(instance=active_instance)
                       .exclude(sw_part_number__isnull=True)
                       .exclude(sw_part_number__exact="")
                       .values_list("sw_part_number", flat=True)
                       .distinct()
        if sw and str(sw).strip()
    ]))
    
    # Get versions from TestCaseVersion (for dropdown suggestions)
    # Include both active and inactive versions - user can select any
    active_versions = list(
        TestCaseVersion.objects.filter(
            instance=active_instance,
            is_locked=False  # Only exclude locked versions
        ).values_list("app_sw_version", flat=True).distinct().order_by("-created_at")
    )
    
    # If no versions from TestCaseVersion, fallback to SWVersionMapping
    if not active_versions:
        from ..models import SWVersionMapping
        active_versions = list(
            SWVersionMapping.objects.filter(
                instance=active_instance
            ).values_list("version", flat=True).distinct().order_by("-updated_at")
        )
    
    # REMOVED: No blocking checks - form is self-contained
    # User can enter sheet/version even if not in dropdowns
    
    # Calculate next SL.NO - will be updated when sw_part_number is selected
    # sl_no is scoped per sw_part_number
    next_sl_no = "1"  # Default, will be updated via AJAX when SW Part Number is selected

    if request.method == "POST":
        # Get values from form (all inside the form, no external dependency)
        sl_no = clean_slno(request.POST.get("sl_no"))
        test_case_id = clean(request.POST.get("test_case_id"))
        sw_part_number = clean(request.POST.get("sw_part_number"))
        app_sw_version = clean(request.POST.get("app_sw_version"))
        sheet_name = clean(request.POST.get("sheet_name", "")).strip()
        
        # Validate required fields
        if not sw_part_number:
            messages.error(request, "SW Part Number is required.")
            return render(request, "testmanager/add.html", {
                "available_sheets": available_sheets,
                "sw_list": sw_list,
                "active_versions": active_versions,
                "next_sl_no": next_sl_no,
            })
        
        if not app_sw_version:
            messages.error(request, "Application SW Version is required.")
            return render(request, "testmanager/add.html", {
                "available_sheets": available_sheets,
                "sw_list": sw_list,
                "active_versions": active_versions,
                "next_sl_no": next_sl_no,
            })
        
        if not sheet_name:
            messages.error(request, "Sheet name is required.")
            return render(request, "testmanager/add.html", {
                "available_sheets": available_sheets,
                "sw_list": sw_list,
                "active_versions": active_versions,
                "next_sl_no": next_sl_no,
            })
        
        sheet_name_clean = sheet_name.upper()
        
        # CRITICAL: Get or create TestCaseVersion (self-contained - no blocking)
        # If version doesn't exist, create it automatically
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version
        ).first()
        
        if not version_obj:
            # Check if there's an existing active version for this SW part number
            existing_active = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                is_active=True,
                is_locked=False
            ).first()
            
            # Create new TestCaseVersion
            # If no active version exists for this SW, make this one active
            # Otherwise, create as inactive (user selected a specific version)
            is_active = existing_active is None
            version_obj = TestCaseVersion.objects.create(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=app_sw_version,
                is_active=is_active,
                is_locked=False
            )
        else:
            # Version exists - check if it's locked
            if version_obj.is_locked:
                messages.error(
                    request,
                    f"Version '{app_sw_version}' is locked and cannot be modified."
                )
                return render(request, "testmanager/add.html", {
                    "available_sheets": available_sheets,
                    "sw_list": sw_list,
                    "active_versions": active_versions,
                    "next_sl_no": next_sl_no,
                })

        # Store base_test_case_id (original ID from form)
        base_test_case_id = test_case_id
        
        # Build versioned test_case_id using the selected version from form
        versioned_test_case_id = f"{base_test_case_id}_{app_sw_version}" if app_sw_version else base_test_case_id
        
        # Check for duplicate using (base_test_case_id + app_sw_version + instance)
        existing = TestCase.objects.filter(
            instance=active_instance,
            base_test_case_id=base_test_case_id,
            app_sw_version=app_sw_version
        ).first()
        
        if existing:
            url = reverse("testcase_list") + f"?highlight={existing.id}"
            messages.error(
                request,
                mark_safe(f"Test Case ID '{base_test_case_id}' already exists for version '{app_sw_version}'. <a href='{url}'>View it</a>")
            )
            return render(request, "testmanager/add.html", {
                "available_sheets": available_sheets,
                "sw_list": sw_list,
                "active_versions": active_versions,
                "next_sl_no": next_sl_no,
            })

        # CRITICAL: Create TestCaseSheet if it doesn't exist (self-contained)
        # Sheet is created automatically if it doesn't exist for this version
        from ..models import TestCaseSheet
        sheet_obj, sheet_created = TestCaseSheet.objects.get_or_create(
            version=version_obj,
            sheet_name=sheet_name_clean
        )
        
        # Also ensure SheetMeta exists (for backward compatibility)
        from ..models import SheetMeta
        SheetMeta.objects.get_or_create(
            sheet_name=sheet_name_clean,
            defaults={"headers": []}
        )

        # Create test case with explicit version and sheet binding
        with transaction.atomic():  # type: ignore
            # CRITICAL: If sl_no is not provided or empty, calculate next sl_no for this sw_part_number
            # sl_no is scoped per sw_part_number (not sheet/version/feature)
            if not sl_no or not sl_no.strip():
                from ..utils import get_next_sl_no_for_sw_part_number
                sl_no = get_next_sl_no_for_sw_part_number(sw_part_number, active_instance)
            
            # CRITICAL: sl_no must be stored in TestCase (master definition)
            # sl_no must be immutable across versions
            test = TestCase.objects.create(
                instance=active_instance,
                sheet_name=sheet_name_clean,  # Store sheet_name for backward compatibility
                sl_no=sl_no,  # CRITICAL: Persist sl_no from form (or auto-calculated)
                sw_part_number=sw_part_number,
                feature=clean(request.POST.get("feature")),
                requirement_id=clean(request.POST.get("requirement_id")),
                requirement_description=clean(request.POST.get("requirement_description")),
                base_test_case_id=base_test_case_id,  # Store original ID
                test_case_id=versioned_test_case_id,  # Store versioned ID
                test_case_summary=clean(request.POST.get("test_case_summary")),
                pre_conditions=clean(request.POST.get("pre_conditions")),
                inputs=clean(request.POST.get("inputs")),
                periodic_time=clean(request.POST.get("periodic_time")),
                test_steps=clean(request.POST.get("test_steps")),
                expected_result=clean(request.POST.get("expected_result")),
                app_sw_version=app_sw_version,  # Store for backward compatibility
                status="",
                reports="",
                comments="",
            )

        # ✅ ACTIVITY LOG
        ActivityLog.objects.create(
            user=request.user,
            action=ACTIVITY_ACTION_ADD,
            reference=test_case_id,
            remarks=f"Test case created in active instance, version: {app_sw_version}, sheet: {sheet_name_clean}",
            content_type="TestCase"
        )
  
        messages.success(request, f"Test case added successfully! (SW: {sw_part_number}, Version: {app_sw_version}, Sheet: {sheet_name_clean})")
        
        # Redirect to list page and highlight the created testcase
        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url:
            # Add highlight parameter to existing URL
            parsed = urlparse(next_url)
            params = parse_qs(parsed.query)
            params['highlight'] = [str(test.id)]
            new_query = urlencode(params, doseq=True)
            redirect_url = f"{parsed.path}?{new_query}"
            return redirect(redirect_url)
        
        # Redirect to list with highlight
        redirect_url = reverse("testcase_list") + f"?sheet={sheet_name_clean}&sw={sw_part_number}&version={app_sw_version}&highlight={test.id}"
        return redirect(redirect_url)
        

    return render(
        request,
        "testmanager/add.html",
        {
            "available_sheets": available_sheets,
            "sw_list": sw_list,
            "active_versions": active_versions,
            "next_sl_no": next_sl_no,
        }
    )

# -------------------------------------------------------------------------------------
# Create new
# -------------------------------------------------------------------------------------

@require_POST
@login_required
@manager_required(json_response=True)
def create_new_version(request):
    """
    FEATURE-SCOPED VERSION CREATION: Create New Version (NON-DESTRUCTIVE)
    
    AUTHORITY: Only MANAGER users can create new versions.
    SCOPE: Operates ONLY on selected Sheet + SW Part Number + Feature(s) + Versions.
    
    ABSOLUTE RULES:
    - ADD new data only
    - NEVER remove existing data
    - NEVER modify other features
    - NEVER block other testers
    
    REQUEST PAYLOAD (JSON):
    {
        "sheet": "SheetName",
        "sw_part_number": "SW123",
        "features": ["Feature1", "Feature2"],
        "old_version": "V1.0",
        "new_version": "V2.0"
    }
    
    STEPS:
    1. Validate feature completion (feature-scoped only)
    2. Read old test cases (read-only, never modify)
    3. Validate new version doesn't exist
    4. Clone test cases (selected features only)
    5. Create execution rows (reset status/reports/comments)
    6. All in transaction with rollback on error
    """
    import json
    
    active_instance = get_active_instance()
    
    try:
        # Parse request data (support both JSON and form data)
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            # Fallback to form data
            data = {
                'sheet': request.POST.get('sheet', '').strip(),
                'sw_part_number': request.POST.get('sw_part_number', '').strip(),
                'features': request.POST.getlist('features') if hasattr(request.POST, 'getlist') else [request.POST.get('features', '')],
                'old_version': request.POST.get('old_version', '').strip(),
                'new_version': request.POST.get('new_version', '').strip(),
            }
        
        # Extract and validate input
        sheet_name = data.get('sheet', '').strip()
        sw_part_number = data.get('sw_part_number', '').strip()
        feature_names = data.get('features', [])
        old_version = data.get('old_version', '').strip()
        new_version = data.get('new_version', '').strip()
        
        # Convert features to list if it's a string
        if isinstance(feature_names, str):
            feature_names = [feature_names] if feature_names else []
        feature_names = [f.strip() for f in feature_names if f and f.strip()]
        
        # Validate required fields
        if not sheet_name:
            return JsonResponse({"ok": False, "error": "Sheet name is required."}, status=400)
        if not sw_part_number:
            return JsonResponse({"ok": False, "error": "SW Part Number is required."}, status=400)
        if not feature_names:
            return JsonResponse({"ok": False, "error": "At least one feature must be selected."}, status=400)
        if not old_version:
            return JsonResponse({"ok": False, "error": "Old version is required."}, status=400)
        if not new_version:
            return JsonResponse({"ok": False, "error": "New version is required."}, status=400)
        
        # STEP 1: Validate feature completion (FEATURE-SCOPED ONLY)
        from ..services import get_feature_completion
        from ..models import TestCaseVersion, TestCaseSheet, SWVersionMapping
        
        # Get old version object
        old_version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=old_version
        ).first()
        
        if not old_version_obj:
            return JsonResponse({
                "ok": False,
                "error": f"Old version '{old_version}' not found for SW Part Number '{sw_part_number}'."
            }, status=400)
        
        # Get sheet object
        sheet_obj = TestCaseSheet.objects.filter(
            version=old_version_obj,
            sheet_name=sheet_name
        ).first()
        
        if not sheet_obj:
            return JsonResponse({
                "ok": False,
                "error": f"Sheet '{sheet_name}' not found for version '{old_version}' and SW Part Number '{sw_part_number}'."
            }, status=400)
        
        # Validate each selected feature is completed
        incomplete_features = []
        for feature_name in feature_names:
            # Check if feature exists
            feature_exists = TestCase.objects.filter(
                instance=active_instance,
                sheet_name=sheet_name,
                sw_part_number=sw_part_number,
                app_sw_version=old_version,
                feature=feature_name
            ).exists()
            
            if not feature_exists:
                incomplete_features.append(f'Feature "{feature_name}" not found')
                continue
            
            # Check feature completion
            total_count, completed_count, is_completed = get_feature_completion(
                active_instance, old_version_obj, sheet_obj, feature_name
            )
            
            if total_count == 0:
                incomplete_features.append(f'No test cases found for feature "{feature_name}"')
            elif not is_completed:
                incomplete_features.append(
                    f'Feature "{feature_name}" is not completed ({completed_count}/{total_count} tests have status PASS or FAIL)'
                )
        
        if incomplete_features:
            return JsonResponse({
                "ok": False,
                "error": f"Cannot create new version. The following features are not completed: {', '.join(incomplete_features)}."
            }, status=400)
        
        # STEP 2: Read old test cases (READ-ONLY - NEVER MODIFY)
        old_test_cases = TestCase.objects.filter(
            instance=active_instance,
            sheet_name=sheet_name,
            sw_part_number=sw_part_number,
            app_sw_version=old_version,
            feature__in=feature_names
        )
        
        if not old_test_cases.exists():
            return JsonResponse({
                "ok": False,
                "error": f"No test cases found for selected features in old version '{old_version}'."
            }, status=400)
        
        # STEP 3: Validate new version doesn't already exist for this scope
        existing_new_version = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=new_version
        ).first()
        
        if existing_new_version:
            # Check if test cases already exist for this new version + features
            existing_test_cases = TestCase.objects.filter(
                instance=active_instance,
                sheet_name=sheet_name,
                sw_part_number=sw_part_number,
                app_sw_version=new_version,
                feature__in=feature_names
            )
            
            if existing_test_cases.exists():
                return JsonResponse({
                    "ok": False,
                    "error": f"Version '{new_version}' already exists with test cases for selected features. Cannot create duplicate."
                }, status=400)
        
        # STEP 4 & 5: Clone test cases and create execution rows (IN TRANSACTION)
        from django.utils import timezone
        
        with transaction.atomic():  # type: ignore
            # Create or get new version object
            if existing_new_version:
                new_version_obj = existing_new_version
            else:
                # Create new version object (set old version to inactive first)
                TestCaseVersion.objects.filter(
                    instance=active_instance,
                    sw_part_number=sw_part_number,
                    is_active=True
                ).update(is_active=False)
                
                new_version_obj = TestCaseVersion.objects.create(
                    instance=active_instance,
                    sw_part_number=sw_part_number,
                    app_sw_version=new_version,
                    is_active=True,
                    is_locked=False
                )
            
            # Create or get sheet object for new version
            new_sheet_obj, _ = TestCaseSheet.objects.get_or_create(
                version=new_version_obj,
                sheet_name=sheet_name
            )
            
            # Update SWVersionMapping (for backward compatibility)
            SWVersionMapping.objects.update_or_create(
                instance=active_instance,
                sw_part_number=sw_part_number,
                defaults={"version": new_version, "is_active": True, "updated_at": timezone.now()}
            )
            
            # Clone test cases for selected features only
            new_test_cases = []
            for old_tc in old_test_cases:
                # Get base_test_case_id
                base_test_case_id = old_tc.base_test_case_id or old_tc.test_case_id
                
                # Check if new test case already exists (duplicate check)
                if TestCase.objects.filter(
                    instance=active_instance,
                    base_test_case_id=base_test_case_id,
                    app_sw_version=new_version
                ).exists():
                    continue  # Skip if already exists
                
                # Build new test_case_id
                new_test_case_id = f"{base_test_case_id}_{new_version}" if new_version else base_test_case_id
                
                # Clone test case (NON-DESTRUCTIVE: old_tc remains unchanged)
                new_tc = TestCase(
                    instance=active_instance,
                    sheet_name=old_tc.sheet_name,
                    sl_no=old_tc.sl_no,  # Preserve sl_no
                    sw_part_number=sw_part_number,
                    feature=old_tc.feature,
                    requirement_id=old_tc.requirement_id,
                    requirement_description=old_tc.requirement_description,
                    base_test_case_id=base_test_case_id,
                    test_case_id=new_test_case_id,
                    test_case_summary=old_tc.test_case_summary,
                    pre_conditions=old_tc.pre_conditions,
                    inputs=old_tc.inputs,
                    periodic_time=old_tc.periodic_time,
                    test_steps=old_tc.test_steps,
                    expected_result=old_tc.expected_result,
                    app_sw_version=new_version,
                    # Reset execution data (new version starts clean)
                    status="",
                    reports="",
                    comments="",
                )
                new_test_cases.append(new_tc)
            
            if not new_test_cases:
                return JsonResponse({
                    "ok": False,
                    "error": "No new test cases to create. They may already exist for the new version."
                }, status=400)
            
            # Bulk create new test cases
            TestCase.objects.bulk_create(new_test_cases, batch_size=500)
            
            # STEP 5: Create execution rows for new test cases (reset status/reports/comments)
            # Refetch created test cases to get their IDs
            created_test_case_ids = [tc.test_case_id for tc in new_test_cases]
            created_test_cases = TestCase.objects.filter(
                instance=active_instance,
                test_case_id__in=created_test_case_ids
            )
            
            new_executions = []
            for new_tc in created_test_cases:
                # Check if execution already exists
                if TestExecution.objects.filter(
                    instance=active_instance,
                    test_case=new_tc,
                    version=new_version_obj
                ).exists():
                    continue
                
                # Create new execution row with reset fields
                new_executions.append(
                    TestExecution(
                        instance=active_instance,
                        test_case=new_tc,
                        version=new_version_obj,
                        sheet=new_sheet_obj,
                        sw_part_number=sw_part_number,
                        app_sw_version=new_version,
                        status="",  # Reset
                        reports="",  # Reset
                        comments="",  # Reset
                        is_locked=False
                    )
                )
            
            if new_executions:
                TestExecution.objects.bulk_create(new_executions, batch_size=500)
        
        # Success response
        return JsonResponse({
            "ok": True,
            "message": f"New version '{new_version}' created successfully for selected feature(s).",
            "created_test_cases": len(new_test_cases),
            "created_executions": len(new_executions),
            "features": feature_names
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            "ok": False,
            "error": "Invalid JSON data in request."
        }, status=400)
    except Exception as e:
        from ..logging_utils import log_error
        import traceback
        log_error(
            "testcases.py:create_new_version",
            "Error creating new version",
            {
                "error": str(e),
                "type": type(e).__name__,
                "traceback": traceback.format_exc()
            },
            exc_info=True
        )
        return JsonResponse({
            "ok": False,
            "error": f"Error creating new version: {str(e)}"
        }, status=500)


# -------------------------------------------------------------------------------------
# test_it VIEW (EXECUTION ONLY)
# -------------------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@login_required
def testcase_test_it(request, id):
    """
    Execute a test case (create/update TestExecution).
    
    RISK REMOVAL: Removed all _debug_log() calls, uses proper logging_utils.
    Uses decorators for role checks instead of duplicated logic.
    """
    log_debug("views.py:testcase_test_it", "Function entry", {"test_id": id, "method": request.method})
    try:
        test = get_object_or_404(TestCase, id=id)
        log_debug("views.py:testcase_test_it", "Test case found", {"test_case_id": test.test_case_id, "sw_part_number": test.sw_part_number})
    except Exception as e:
        log_error("views.py:testcase_test_it", "Error getting test case", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        raise
    
    next_url = request.GET.get("next") or request.POST.get("next")

    # Get active instance - all queries must filter by active instance only
    active_instance = get_active_instance()

    # Check if user is Manager (needed for version handling)
    is_manager_check = is_manager(request.user)
    if request.user.is_superuser:
        is_manager_check = True
    
    # Get sw_part_number from URL parameter 'sw' or test case
    sw_part_number = request.GET.get("sw", "").strip()
    if not sw_part_number:
        sw_part_number = test.sw_part_number or ""
    
    # CRITICAL: For non-managers, resolve active version ONCE and enforce read-only rules
    # Managers: Can specify version via URL parameter
    # Non-managers: Version is automatically resolved to latest active version
    app_sw_version = request.GET.get("version", "").strip()
    active_version_obj = None
    
    if not is_manager_check:
        # Non-manager: Auto-resolve to latest active version
        # Ignore version parameter from URL - always use active version
        active_version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            is_active=True
        ).order_by('-created_at').first()
        
        if not active_version_obj:
            messages.error(request, "No active version found for this SW Part Number. Please contact a manager.")
            if next_url:
                return redirect(next_url)
            return redirect("testcase_list")
        
        app_sw_version = active_version_obj.app_sw_version
    else:
        # Manager: Use version from URL if provided
        if app_sw_version:
            active_version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=app_sw_version
            ).first()
        else:
            # If no version in URL, try to get active version
            active_version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                is_active=True
            ).order_by('-created_at').first()
            if active_version_obj:
                app_sw_version = active_version_obj.app_sw_version
    
    log_debug("views.py:testcase_test_it", "Version-locked execution", {
        "app_sw_version": app_sw_version,
        "sw_part_number": sw_part_number,
        "version_from_url": bool(request.GET.get("version")),
        "sw_from_url": bool(request.GET.get("sw"))
    })

    if request.method == "POST":
        log_debug("views.py:testcase_test_it", "POST request received", {"status": request.POST.get("status"), "version": app_sw_version})
        try:
            status = clean(request.POST.get("status"))
            reports = clean(request.POST.get("reports"))
            comments = clean(request.POST.get("comments"))
            log_debug("views.py:testcase_test_it", "Form data cleaned", {"status": status})
        except Exception as e:
            log_error("views.py:testcase_test_it", "Error cleaning form data", {"error": str(e), "type": type(e).__name__}, exc_info=True)
            raise

        # VERSION-LOCKED EXECUTION: Validate version is provided (already checked above, but double-check)
        if not app_sw_version:
            messages.error(request, "Version parameter is required. Please select a version from the dropdown.")
            if next_url:
                return redirect(next_url)
            return redirect("testcase_list")
        
        if not status:
            messages.error(request, "Execution status is required.")
            return redirect(request.path)

        # VERSION-LOCKED EXECUTION: Get execution for exact version from URL parameters
        # Use sw_part_number from URL (or test case if not in URL)
        try:
            execution = get_execution_for_version(test, sw_part_number, app_sw_version)
            log_debug("views.py:testcase_test_it", "Execution query completed", {
                "execution_exists": execution is not None,
                "version": app_sw_version,
                "sw_part_number": sw_part_number
            })
        except Exception as e:
            log_error("views.py:testcase_test_it", "Error querying execution", {"error": str(e), "type": type(e).__name__}, exc_info=True)
            raise

        # Get active instance - all executions must belong to active instance
        active_instance = get_active_instance()
        
        # CRITICAL: Get or create TestCaseVersion to check lock status
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version
        ).first()
        
        # WRITE ACCESS RULES (STRICT):
        # - ALL authenticated users can CREATE and UPDATE when:
        #   - version.is_locked == False
        #   - execution.is_locked == False (if execution exists)
        # - Locked versions/executions are read-only for EVERYONE (including manager)
        
        # CRITICAL: Check if version is locked - locked versions are read-only for everyone
        if version_obj and version_obj.is_locked:
            messages.error(request, "This version is locked and cannot be modified.")
            if next_url:
                return redirect(next_url)
            return redirect("testcase_list")
        
        # VERSION-LOCKED EXECUTION: Enforce edit rules
        if execution:
            # CRITICAL: Check if execution is locked
            if execution.is_locked:
                messages.error(request, "This execution is locked and cannot be modified.")
                if next_url:
                    return redirect(next_url)
                return redirect("testcase_list")
            # Execution exists and is not locked - allow update for all authenticated users
        else:
            # No execution exists yet - allow create for all authenticated users if version is not locked
            pass

        # CRITICAL: Get or create TestCaseVersion and TestCaseSheet
        # Version must NEVER be inferred dynamically after save
        from ..models import TestCaseSheet
        version, _ = TestCaseVersion.objects.get_or_create(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version,
            defaults={"is_active": True, "is_locked": False}
        )
        
        sheet_name = test.sheet_name or ""
        sheet = None
        if sheet_name:
            sheet, _ = TestCaseSheet.objects.get_or_create(
                version=version,
                sheet_name=sheet_name
            )
        
        # CRITICAL: Check if version is locked - locked versions are read-only for everyone
        if version.is_locked:
            messages.error(request, "This version is locked and cannot be modified.")
            if next_url:
                return redirect(next_url)
            return redirect("testcase_list")
        
        # VERSION-LOCKED EXECUTION: Create or update execution with EXACT values from URL
        # active_instance already fetched above
        # Use sw_part_number from URL parameter (or test case if not provided)
        # Use app_sw_version strictly from URL parameter
        if execution:
            # CRITICAL: Check if execution is locked
            if execution.is_locked:
                messages.error(request, "This execution is locked and cannot be modified.")
                if next_url:
                    return redirect(next_url)
                return redirect("testcase_list")
            
            # VERSION-LOCKED EXECUTION: Update existing execution for this exact version
            execution.status = status
            execution.reports = reports
            execution.comments = comments
            # Ensure sw_part_number matches URL parameter
            execution.sw_part_number = sw_part_number
            execution.app_sw_version = app_sw_version
            execution.instance = active_instance  # Ensure instance is set
            execution.version = version  # CRITICAL: Bind to version explicitly
            execution.sheet = sheet  # CRITICAL: Bind to sheet explicitly
            if not execution.user:
                execution.user = request.user
            execution.save()
            created = False
        else:
            # VERSION-LOCKED EXECUTION: Create new execution with exact values from URL
            # CRITICAL: Bind execution to TestCaseVersion and TestCaseSheet explicitly
            execution = TestExecution.objects.create(
                instance=active_instance,  # All executions belong to active instance
                test_case=test,
                version=version,  # CRITICAL: Explicit FK to TestCaseVersion
                sheet=sheet,  # CRITICAL: Explicit FK to TestCaseSheet
                user=request.user,
                sw_part_number=sw_part_number,  # From URL parameter 'sw'
                app_sw_version=app_sw_version,  # From URL parameter 'version'
                status=status,
                reports=reports,
                comments=comments,
                is_locked=False  # New executions are not locked
            )
            created = True

        # 📝 Activity log
        # RISK REMOVAL: Using constants for action strings
        ActivityLog.objects.create(
            user=request.user,
            action=ACTIVITY_ACTION_EDIT if not created else ACTIVITY_ACTION_ADD,
            reference=f"{test.test_case_id} | {app_sw_version}",
            remarks=f"Execution {'updated' if not created else 'created'} → {status}",
            content_type="TestExecution",
        )

        messages.success(
            request,
            f"Execution saved for version {app_sw_version or '(no version)'}."
        )

        if next_url:
            return redirect(next_url)
        return redirect("testcase_list")

    # -----------------------
    # GET REQUEST
    # -----------------------
    # VERSION-LOCKED EXECUTION: Get execution for exact version from URL parameters
    # Use sw_part_number from URL (or test case if not in URL)
    try:
        execution = get_execution_for_version(test, sw_part_number, app_sw_version)
        log_debug("views.py:testcase_test_it", "GET - Execution query completed", {
            "execution_exists": execution is not None,
            "version": app_sw_version,
            "sw_part_number": sw_part_number
        })
    except Exception as e:
        log_error("views.py:testcase_test_it", "GET - Error querying execution", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        execution = None

    # OPEN ACCESS (READ): ALL authenticated users can VIEW execution pages
    # WRITE ACCESS: Tester/Developer/Manager can CREATE/UPDATE when version/execution is not locked
    active_instance = get_active_instance()
    
    # SECURITY CRITICAL: Check version access and execution permissions
    from ..version_service import get_versions_for_user, can_user_access_version, can_user_execute_version
    
    version_obj = None
    if app_sw_version and sw_part_number:
        # SECURITY: Use role-based version filtering
        versions = get_versions_for_user(request.user, active_instance, sw_part_number)
        version_obj = versions.filter(app_sw_version=app_sw_version).first()
        
        # CRITICAL: Block access if user cannot access this version
        if version_obj and not can_user_access_version(request.user, version_obj):
            messages.error(request, "You don't have permission to access this version. Only active versions are accessible.")
            return redirect("testcase_list")
    
    # Determine if user can edit based on execution permissions
    can_edit = False
    edit_reason = ""
    
    if execution:
        # Execution exists - check execution permissions
        if execution.is_locked:
            can_edit = False
            edit_reason = "This execution is locked and cannot be modified."
        elif version_obj:
            can_execute, reason = can_user_execute_version(request.user, version_obj)
            can_edit = can_execute
            edit_reason = reason if not can_execute else ""
        else:
            can_edit = False
            edit_reason = "Version not found or access denied."
    else:
        # No execution exists yet - check execution permissions
        if version_obj:
            can_execute, reason = can_user_execute_version(request.user, version_obj)
            can_edit = can_execute
            edit_reason = reason if not can_execute else ""
        else:
            can_edit = False
            edit_reason = "Version not found or access denied."

    # Determine if this is the current/active version
    is_current_version = False
    if app_sw_version and sw_part_number:
        is_current_version = is_version_current(app_sw_version, sw_part_number, active_instance=active_instance)

    try:
        log_debug("views.py:testcase_test_it", "Rendering template", {"template": "testmanager/test_it.html"})
        return render(
            request,
            "testmanager/test_it.html",
            {
                "test": test,
                "execution": execution,
                "next": next_url,
                "app_sw_version": app_sw_version,
                "can_edit": can_edit,
                "is_current_version": is_current_version,
                "edit_reason": edit_reason if not can_edit else "",
            }
        )
    except Exception as e:
        log_error("views.py:testcase_test_it", "Error rendering template", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        raise


# -------------------------------------------------------------------------------------
# VIEW TEST EXECUTION (READ-ONLY)
# -------------------------------------------------------------------------------------
@login_required
def get_next_sl_no_api(request):
    """
    API endpoint to get the next sl_no for a given sw_part_number.
    Used by "Add New Test Case" UI to auto-fill sl_no field.
    
    sl_no is scoped per sw_part_number (not sheet/version/feature).
    """
    sw_part_number = request.GET.get('sw_part_number', '').strip()
    
    if not sw_part_number:
        return JsonResponse({
            'ok': False,
            'error': 'SW Part Number is required'
        }, status=400)
    
    from ..utils import get_next_sl_no_for_sw_part_number
    from ..services import get_active_instance
    
    active_instance = get_active_instance()
    next_sl_no = get_next_sl_no_for_sw_part_number(sw_part_number, active_instance)
    
    return JsonResponse({
        'ok': True,
        'next_sl_no': next_sl_no,
        'sw_part_number': sw_part_number
    })


@login_required
def view_test_execution(request, id):
    """
    View test case and execution details in read-only mode.
    
    RISK REMOVAL: Removed _debug_log() call, uses proper logging_utils.
    """
    try:
        test = get_object_or_404(TestCase, id=id)
    except Exception as e:
        log_error("views.py:view_test_execution", "Error getting test case", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        raise
    
    next_url = request.GET.get("next", "")
    
    # Check if user is Manager (needed for version handling)
    is_manager_check = is_manager(request.user)
    if request.user.is_superuser:
        is_manager_check = True
    
    # Get sw_part_number from URL parameter 'sw' or test case
    sw_part_number = request.GET.get("sw", "").strip()
    if not sw_part_number:
        sw_part_number = test.sw_part_number or ""
    
    # STRICT VERSION CONTROL: Auto-resolve version for non-managers
    # Managers: Can specify version via URL parameter
    # Non-managers: Version is automatically resolved to latest active version
    app_sw_version = request.GET.get("version", "").strip()
    
    if not is_manager_check:
        # Non-manager: Auto-resolve to latest active version
        # Ignore version parameter from URL - always use active version
        app_sw_version = get_current_active_version(sw_part_number)
        if not app_sw_version:
            messages.error(request, "No active version found for this SW Part Number. Please contact a manager.")
            return redirect("testcase_list")
    else:
        # Manager: Use version from URL if provided, otherwise use test case version
        if not app_sw_version:
            app_sw_version = test.app_sw_version or ""
        # If still no version, try to get active version
        if not app_sw_version:
            app_sw_version = get_current_active_version(sw_part_number)
    
    # INSTANCE ISOLATION: Get execution for exact version AND instance from URL parameters
    # Use sw_part_number from URL (or test case if not in URL)
    # Get active instance for filtering
    active_instance = get_active_instance()
    
    # CRITICAL: Verify test case belongs to active instance
    if test.instance != active_instance:
        messages.error(request, "Test case does not belong to active instance.")
        return redirect("testcase_list")
    
    execution = None
    if app_sw_version:
        # INSTANCE ISOLATION: get_execution_for_version now filters by instance internally
        execution = get_execution_for_version(test, sw_part_number, app_sw_version)
        # CRITICAL: Double-check instance matches (extra safety)
        if execution and execution.instance != active_instance:
            execution = None
    elif sw_part_number:
        # If no version in URL, try to get latest execution (for backward compatibility, active instance only)
        # CRITICAL: Filter by instance to ensure instance isolation
        execution = TestExecution.objects.filter(
            instance=active_instance,  # CRITICAL: Filter by instance
            test_case=test,
            sw_part_number=sw_part_number,
        ).order_by('-executed_at').first()
    
    # Always display base_test_case_id (version suffix never shown in UI)
    display_test_case_id = test.base_test_case_id or test.test_case_id
    # Auto-derive requirement_id from base_test_case_id if requirement_id is empty
    display_requirement_id = get_requirement_id(test.base_test_case_id or test.test_case_id, test.requirement_id)
    
    return render(
        request,
        "testmanager/view_test.html",
        {
            "test": test,
            "execution": execution,
            "next": next_url,
            "app_sw_version": app_sw_version,
            "display_test_case_id": display_test_case_id,
            "display_requirement_id": display_requirement_id,
        }
    )

