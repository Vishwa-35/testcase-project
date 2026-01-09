"""
Export Views - NEW SIMPLIFIED EXPORT FLOW

NEW FLOW:
1. Sheet Selection (required)
2. Version Selection (multi-select via checkboxes)
3. Feature Selection (multi-select via checkboxes)
4. Export (always allowed, no restrictions)
"""

import json
import os
from datetime import datetime

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, FileResponse
from django.utils import timezone
from django.conf import settings

from ..models import (
    TestCase, TestCaseSheet, TestCaseVersion, TestExecution,
    ProjectOverview, TestExecutionSnapshot
)
from ..services import get_active_instance
from ..excel_export import build_testcase_export_workbook
from ..constants import PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP
from ..utils import get_requirement_id
from ..logging_utils import log_error


# =====================================================
# API ENDPOINTS FOR NEW EXPORT FLOW
# =====================================================

@login_required
def get_sheets_api(request):
    """
    API: Get all available sheets.
    
    PERMISSIONS: Manager only (superuser) - for export flow
    TESTERS cannot access this API.
    
    Returns list of unique sheet names from TestCase data (active instance only).
    """
    # PERMISSION CHECK: Manager only
    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': 'Permission denied. Only managers can access export APIs.'
        }, status=403)
    
    try:
        # FIXED: Get sheet names ONLY from SheetMeta (source of truth)
        # This ensures all sheets appear, even if they have no test cases
        from ..models import SheetMeta
        sheets = list(
            SheetMeta.objects.all()
            .order_by('sheet_name')
            .values_list('sheet_name', flat=True)
        )
        
        return JsonResponse({
            'ok': True,
            'sheets': [{'name': sheet} for sheet in sheets if sheet]
        })
    except Exception as e:
        log_error("export_views.py:get_sheets_api", "Error loading sheets", {
            "error": str(e),
            "type": type(e).__name__
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading sheets: {str(e)}'
        }, status=500)


@login_required
def get_versions_for_sheet_api(request):
    """
    API: Get versions for a selected sheet.
    
    PERMISSIONS: Manager only (superuser) - for export flow
    TESTERS cannot access this API.
    
    BUG FIX: Returns ONLY active versions (is_active=True) for active instance.
    Old versions NEVER shown.
    
    Returns versions GROUPED by SW Part Number.
    Each (SW Part Number + Version) combination appears ONLY ONCE.
    """
    # PERMISSION CHECK: Manager only
    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': 'Permission denied. Only managers can access export APIs.'
        }, status=403)
    
    try:
        active_instance = get_active_instance()
        sheet_name = request.GET.get('sheet', '')
        
        if not sheet_name:
            return JsonResponse({
                'ok': False,
                'error': 'Sheet name is required'
            }, status=400)
        
        # BUG FIX: Filter by active instance and active versions only
        # Get DISTINCT (sw_part_number, app_sw_version) combinations from TestCaseVersion
        # Filter by active instance and is_active=True to exclude old versions
        version_objs = TestCaseVersion.objects.filter(
            instance=active_instance,
            is_active=True  # BUG FIX: Only active versions
        ).select_related()
        
        # Filter versions that have test cases in the selected sheet
        version_ids_with_sheet = TestCase.objects.filter(
            instance=active_instance,
            sheet_name=sheet_name
        ).exclude(
            sw_part_number='',
            app_sw_version=''
        ).values_list('sw_part_number', 'app_sw_version').distinct()
        
        # Create a set of (sw_part_number, app_sw_version) tuples for quick lookup
        sheet_versions_set = {(sw, ver) for sw, ver in version_ids_with_sheet if sw and ver}
        
        # Get version objects that match the sheet and are active
        version_combos = []
        for version_obj in version_objs:
            key = (version_obj.sw_part_number, version_obj.app_sw_version)
            if key in sheet_versions_set:
                version_combos.append({
                    'sw_part_number': version_obj.sw_part_number,
                    'app_sw_version': version_obj.app_sw_version
                })
        
        # Track unique (SW + Version) combinations to prevent duplicates
        seen_combinations = set()
        # Also track version IDs to prevent duplicate IDs in the same SW group
        seen_version_ids_by_sw = {}
        
        # Group versions by SW Part Number
        versions_by_sw = {}
        
        for combo in version_combos:
            sw_part_number = combo['sw_part_number']
            app_sw_version = combo['app_sw_version']
            
            if not sw_part_number or not app_sw_version:
                continue
            
            # Use (sw_part_number, app_sw_version) as unique key to avoid duplicates
            unique_key = (sw_part_number, app_sw_version)
            
            # Skip if we've already processed this combination
            if unique_key in seen_combinations:
                continue
            
            # Mark as seen
            seen_combinations.add(unique_key)
            
            # Find TestCaseVersion object - must be active
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=app_sw_version,
                is_active=True  # BUG FIX: Only active versions
            ).first()
            
            if version_obj:
                # Initialize tracking for this SW if not exists
                if sw_part_number not in seen_version_ids_by_sw:
                    seen_version_ids_by_sw[sw_part_number] = set()
                    versions_by_sw[sw_part_number] = []
                
                # Skip if we've already added this version ID for this SW
                if version_obj.id in seen_version_ids_by_sw[sw_part_number]:
                    continue
                
                # Mark version ID as seen for this SW
                seen_version_ids_by_sw[sw_part_number].add(version_obj.id)
                
                # Add version to the SW group (only active versions)
                versions_by_sw[sw_part_number].append({
                    'id': version_obj.id,
                    'sw_part_number': sw_part_number,
                    'version': app_sw_version,
                    'is_active': True,  # Always True (we filtered for active only)
                    'created_at': version_obj.created_at.isoformat() if version_obj.created_at else None
                })
        
        # BUG FIX: Sort versions within each SW by created_at (newest first)
        # Only keep the most recent version per SW for export
        for sw in versions_by_sw:
            versions_by_sw[sw].sort(key=lambda x: x.get('created_at') or '', reverse=True)
            # EXPORT RULE: Export ONLY most recent version per SW
            versions_by_sw[sw] = versions_by_sw[sw][:1]  # Keep only the most recent
        
        # Convert to list format grouped by SW
        grouped_versions = []
        for sw_part_number in sorted(versions_by_sw.keys()):
            grouped_versions.append({
                'sw_part_number': sw_part_number,
                'versions': versions_by_sw[sw_part_number]
            })
        
        return JsonResponse({
            'ok': True,
            'versions_by_sw': grouped_versions
        })
    except Exception as e:
        log_error("export_views.py:get_versions_for_sheet_api", "Error loading versions", {
            "error": str(e),
            "type": type(e).__name__,
            "sheet": sheet_name
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading versions: {str(e)}'
        }, status=500)


@login_required
def get_features_for_selection_api(request):
    """
    API: Get all features for selected sheet + versions.
    
    PERMISSIONS: Manager only (superuser) - for export flow
    TESTERS cannot access this API.
    
    Returns DISTINCT features that exist ONLY in the selected sheet and selected SW Part Numbers.
    Features are filtered strictly by selected SW Part Number(s).
    """
    # PERMISSION CHECK: Manager only
    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': 'Permission denied. Only managers can access export APIs.'
        }, status=403)
    
    try:
        active_instance = get_active_instance()
        sheet_name = request.GET.get('sheet', '')
        version_ids = request.GET.getlist('versions')  # Can be multiple
        
        if not sheet_name:
            return JsonResponse({
                'ok': False,
                'error': 'Sheet name is required'
            }, status=400)
        
        if not version_ids:
            return JsonResponse({
                'ok': False,
                'error': 'At least one version must be selected'
            }, status=400)
        
        # Get version objects
        version_objs = TestCaseVersion.objects.filter(
            instance=active_instance,
            id__in=[int(vid) for vid in version_ids if vid.isdigit()]
        )
        
        if not version_objs.exists():
            return JsonResponse({
                'ok': False,
                'error': 'No valid versions selected'
            }, status=400)
        
        # Extract selected SW Part Numbers (distinct)
        selected_sw_part_numbers = list(set([
            v.sw_part_number for v in version_objs if v.sw_part_number
        ]))
        
        # Extract selected app_sw_versions (distinct)
        selected_app_sw_versions = list(set([
            v.app_sw_version for v in version_objs if v.app_sw_version
        ]))
        
        # Build query for test cases - filter by:
        # 1. Selected sheet
        # 2. Selected SW Part Numbers (strict filter)
        # 3. Selected app_sw_versions
        qs = TestCase.objects.filter(
            instance=active_instance,
            sheet_name=sheet_name,
            sw_part_number__in=selected_sw_part_numbers,
            app_sw_version__in=selected_app_sw_versions
        )
        
        # Get DISTINCT features
        features = list(
            qs.values_list('feature', flat=True)
            .distinct()
            .order_by('feature')
        )
        
        return JsonResponse({
            'ok': True,
            'features': [{'name': f} for f in features if f]
        })
    except Exception as e:
        log_error("export_views.py:get_features_for_selection_api", "Error loading features", {
            "error": str(e),
            "type": type(e).__name__,
            "sheet": sheet_name,
            "versions": version_ids
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading features: {str(e)}'
        }, status=500)


@login_required
def get_completed_features_for_selection_api(request):
    """
    TEMPORARY STUB.
    Required so Django imports succeed while
    Create New Instance logic is under development.
    """
    return JsonResponse({
        "ok": True,
        "features": []
    })


@login_required
def get_sw_part_numbers_for_sheet_api(request):
    """
    API: Get DISTINCT SW Part Numbers for a selected sheet.
    Used for Create New Instance flow - Step 2.
    Returns only SW Part Numbers (no versions).
    """
    try:
        active_instance = get_active_instance()
        sheet_name = request.GET.get('sheet', '')
        
        if not sheet_name:
            return JsonResponse({
                'ok': False,
                'error': 'Sheet name is required'
            }, status=400)
        
        # Get DISTINCT SW Part Numbers from TestCase for this sheet
        sw_part_numbers = list(
            TestCase.objects.filter(
                instance=active_instance,
                sheet_name=sheet_name
            )
            .exclude(sw_part_number='')
            .exclude(sw_part_number__isnull=True)
            .values_list('sw_part_number', flat=True)
            .distinct()
            .order_by('sw_part_number')
        )
        
        return JsonResponse({
            'ok': True,
            'sw_part_numbers': [{'name': sw} for sw in sw_part_numbers if sw]
        })
    except Exception as e:
        log_error("export_views.py:get_sw_part_numbers_for_sheet_api", "Error loading SW part numbers", {
            "error": str(e),
            "type": type(e).__name__,
            "sheet": sheet_name
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading SW part numbers: {str(e)}'
        }, status=500)


@login_required
def get_completed_features_for_sw_api(request):
    """
    API: Get ONLY completed features for selected sheet + SW Part Numbers.
    Returns DISTINCT features that are COMPLETED (all test cases have status IN ("PASS", "FAIL")).
    Used for Create New Instance flow - Step 3.
    
    FEATURE COMPLETION DEFINITION (FEATURE-SCOPED):
    A feature is COMPLETED if and ONLY if:
    - For the selected Sheet, SW Part Number, Version, Feature
    - ALL test cases have status IN ("PASS", "FAIL")
    - Checks ONLY the ACTIVE version for each SW Part Number (not all versions)
    """
    try:
        active_instance = get_active_instance()
        sheet_name = request.GET.get('sheet', '')
        sw_part_numbers = request.GET.getlist('sw_part_numbers')  # Can be multiple
        
        if not sheet_name:
            return JsonResponse({
                'ok': False,
                'error': 'Sheet name is required'
            }, status=400)
        
        if not sw_part_numbers:
            return JsonResponse({
                'ok': False,
                'error': 'At least one SW Part Number must be selected'
            }, status=400)
        
        # Clean SW Part Numbers
        sw_part_numbers = [sw.strip() for sw in sw_part_numbers if sw and sw.strip()]
        
        if not sw_part_numbers:
            return JsonResponse({
                'ok': False,
                'error': 'At least one valid SW Part Number must be selected'
            }, status=400)
        
        # FEATURE-SCOPED: Get features for selected sheet and SW Part Numbers
        # Only check ACTIVE versions (not all versions) - feature must be completed in active version
        from ..services import get_feature_completion
        from ..models import TestCaseSheet
        
        # Get active versions for each SW Part Number
        active_versions = {}
        for sw_part_number in sw_part_numbers:
            # Get active version for this SW Part Number
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                is_active=True
            ).order_by('-created_at').first()
            
            if version_obj:
                active_versions[sw_part_number] = version_obj
        
        if not active_versions:
            return JsonResponse({
                'ok': True,
                'features': [],
                'message': 'No active versions found for selected SW Part Numbers'
            })
        
        # Get all features for selected sheet and active versions only
        all_features = list(
            TestCase.objects.filter(
                instance=active_instance,
                sheet_name=sheet_name,
                sw_part_number__in=sw_part_numbers,
                app_sw_version__in=[v.app_sw_version for v in active_versions.values()]
            )
            .exclude(feature__isnull=True)
            .exclude(feature__exact="")
            .values_list('feature', flat=True)
            .distinct()
            .order_by('feature')
        )
        
        # Filter to only completed features (FEATURE-SCOPED validation)
        # A feature is completed if ALL test cases under that feature have status IN ("PASS", "FAIL")
        # Check ONLY active version for each SW Part Number
        # Show feature if it's completed for AT LEAST ONE selected SW Part Number
        completed_features = []
        for feature_name in all_features:
            if not feature_name:
                continue
            
            # Check completion for this feature per SW Part Number in active version only
            # Feature is shown if completed for AT LEAST ONE SW Part Number
            feature_completed_for_any_sw = False
            total_count = 0
            completed_count = 0
            
            # Check each SW Part Number separately
            for sw_part_number, version_obj in active_versions.items():
                # Get sheet object for this version
                sheet_obj = TestCaseSheet.objects.filter(
                    version=version_obj,
                    sheet_name=sheet_name
                ).first()
                
                if not sheet_obj:
                    # Sheet doesn't exist for this version - skip this SW Part Number
                    continue
                
                # Check feature completion using feature-scoped validation
                sw_total, sw_completed, sw_is_completed = get_feature_completion(
                    active_instance, version_obj, sheet_obj, feature_name
                )
                
                if sw_total == 0:
                    # No test cases for this feature in this SW Part Number - skip
                    continue
                
                total_count += sw_total
                completed_count += sw_completed
                
                if sw_is_completed:
                    # Feature is completed for this SW Part Number - include it
                    feature_completed_for_any_sw = True
            
            # Include feature if it's completed for AT LEAST ONE selected SW Part Number in active version
            if feature_completed_for_any_sw and total_count > 0:
                completed_features.append({
                    'name': feature_name,
                    'total_count': total_count,
                    'completed_count': completed_count,
                    'is_completed': True
                })
        
        return JsonResponse({
            'ok': True,
            'features': completed_features
        })
    except Exception as e:
        log_error("export_views.py:get_completed_features_for_sw_api", "Error loading completed features", {
            "error": str(e),
            "type": type(e).__name__,
            "sheet": sheet_name,
            "sw_part_numbers": sw_part_numbers
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading completed features: {str(e)}'
        }, status=500)


# =====================================================
# EXPORT FUNCTIONS
# =====================================================

@login_required
def export_excel(request):
    """
    NEW EXPORT: Export test cases to Excel.
    
    PERMISSIONS: Manager only (superuser)
    TESTERS CANNOT export Excel.
    
    Accepts payload:
    {
        "sheet": "<sheet_name>",
        "versions": [1, 2, 3, ...],  # Version IDs
        "features": ["Feature A", "Feature B", ...]
    }
    
    EXPORT RULES:
    - Export ONLY active instance (is_active=True)
    - Export ONLY most recent version per SW Part Number
    - Timestamp fixed at creation time (no DB writes during export)
    """
    # PERMISSION CHECK: Manager only
    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': 'Permission denied. Only managers can export Excel.'
        }, status=403)
    
    active_instance = get_active_instance()
    
    # Parse JSON payload
    sheet_name = ""
    version_ids = []
    selected_features = []
    
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            sheet_name = data.get('sheet', '')
            version_ids = data.get('versions', [])
            selected_features = data.get('features', [])
        except (json.JSONDecodeError, TypeError) as e:
            log_error("export_views.py:export_excel", "Error parsing JSON", {
                "error": str(e)
            }, exc_info=True)
            return JsonResponse({
                'ok': False,
                'error': 'Invalid JSON payload'
            }, status=400)
    
    # Validate required fields
    if not sheet_name:
        return JsonResponse({
            'ok': False,
            'error': 'Sheet name is required'
        }, status=400)
    
    if not version_ids:
        return JsonResponse({
            'ok': False,
            'error': 'At least one version must be selected'
        }, status=400)
    
    if not selected_features:
        return JsonResponse({
            'ok': False,
            'error': 'At least one feature must be selected'
        }, status=400)
    
    # Get version objects
    version_objs = list(TestCaseVersion.objects.filter(
        instance=active_instance,
        id__in=version_ids
    ))
    
    if not version_objs:
        return JsonResponse({
            'ok': False,
            'error': 'No valid versions found'
        }, status=400)
    
    # Build version_selections dict for excel_export module
    version_selections = {}
    for version_obj in version_objs:
        sw_num = version_obj.sw_part_number
        version = version_obj.app_sw_version
        if sw_num in version_selections:
            if isinstance(version_selections[sw_num], str):
                version_selections[sw_num] = [version_selections[sw_num], version]
            else:
                version_selections[sw_num].append(version)
        else:
            version_selections[sw_num] = version
    
    # Build workbook
    try:
        wb = build_testcase_export_workbook(
            sheet_filter=sheet_name,  # Filter by selected sheet
            sw="",  # Export ALL SW part numbers under this sheet
            app_sw_version="",
            feature="",  # Will filter by selected_features in the function
            query="",
            versions_list=None,
            latest_versions_only=False,
            version_selections=version_selections,
            selected_features=selected_features,  # Pass selected features
            selected_version_objs=version_objs,  # Pass version objects
        )
    except Exception as e:
        log_error("export_views.py:export_excel", "Error creating workbook", {
            "error": str(e),
            "type": type(e).__name__
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error creating export: {str(e)}'
        }, status=500)
    
    # EXPORT RULE: Fixed timestamp at creation time - no DB writes during export
    # Use fixed timestamp based on active instance creation time or current time (not from DB)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    feature_names_str = "_".join([f.replace(" ", "_") for f in selected_features[:3]])
    filename = f"TestCases_Export_{timestamp}_{sheet_name}_{feature_names_str}.xlsx"
    response["Content-Disposition"] = f"attachment; filename={filename}"
    wb.save(response)
    
    # NO DB WRITES during export - exports are READ-ONLY
    # Removed: ProjectOverview update for export timestamp
    
    return response


@login_required
def export_html(request):
    """
    NEW EXPORT: Export test cases as standalone offline HTML report.
    
    PERMISSIONS: Manager only (superuser)
    TESTERS CANNOT export HTML.
    
    Accepts payload:
    {
        "sheet": "<sheet_name>",
        "versions": [1, 2, 3, ...],  # Version IDs
        "features": ["Feature A", "Feature B", ...]
    }
    
    EXPORT RULES:
    - Export ONLY active instance (is_active=True)
    - Export ONLY most recent version per SW Part Number
    - Timestamp fixed at creation time (no DB writes during export)
    
    Returns a downloadable .html file that works completely offline.
    All CSS, JS, and chart data are inlined.
    """
    # PERMISSION CHECK: Manager only
    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': 'Permission denied. Only managers can export HTML.'
        }, status=403)
    
    from django.template.loader import render_to_string
    
    active_instance = get_active_instance()
    
    # Parse JSON payload
    sheet_name = ""
    version_ids = []
    selected_features = []
    
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            sheet_name = data.get('sheet', '')
            version_ids = data.get('versions', [])
            selected_features = data.get('features', [])
        except (json.JSONDecodeError, TypeError) as e:
            log_error("export_views.py:export_html", "Error parsing JSON", {
                "error": str(e)
            }, exc_info=True)
            return JsonResponse({
                'ok': False,
                'error': 'Invalid JSON payload'
            }, status=400)
    
    # Validate required fields
    if not sheet_name:
        return JsonResponse({
            'ok': False,
            'error': 'Sheet name is required'
        }, status=400)
    
    if not version_ids:
        return JsonResponse({
            'ok': False,
            'error': 'At least one version must be selected'
        }, status=400)
    
    if not selected_features:
        return JsonResponse({
            'ok': False,
            'error': 'At least one feature must be selected'
        }, status=400)
    
    # EXPORT RULE: Export ONLY active instance (is_active=True)
    # Get version objects - must be from active instance and be active versions
    version_objs = list(TestCaseVersion.objects.filter(
        instance=active_instance,
        id__in=version_ids,
        is_active=True  # Only export active versions
    ))
    
    if not version_objs:
        return JsonResponse({
            'ok': False,
            'error': 'No valid active versions found'
        }, status=400)
    
    # EXPORT RULE: Export ONLY most recent version per SW Part Number
    # Filter to keep only the most recent version for each SW Part Number
    sw_version_map = {}
    for version_obj in version_objs:
        sw_num = version_obj.sw_part_number
        if sw_num not in sw_version_map:
            sw_version_map[sw_num] = version_obj
        else:
            # Compare creation dates - keep the most recent
            if version_obj.created_at > sw_version_map[sw_num].created_at:
                sw_version_map[sw_num] = version_obj
    
    # Use only the most recent version per SW
    version_objs = list(sw_version_map.values())
    
    # Get test cases for selected sheet, versions, and features
    qs = TestCase.objects.filter(
        instance=active_instance,
        sheet_name=sheet_name,
        feature__in=selected_features
    )
    
    # Filter by selected versions
    from functools import reduce
    from operator import or_
    version_filters = reduce(
        or_,
        [
            Q(
                sw_part_number=version_obj.sw_part_number,
                app_sw_version=version_obj.app_sw_version
            )
            for version_obj in version_objs
        ]
    )
    qs = qs.filter(version_filters)
    
    test_cases = list(qs.select_related())
    
    # Get executions for these test cases
    test_case_ids = [tc.id for tc in test_cases]
    executions = TestExecution.objects.filter(
        instance=active_instance,
        test_case_id__in=test_case_ids,
        version__in=version_objs
    ).select_related('test_case', 'user', 'version')
    
    # Create execution map
    execution_map = {}
    for exec_obj in executions:
        execution_map[exec_obj.test_case_id] = exec_obj
    
    # Attach executions to test cases
    for tc in test_cases:
        tc.exec = execution_map.get(tc.id)
    
    # Calculate statistics
    total_count = len(test_cases)
    executed_count = len([tc for tc in test_cases if tc.exec and tc.exec.status])
    passed_count = len([tc for tc in test_cases if tc.exec and tc.exec.status and tc.exec.status.upper() == 'PASS'])
    failed_count = len([tc for tc in test_cases if tc.exec and tc.exec.status and tc.exec.status.upper() == 'FAIL'])
    not_executed_count = total_count - executed_count
    
    # Get Project Overview
    project_overview = ProjectOverview.objects.filter(instance=active_instance).exclude(
        key__in=[PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP]
    ).first()
    
    # Get all SW part numbers for selected versions
    sw_part_numbers = sorted(set([v.sw_part_number for v in version_objs]))
    app_sw_versions = sorted(set([v.app_sw_version for v in version_objs]))
    
    # Read CSS and JS files to inline
    css_content = ""
    js_content = ""
    try:
        css_path = os.path.join(settings.BASE_DIR, 'testmanager', 'static', 'testmanager', 'css', 'export_html.css')
        if os.path.exists(css_path):
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
    except Exception as e:
        log_error("export_views.py:export_html", "Error reading CSS file", {"error": str(e)}, exc_info=True)
    
    try:
        js_path = os.path.join(settings.BASE_DIR, 'testmanager', 'static', 'testmanager', 'js', 'export_html.js')
        if os.path.exists(js_path):
            with open(js_path, 'r', encoding='utf-8') as f:
                js_content = f.read()
    except Exception as e:
        log_error("export_views.py:export_html", "Error reading JS file", {"error": str(e)}, exc_info=True)
    
    # Prepare chart data as JSON
    status_labels_json = json.dumps(["PASS", "FAIL", "NOT EXECUTED"])
    status_values_json = json.dumps([passed_count, failed_count, not_executed_count])
    sheet_labels_json = json.dumps(["Total", "Executed", "Passed"])
    sheet_values_json = json.dumps([total_count, executed_count, passed_count])
    bar_mode_json = json.dumps("status_overview")
    
    # EMBED ALL DATA AS JSON FOR OFFLINE FILTERING
    # Prepare all test case data as JSON for offline filtering
    report_data = []
    for tc in test_cases:
        exec_obj = execution_map.get(tc.id)
        report_data.append({
            'sl_no': tc.sl_no or '',
            'sw_part_number': tc.sw_part_number or '',
            'feature': tc.feature or '',
            'requirement_id': get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id),
            'requirement_description': tc.requirement_description or '',
            'test_case_id': tc.base_test_case_id or tc.test_case_id,
            'test_case_summary': tc.test_case_summary or '',
            'pre_conditions': tc.pre_conditions or '',
            'inputs': tc.inputs or '',
            'periodic_time': tc.periodic_time or '',
            'test_steps': tc.test_steps or '',
            'expected_result': tc.expected_result or '',
            'status': exec_obj.status if exec_obj and exec_obj.status else '',
            'reports': exec_obj.reports if exec_obj and exec_obj.reports else '',
            'comments': exec_obj.comments if exec_obj and exec_obj.comments else '',
            'sheet_name': tc.sheet_name or '',
        })
    
    # Get all unique values for dropdowns
    all_sheets = sorted(set([tc.sheet_name for tc in test_cases if tc.sheet_name]))
    all_sw_part_numbers = sorted(set([tc.sw_part_number for tc in test_cases if tc.sw_part_number]))
    all_features = sorted(set([tc.feature for tc in test_cases if tc.feature]))
    
    report_data_json = json.dumps(report_data, default=str)
    all_sheets_json = json.dumps(all_sheets)
    all_sw_part_numbers_json = json.dumps(all_sw_part_numbers)
    all_features_json = json.dumps(all_features)
    
    # Build context for standalone HTML template
    context = {
        "tests": test_cases,
        "selected_sheet": sheet_name,
        "selected_sw": ", ".join(sw_part_numbers) if len(sw_part_numbers) > 1 else (sw_part_numbers[0] if sw_part_numbers else ""),
        "selected_version": ", ".join(app_sw_versions) if len(app_sw_versions) > 1 else (app_sw_versions[0] if app_sw_versions else ""),
        "selected_feature": ", ".join(selected_features) if len(selected_features) > 1 else (selected_features[0] if selected_features else ""),
        "status_labels": ["PASS", "FAIL", "NOT EXECUTED"],
        "status_values": [passed_count, failed_count, not_executed_count],
        "status_labels_json": status_labels_json,
        "status_values_json": status_values_json,
        "sheet_labels": ["Total", "Executed", "Passed"],
        "sheet_values": [total_count, executed_count, passed_count],
        "sheet_labels_json": sheet_labels_json,
        "sheet_values_json": sheet_values_json,
        "bar_mode": "status_overview",
        "bar_mode_json": bar_mode_json,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_executed_count": not_executed_count,
        "total_executed": executed_count,
        "total_test_cases": total_count,
        "pass_percentage": round((passed_count / total_count * 100) if total_count > 0 else 0, 2),
        "fail_percentage": round((failed_count / total_count * 100) if total_count > 0 else 0, 2),
        "not_exec_percentage": round((not_executed_count / total_count * 100) if total_count > 0 else 0, 2),
        "executed_percentage": round((executed_count / total_count * 100) if total_count > 0 else 0, 2),
        "project_code": project_overview.project_code if project_overview else "",
        "vcu_platform": project_overview.vcu_platform if project_overview else "",
        "hw_part_number": project_overview.hardware_part_number if project_overview else "",
        "software_part_number": ", ".join(sw_part_numbers),
        "project_stage": project_overview.project_stage if project_overview else "",
        "developer": project_overview.developer if project_overview else "",
        "test_engineer": project_overview.test_engineer if project_overview else "",
        "app_sw_version": ", ".join(app_sw_versions),
        "bootloader_sw_version": project_overview.bootloader_sw_version if project_overview else "",
        "checksum_value": project_overview.checksum_value if project_overview else "",
        "dbc_test_it": project_overview.dbc_test_it if project_overview else "",
        "now": timezone.now(),
        "inline_css": css_content,
        "inline_js": js_content,
        # Offline filtering data
        "report_data_json": report_data_json,
        "all_sheets_json": all_sheets_json,
        "all_sw_part_numbers_json": all_sw_part_numbers_json,
        "all_features_json": all_features_json,
    }
    
    # Generate standalone HTML
    html_content = render_to_string("testmanager/export_html_standalone.html", context)
    
    # Create html_reports directory if it doesn't exist
    reports_dir = os.path.join(settings.BASE_DIR, 'html_reports')
    os.makedirs(reports_dir, exist_ok=True)
    
    # EXPORT RULE: Fixed timestamp at creation time - no DB writes during export
    # Use fixed timestamp (not from DB)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_sheet = sheet_name.replace(' ', '_').replace('/', '_')[:50]
    safe_sw = "_".join(sw_part_numbers[:2]).replace(' ', '_').replace('/', '_')[:30] if sw_part_numbers else "all"
    filename = f"{safe_sheet}_{safe_sw}_{timestamp}.html"
    file_path = os.path.join(reports_dir, filename)
    
    # Write HTML file
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception as e:
        log_error("export_views.py:export_html", "Error writing HTML file", {"error": str(e)}, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error saving HTML file: {str(e)}'
        }, status=500)
    
    # Return as downloadable file
    try:
        response = FileResponse(
            open(file_path, 'rb'),
            content_type='text/html',
            as_attachment=True,
            filename=filename
        )
        # File will be closed automatically by FileResponse
        return response
    except Exception as e:
        log_error("export_views.py:export_html", "Error creating file response", {"error": str(e)}, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error creating download: {str(e)}'
        }, status=500)


@login_required
def export_html_snapshot(request, export_id):
    """
    View historical snapshot of test executions by export_id.
    This function is kept for backward compatibility with existing snapshots.
    """
    try:
        snapshot = TestExecutionSnapshot.objects.get(export_id=export_id)
    except TestExecutionSnapshot.DoesNotExist:
        messages.error(request, f"Snapshot '{export_id}' not found.")
        return redirect("testcase_list")
    
    # Reconstruct test cases from snapshot data
    execution_data = snapshot.execution_data or []
    
    # Create mock test case objects from snapshot data
    class MockTestCase:
        def __init__(self, data):
            self.id = data.get('test_case_db_id', 0)
            self.test_case_id = data.get('test_case_id', '')
            self.base_test_case_id = data.get('base_test_case_id', '')
            self.sheet_name = data.get('sheet_name', '')
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
            self.status = data.get('status', '')
            self.reports = data.get('reports', '')
            self.comments = data.get('comments', '')
            self.display_test_case_id = self.base_test_case_id or self.test_case_id
            self.display_requirement_id = get_requirement_id(self.base_test_case_id or self.test_case_id, self.requirement_id)
            self.exec = None
    
    class MockExecution:
        def __init__(self, data):
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
    
    # Reconstruct test cases
    test_cases = []
    for exec_data in execution_data:
        mock_tc = MockTestCase(exec_data)
        mock_exec = MockExecution(exec_data)
        mock_tc.exec = mock_exec
        test_cases.append(mock_tc)
    
    # Sort by id
    test_cases.sort(key=lambda tc: (tc.sheet_name or '', tc.id))
    
    # Calculate statistics
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
    
    # Status distribution
    status_labels = ["PASS", "FAIL", "NOT EXECUTED"]
    status_values = [passed_count, failed_count, not_executed_count]
    
    # Bar chart data
    sheet_labels = ["Total Test Cases", "Pass", "Fail", "Not Executed"]
    sheet_values = [total_test_cases, passed_count, failed_count, not_executed_count]
    
    # Get filter lists
    # FIXED: Get sheet names ONLY from SheetMeta (source of truth)
    from ..models import SheetMeta
    sheet_names = list(
        SheetMeta.objects.all().order_by("sheet_name").values_list("sheet_name", flat=True)
    )
    
    sw_list = []
    if snapshot.sw_part_number:
        sw_set = set()
        for tc in test_cases:
            if tc.sw_part_number:
                sw_set.add(str(tc.sw_part_number).strip())
        sw_list = sorted([s for s in sw_set if s])
    
    version_list = []
    if snapshot.app_sw_version:
        version_set = set()
        for tc in test_cases:
            if tc.app_sw_version:
                version_set.add(tc.app_sw_version)
        version_list = sorted(list(version_set))
    
    # Get Project Overview
    project_overview = ProjectOverview.objects.filter(instance=snapshot.instance).exclude(
        key__in=[PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP]
    ).first()
    
    context = {
        "tests": test_cases,
        "selected_sheet": snapshot.sheet_name,
        "selected_sw": snapshot.sw_part_number,
        "selected_version": snapshot.app_sw_version,
        "selected_feature": "",
        "search_query": "",
        "status_labels": status_labels,
        "status_values": status_values,
        "sheet_labels": sheet_labels,
        "sheet_values": sheet_values,
        "bar_mode": "status_overview",
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_executed_count": not_executed_count,
        "total_executed": total_executed,
        "total_test_cases": total_test_cases,
        "pass_percentage": round(pass_percentage, 2),
        "fail_percentage": round(fail_percentage, 2),
        "not_exec_percentage": round(not_exec_percentage, 2),
        "executed_percentage": round(executed_percentage, 2),
        "trend_labels": [],
        "trend_data": [],
        "sheets": sheet_names,
        "sw_list": sw_list,
        "version_list": version_list,
        "all_completed": True,
        "executed_count": total_executed,
        "total_count": total_test_cases,
        "snapshot_created": snapshot,
        "is_snapshot": True,
        "current_export_id": export_id,
        "snapshot_name": snapshot.snapshot_name,
        "exported_at": snapshot.exported_at,
        "exported_by": snapshot.exported_by,
        "now": snapshot.exported_at,
        "recent_snapshots": TestExecutionSnapshot.objects.exclude(export_id=export_id).order_by('-exported_at')[:10],
        "project_code": project_overview.project_code if project_overview else "",
        "vcu_platform": project_overview.vcu_platform if project_overview else "",
        "hw_part_number": project_overview.hardware_part_number if project_overview else "",
        "software_part_number": snapshot.sw_part_number or "",
        "project_stage": project_overview.project_stage if project_overview else "",
        "developer": project_overview.developer if project_overview else "",
        "test_engineer": project_overview.test_engineer if project_overview else "",
        "app_sw_version": snapshot.app_sw_version or "",
        "bootloader_sw_version": project_overview.bootloader_sw_version if project_overview else "",
        "checksum_value": project_overview.checksum_value if project_overview else "",
        "dbc_test_it": project_overview.dbc_test_it if project_overview else "",
    }
    
    html_content = render(request, "testmanager/export_html.html", context)
    return html_content
