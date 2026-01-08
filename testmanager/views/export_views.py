"""
Export Views

This module contains views for exporting test cases to Excel and HTML formats,
including snapshot functionality for historical exports.
"""

from datetime import datetime
from functools import reduce
from operator import or_
import os
import json
import base64
import urllib.request
import urllib.error

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Case, When, IntegerField, Value
from django.db.models.functions import Cast
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.conf import settings

from ..models import (
    TestExecution, TestCase, SheetMeta, ProjectOverview,
    TestExecutionSnapshot, TestCaseVersion
)
from ..services import get_active_instance
from ..excel_export import build_testcase_export_workbook, _get_most_recently_created_version
from ..constants import (
    PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
)
from ..decorators import manager_required, is_manager
from ..version_service import (
    sort_test_cases_by_version,
    parse_version
)
from ..services import (
    check_all_tests_completed,
    check_feature_completion,
)
from ..utils import get_requirement_id
from ..logging_utils import log_error


# RISK REMOVAL: Using service function instead of local _check_all_tests_completed()
_check_all_tests_completed = check_all_tests_completed


def _is_version_exported(sw_part_number, app_sw_version, active_instance):
    """
    Check if a version has already been exported.
    
    A version is considered exported if:
    - There's a TestExecutionSnapshot with matching app_sw_version
    - For project-wide exports: sw_part_number is empty in snapshot
    - For SW-specific exports: sw_part_number matches
    
    Args:
        sw_part_number: SW Part Number (can be empty for project-wide check)
        app_sw_version: Application SW Version
        active_instance: TestInstance object
        
    Returns:
        bool: True if version is already exported, False otherwise
    """
    # Check for project-wide export (sw_part_number empty in snapshot)
    project_wide_snapshot = TestExecutionSnapshot.objects.filter(
        instance=active_instance,
        sw_part_number="",  # Empty indicates project-wide
        app_sw_version=app_sw_version
    ).exists()
    
    if project_wide_snapshot:
        return True
    
    # Check for SW-specific export (if sw_part_number provided)
    if sw_part_number:
        sw_specific_snapshot = TestExecutionSnapshot.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version
        ).exists()
        
        if sw_specific_snapshot:
            return True
    
    return False


def _is_version_completed(sw_part_number, app_sw_version, active_instance):
    """
    Check if a version has 100% execution completion.
    
    Args:
        sw_part_number: SW Part Number
        app_sw_version: Application SW Version
        active_instance: TestInstance object
        
    Returns:
        tuple: (is_completed: bool, executed_count: int, total_count: int)
    """
    from ..models import TestCaseVersion
    # Look up the version object
    version_obj = None
    if app_sw_version:
        try:
            version_obj = TestCaseVersion.objects.get(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=app_sw_version
            )
        except TestCaseVersion.DoesNotExist:
            version_obj = None
    
    return check_all_tests_completed(
        sheet_filter="",
        sw=sw_part_number,
        version_obj=version_obj
    )


def _get_exportable_versions(active_instance, user=None):
    """
    Get list of UNIQUE versions (across ALL SW part numbers) that are:
    - Completed (100% execution) across ALL SW part numbers
    - NOT yet exported
    - Accessible by user (based on permissions)
    
    VERSION-ONLY EXPORT: Returns unique app_sw_version values that exist across
    the entire project. When a version is selected, ALL SW part numbers with
    that version will be exported.
    
    Args:
        active_instance: TestInstance object
        user: User object for permission filtering (optional)
    
    Returns:
        list: List of dicts with keys: app_sw_version, sw_part_numbers (list), executed_count, total_count
    """
    from ..version_service import get_versions_for_user, can_user_access_version
    
    # Collect all versions grouped by app_sw_version
    version_map = {}  # {app_sw_version: {'sw_part_numbers': [], 'version_objs': [], 'total_executed': 0, 'total_count': 0}}
    
    # Get all versions accessible by user (if user provided)
    if user:
        # Get unique SW part numbers first
        sw_part_numbers = list(
            TestCaseVersion.objects.filter(instance=active_instance)
            .values_list('sw_part_number', flat=True).distinct()
        )
        
        # Get versions for each SW part number that user can access
        all_accessible_versions = []
        for sw_num in sw_part_numbers:
            if sw_num:
                versions = get_versions_for_user(user, active_instance, sw_num)
                all_accessible_versions.extend(versions)
    else:
        # No user filtering - get all versions (backward compatibility)
        all_accessible_versions = TestCaseVersion.objects.filter(
            instance=active_instance
        ).select_related('instance')
    
    # Group versions by app_sw_version
    for version_obj in all_accessible_versions:
        app_sw_version = version_obj.app_sw_version
        
        # Skip if user provided and cannot access this version
        if user and not can_user_access_version(user, version_obj):
            continue
        
        # Initialize version entry if not exists
        if app_sw_version not in version_map:
            version_map[app_sw_version] = {
                'sw_part_numbers': [],
                'version_objs': [],
                'total_executed': 0,
                'total_count': 0,
            }
        
        # Add this SW part number and version object
        if version_obj.sw_part_number not in version_map[app_sw_version]['sw_part_numbers']:
            version_map[app_sw_version]['sw_part_numbers'].append(version_obj.sw_part_number)
        if version_obj not in version_map[app_sw_version]['version_objs']:
            version_map[app_sw_version]['version_objs'].append(version_obj)
    
    # Check each unique version for exportability
    exportable_versions = []
    for app_sw_version, version_info in version_map.items():
        # Check if this version is already exported (any SW part number with this version)
        is_exported = False
        for sw_num in version_info['sw_part_numbers']:
            if _is_version_exported(sw_num, app_sw_version, active_instance):
                is_exported = True
                break
        
        if is_exported:
            continue
        
        # Check completion status across ALL SW part numbers for this version
        total_executed = 0
        total_count = 0
        all_completed = True
        
        for sw_num in version_info['sw_part_numbers']:
            is_completed, executed_count, count = _is_version_completed(
                sw_num, app_sw_version, active_instance
            )
            total_executed += executed_count
            total_count += count
            if not is_completed or count == 0:
                all_completed = False
        
        # Only include if ALL SW part numbers are 100% completed
        if all_completed and total_count > 0:
            exportable_versions.append({
                'app_sw_version': app_sw_version,
                'sw_part_numbers': sorted(version_info['sw_part_numbers']),
                'version_objs': version_info['version_objs'],
                'executed_count': total_executed,
                'total_count': total_count,
            })
    
    # Sort by version string (newest first based on creation date of first version_obj)
    exportable_versions.sort(
        key=lambda x: max(v.created_at for v in x['version_objs']) if x['version_objs'] else datetime.min,
        reverse=True
    )
    
    return exportable_versions


def _create_snapshot_for_version(sw_part_number, app_sw_version, active_instance, user=None):
    """
    Create snapshot for a specific version WITHOUT resetting execution data.
    
    This function:
    - Works ONLY on the specified version
    - Validates all tests are completed before creating snapshot
    - Preserves all execution data (does NOT reset anything)
    - Creates read-only historical reference
    
    Args:
        sw_part_number: SW Part Number
        app_sw_version: Application SW Version
        active_instance: TestInstance object
        user: User who created the snapshot
        
    Returns:
        TestExecutionSnapshot or None
    """
    # Get version object
    version_obj = TestCaseVersion.objects.filter(
        instance=active_instance,
        sw_part_number=sw_part_number,
        app_sw_version=app_sw_version
    ).first()
    
    if not version_obj:
        return None
    
    # Check if all test cases of this version are executed
    all_completed, executed_count, total_count = check_all_tests_completed(
        sheet_filter="",
        sw=sw_part_number,
        version_obj=version_obj
    )
    
    if not all_completed or total_count == 0:
        return None
    
    # Get all test cases for this SW part number (version filtering done via executions)
    test_case_ids = list(
        TestCase.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number
        ).values_list('id', flat=True)
    )
    
    # Get executions for this version
    executions = TestExecution.objects.filter(
        instance=active_instance,
        version=version_obj,
        test_case_id__in=test_case_ids
    ).select_related('test_case', 'user')
    
    # Prepare snapshot data
    execution_data = []
    total_passed = 0
    total_failed = 0
    total_not_executed = 0
    
    for exec in executions:
        tc = exec.test_case
        display_requirement_id = get_requirement_id(tc.test_case_id, tc.requirement_id)
        
        exec_dict = {
            'test_case_id': tc.test_case_id,  # Versioned ID (for internal reference)
            'base_test_case_id': tc.base_test_case_id or tc.test_case_id,  # Base ID (for display)
            'test_case_db_id': tc.id,
            'sheet_name': tc.sheet_name,
            'sl_no': "",
            'sw_part_number': tc.sw_part_number,
            'feature': tc.feature,
            'requirement_id': display_requirement_id,
            'requirement_description': tc.requirement_description,
            'test_case_summary': tc.test_case_summary,
            'pre_conditions': tc.pre_conditions,
            'inputs': tc.inputs,
            'periodic_time': tc.periodic_time,
            'test_steps': tc.test_steps,
            'expected_result': tc.expected_result,
            'app_sw_version': app_sw_version,
            'status': tc.status,
            'reports': tc.reports,
            'comments': tc.comments,
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
        
        # Count by status
        status_upper = (exec.status or "").upper()
        if status_upper == "PASS":
            total_passed += 1
        elif status_upper == "FAIL":
            total_failed += 1
        else:
            total_not_executed += 1
    
    # Generate export_id
    last_snapshot = TestExecutionSnapshot.objects.order_by('-exported_at').first()
    if last_snapshot and last_snapshot.export_id:
        try:
            last_num = int(last_snapshot.export_id.split('_')[-1])
            export_id = f"export_{last_num + 1}"
        except (ValueError, IndexError):
            export_id = f"export_{TestExecutionSnapshot.objects.count() + 1}"
    else:
        export_id = "export_1"
    
    # Create snapshot name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"Export_{timestamp}_{sw_part_number}_{app_sw_version}"
    
    # Create snapshot
    snapshot = TestExecutionSnapshot.objects.create(
        instance=active_instance,
        export_id=export_id,
        snapshot_name=snapshot_name,
        sheet_name="",
        sw_part_number=sw_part_number,
        app_sw_version=app_sw_version,
        execution_data=execution_data,
        exported_by=user,
        total_test_cases=total_count,
        total_executed=len(execution_data),
        total_passed=total_passed,
        total_failed=total_failed,
        total_not_executed=total_not_executed,
    )
    
    # Store last export timestamp
    ProjectOverview.objects.update_or_create(
        key=PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
        defaults={"value": timezone.now().isoformat()}
    )
    
    return snapshot


def _create_snapshot_for_version_project_wide(app_sw_version, sw_part_numbers, active_instance, user=None):
    """
    Create snapshot for a version across ALL SW part numbers (project-wide export).
    
    This function:
    - Collects ALL executions for the specified version across ALL SW part numbers
    - Validates all tests are completed before creating snapshot
    - Preserves all execution data (does NOT reset anything)
    - Creates read-only historical reference
    
    Args:
        app_sw_version: Application SW Version (selected by user)
        sw_part_numbers: List of SW Part Numbers that have this version
        active_instance: TestInstance object
        user: User who created the snapshot
        
    Returns:
        TestExecutionSnapshot or None
    """
    # Get all version objects for this app_sw_version
    version_objs = TestCaseVersion.objects.filter(
        instance=active_instance,
        app_sw_version=app_sw_version,
        sw_part_number__in=sw_part_numbers
    )
    
    if not version_objs.exists():
        return None
    
    # Collect all test case IDs across all SW part numbers
    all_test_case_ids = list(
        TestCase.objects.filter(
            instance=active_instance,
            sw_part_number__in=sw_part_numbers
        ).values_list('id', flat=True)
    )
    
    # Get executions for this version across all SW part numbers
    executions = TestExecution.objects.filter(
        instance=active_instance,
        version__in=version_objs,
        test_case_id__in=all_test_case_ids
    ).select_related('test_case', 'user', 'version')
    
    # Prepare snapshot data
    execution_data = []
    total_passed = 0
    total_failed = 0
    total_not_executed = 0
    
    for exec in executions:
        tc = exec.test_case
        display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
        
        exec_dict = {
            'test_case_id': tc.test_case_id,
            'base_test_case_id': tc.base_test_case_id or tc.test_case_id,
            'test_case_db_id': tc.id,
            'sheet_name': tc.sheet_name,
            'sl_no': "",
            'sw_part_number': tc.sw_part_number,
            'feature': tc.feature,
            'requirement_id': display_requirement_id,
            'requirement_description': tc.requirement_description,
            'test_case_summary': tc.test_case_summary,
            'pre_conditions': tc.pre_conditions,
            'inputs': tc.inputs,
            'periodic_time': tc.periodic_time,
            'test_steps': tc.test_steps,
            'expected_result': tc.expected_result,
            'app_sw_version': app_sw_version,
            'status': tc.status,
            'reports': tc.reports,
            'comments': tc.comments,
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
        
        # Count by status
        status_upper = (exec.status or "").upper()
        if status_upper == "PASS":
            total_passed += 1
        elif status_upper == "FAIL":
            total_failed += 1
        else:
            total_not_executed += 1
    
    # Calculate totals
    total_test_cases = len(set(all_test_case_ids))
    total_executed = len(execution_data)
    
    # Generate export_id
    last_snapshot = TestExecutionSnapshot.objects.order_by('-exported_at').first()
    if last_snapshot and last_snapshot.export_id:
        try:
            last_num = int(last_snapshot.export_id.split('_')[-1])
            export_id = f"export_{last_num + 1}"
        except (ValueError, IndexError):
            export_id = f"export_{TestExecutionSnapshot.objects.count() + 1}"
    else:
        export_id = "export_1"
    
    # Create snapshot name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sw_count = len(sw_part_numbers)
    snapshot_name = f"Export_{timestamp}_{app_sw_version}"
    if sw_count > 1:
        snapshot_name += f"_{sw_count}SW"
    
    # Create snapshot (empty sw_part_number indicates project-wide)
    snapshot = TestExecutionSnapshot.objects.create(
        instance=active_instance,
        export_id=export_id,
        snapshot_name=snapshot_name,
        sheet_name="",  # All sheets
        sw_part_number="",  # All SW part numbers (empty = project-wide)
        app_sw_version=app_sw_version,
        execution_data=execution_data,
        exported_by=user,
        total_test_cases=total_test_cases,
        total_executed=total_executed,
        total_passed=total_passed,
        total_failed=total_failed,
        total_not_executed=total_not_executed,
    )
    
    # Store last export timestamp
    ProjectOverview.objects.update_or_create(
        key=PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
        defaults={"value": timezone.now().isoformat()}
    )
    
    return snapshot


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
    
    # CRITICAL: Filter test cases using version FK, not legacy app_sw_version CharField
    # Resolve all active versions using explicit FK
    from ..models import TestCaseVersion
    version_ids = []
    for sw_num, version_val in latest_versions.items():
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_num,
            app_sw_version=version_val,
            is_active=True
        ).first()
        if version_obj:
            version_ids.append(version_obj.id)
    
    if not version_ids:
        # No valid versions to snapshot
        return None
    
    # ==================================================
    # 2. VALIDATE COMPLETION BEFORE SNAPSHOT
    # ==================================================
    # Check if all test cases of latest version are executed (active instance only)
    test_case_ids = list(qs.values_list('id', flat=True))
    executions = TestExecution.objects.filter(
        instance=active_instance, 
        test_case_id__in=test_case_ids
    ).select_related("version")
    
    # CRITICAL: Filter executions using version FK
    executions = executions.filter(version_id__in=version_ids)  # Use explicit FK
    
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
        # Derive requirement_id from base_test_case_id if empty
        display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
        
        # Save complete test case data + execution data
        exec_dict = {
            # Test Case Details (ALL fields)
            'test_case_id': tc.test_case_id,  # Versioned ID (for internal reference)
            'base_test_case_id': tc.base_test_case_id or tc.test_case_id,  # Base ID (for display)
            'test_case_db_id': tc.id,  # Store DB ID for reference
            'sheet_name': tc.sheet_name,
            'sl_no': "",  # STRICT: sl_no does NOT exist - removed after hierarchy refactor
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
                    # STRICT: sl_no does NOT exist - removed after hierarchy refactor
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


def _download_and_cache_library(url, cache_dir=None):
    """
    Download a library from CDN and cache it locally for offline use.
    
    Args:
        url: CDN URL to download from
        cache_dir: Directory to cache files (defaults to html_reports/.cache)
        
    Returns:
        str: Content of the library file, or empty string if download fails
    """
    if cache_dir is None:
        cache_dir = os.path.join(settings.BASE_DIR, 'html_reports', '.cache')
    
    os.makedirs(cache_dir, exist_ok=True)
    
    # Create cache filename from URL
    cache_filename = url.split('/')[-1].split('?')[0]
    cache_path = os.path.join(cache_dir, cache_filename)
    
    # Try to read from cache first
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            pass
    
    # Download from CDN
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            content = response.read().decode('utf-8')
            # Cache it for future use
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception:
                pass  # Continue even if caching fails
            return content
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        log_error("export_views.py:_download_and_cache_library", "Error downloading library", 
                 {"url": url, "error": str(e)}, exc_info=True)
        # Try to return cached version even if download fails
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                pass
        return ""


def _get_inline_libraries():
    """
    Download and return Bootstrap CSS, Bootstrap JS, Chart.js, and Bootstrap Icons CSS
    for inline embedding in offline HTML files.
    
    Returns:
        dict: {
            'bootstrap_css': str,
            'bootstrap_js': str,
            'chart_js': str,
            'bootstrap_icons_css': str
        }
    """
    return {
        'bootstrap_css': _download_and_cache_library('https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css'),
        'bootstrap_js': _download_and_cache_library('https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js'),
        'chart_js': _download_and_cache_library('https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'),
        'bootstrap_icons_css': _download_and_cache_library('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css'),
    }


def generate_offline_html_report(snapshot, version_selections=None, all_snapshots=None):
    """
    Generate a standalone offline HTML report from a TestExecutionSnapshot.
    
    This function:
    - Accepts TestExecutionSnapshot instance (primary snapshot)
    - Optionally accepts version_selections dict and all_snapshots list for multi-SW exports
    - Renders HTML using snapshot.execution_data (NOT live DB)
    - INLINES all CSS and JS into ONE HTML file
    - REMOVES all Django template tags and static references
    - Embeds snapshot.execution_data as JSON inside the HTML
    - Implements pure JavaScript dropdown filtering (no backend calls)
    - Saves to /html_reports/ directory with timestamp filename: YYYY-MM-DD_HH-MM-SS_TestExecutionReport.html
    
    Args:
        snapshot: TestExecutionSnapshot instance (primary snapshot)
        version_selections: Dict mapping SW Part Number to selected version (optional)
        all_snapshots: List of all snapshots for multi-SW export (optional)
        
    Returns:
        str: Path to saved HTML file, or None if failed
    """
    try:
        # Combine execution data from all snapshots if provided
        if all_snapshots:
            execution_data = []
            for snap in all_snapshots:
                execution_data.extend(snap.execution_data or [])
        else:
            execution_data = snapshot.execution_data or []
        
        # Calculate statistics from snapshot
        passed_count = snapshot.total_passed
        failed_count = snapshot.total_failed
        not_relevant_count = 0  # Calculate from data
        not_executed_count = snapshot.total_not_executed
        total_test_cases = snapshot.total_test_cases
        total_executed = passed_count + failed_count
        
        # Count not relevant from execution data
        for exec_data in execution_data:
            status_upper = (exec_data.get('execution_status', '') or '').upper()
            if status_upper in ("NOT RELEVANT", "NOT_RELEVANT"):
                not_relevant_count += 1
        
        # Calculate percentages
        pass_percentage = (passed_count / total_test_cases * 100) if total_test_cases > 0 else 0
        fail_percentage = (failed_count / total_test_cases * 100) if total_test_cases > 0 else 0
        not_exec_percentage = (not_executed_count / total_test_cases * 100) if total_test_cases > 0 else 0
        executed_percentage = (total_executed / total_test_cases * 100) if total_test_cases > 0 else 0
        
        # Build filter lists from execution data
        sheet_names = sorted(set([d.get('sheet_name', '') for d in execution_data if d.get('sheet_name')]))
        sw_list = sorted(set([d.get('sw_part_number', '') for d in execution_data if d.get('sw_part_number')]))
        
        # Build feature list (if sheet and sw are selected)
        feature_set = set()
        for d in execution_data:
            if d.get('feature'):
                feature_set.add(d.get('feature'))
        feature_list = sorted(list(feature_set))
        
        # Status distribution for pie chart
        status_labels = ["PASS", "FAIL", "NOT RELEVANT", "NOT EXECUTED"]
        status_values = [passed_count, failed_count, not_relevant_count, not_executed_count]
        
        # Get Project Overview data
        # If version_selections provided, use it to get all SW Part Numbers and versions
        project_overview_data = {}
        
        if version_selections:
            # Multi-SW export: Get Project Overview from first available version
            # Software Part Number: ALL SWs (comma-separated)
            sw_part_numbers_list = sorted(list(version_selections.keys()))
            software_part_number = ", ".join(sw_part_numbers_list) if sw_part_numbers_list else ""
            
            # Application SW Version: version selected per SW (comma-separated)
            app_sw_versions_list = [f"{sw}: {version}" for sw, version in sorted(version_selections.items())]
            application_sw_version = ", ".join(app_sw_versions_list) if app_sw_versions_list else ""
            
            # Get Project Overview from first version object
            first_sw = sw_part_numbers_list[0] if sw_part_numbers_list else None
            first_version = version_selections.get(first_sw) if first_sw else None
            
            if first_sw and first_version:
                version_obj = TestCaseVersion.objects.filter(
                    instance=snapshot.instance,
                    sw_part_number=first_sw,
                    app_sw_version=first_version
                ).first()
                
                if version_obj:
                    po = ProjectOverview.objects.filter(version=version_obj).first()
                    if po:
                        project_overview_data = {
                            "project_code": po.project_code or "",
                            "vcu_platform": po.vcu_platform or "",
                            "hw_part_number": po.hardware_part_number or "",
                            "software_part_number": software_part_number,  # ALL SWs
                            "project_stage": po.project_stage or "",
                            "developer": po.developer or "",
                            "test_engineer": po.test_engineer or "",
                            "application_sw_version": application_sw_version,  # Versions per SW
                            "bootloader_sw_version": po.bootloader_sw_version or "",
                            "checksum_value": po.checksum_value or "",
                            "dbc_test_it": po.dbc_test_it or "",
                        }
        else:
            # Single snapshot export (backward compatibility)
            if snapshot.app_sw_version:
                version_obj = TestCaseVersion.objects.filter(
                    instance=snapshot.instance,
                    app_sw_version=snapshot.app_sw_version
                ).first()
                
                if version_obj:
                    po = ProjectOverview.objects.filter(version=version_obj).first()
                    if po:
                        project_overview_data = {
                            "project_code": po.project_code or "",
                            "vcu_platform": po.vcu_platform or "",
                            "hw_part_number": po.hardware_part_number or "",
                            "software_part_number": po.sw_part_number or po.software_part_number or "",
                            "project_stage": po.project_stage or "",
                            "developer": po.developer or "",
                            "test_engineer": po.test_engineer or "",
                            "application_sw_version": snapshot.app_sw_version or "",
                            "bootloader_sw_version": po.bootloader_sw_version or "",
                            "checksum_value": po.checksum_value or "",
                            "dbc_test_it": po.dbc_test_it or "",
                        }
        
        # Bar chart data
        if snapshot.sheet_name and snapshot.sw_part_number:
            bar_mode = "status_overview"
            sheet_labels = ["Total Test Cases", "Pass", "Fail", "Not Relevant"]
            sheet_values = [total_test_cases, passed_count, failed_count, not_relevant_count]
        elif snapshot.sheet_name:
            bar_mode = "total_per_sw"
            sw_counts = {}
            for exec_data in execution_data:
                sw = exec_data.get('sw_part_number', '')
                if sw:
                    sw_counts[sw] = sw_counts.get(sw, 0) + 1
            sheet_labels = sorted(sw_counts.keys())
            sheet_values = [sw_counts[sw] for sw in sheet_labels]
        else:
            bar_mode = "total_per_sheet"
            sheet_counts = {}
            for exec_data in execution_data:
                sheet = exec_data.get('sheet_name', '')
                if sheet:
                    sheet_counts[sheet] = sheet_counts.get(sheet, 0) + 1
            sheet_labels = sorted(sheet_counts.keys())
            sheet_values = [sheet_counts[sheet] for sheet in sheet_labels]
        
        # Read CSS file
        css_file_path = os.path.join(settings.BASE_DIR, 'testmanager', 'static', 'testmanager', 'css', 'export_html.css')
        css_content = ""
        if os.path.exists(css_file_path):
            with open(css_file_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
        
        # Read JS file
        js_file_path = os.path.join(settings.BASE_DIR, 'testmanager', 'static', 'testmanager', 'js', 'export_html.js')
        js_content = ""
        if os.path.exists(js_file_path):
            with open(js_file_path, 'r', encoding='utf-8') as f:
                js_content = f.read()
        
        # Additional CSS for offline functionality (extracted to avoid linter confusion)
        additional_css = """
        /* The following CSS ensures that all table rows are visible by default,
           but rows with the 'hidden' class will not be displayed.
           This can be useful for filtering or toggling visibility in the report table.
        */
        .table tbody tr {
            display: table-row;
        }

        .table tbody tr.hidden {
            display: none;
        }
"""
        
        # Download and inline all external libraries for true offline capability
        libraries = _get_inline_libraries()
        bootstrap_css = libraries.get('bootstrap_css', '')
        bootstrap_icons_css = libraries.get('bootstrap_icons_css', '')
        bootstrap_js = libraries.get('bootstrap_js', '')
        chart_js = libraries.get('chart_js', '')
        
        # Build HTML content with ALL CSS and JS inlined (NO external dependencies)
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Test Cases Report - {snapshot.snapshot_name}</title>
    <style>
        /* Bootstrap CSS - Inlined for offline use */
{bootstrap_css}
        
        /* Bootstrap Icons CSS - Inlined for offline use */
{bootstrap_icons_css}
        
        /* Custom CSS */
{css_content}
{additional_css}
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="export-header">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <div class="d-flex align-items-center mb-2">
                        <div>
                            <h1 class="mb-0"><i class="bi bi-file-earmark-code me-3"></i>Test Cases Export</h1>
                            <p class="mb-2 text-muted">HTML Test Report – {snapshot.snapshot_name}</p>
                        </div>
                    </div>
                    <p class="mb-2">
                        Generated on {snapshot.exported_at.strftime("%B %d, %Y %H:%M")}
                    </p>
                    <div class="mt-3 p-3 rounded" style="background: #3c85ce; border: 1px solid #dee2e6;">
                        <div class="d-flex align-items-center">
                            <i class="bi bi-check-circle-fill text-success me-2" style="font-size: 1.2rem;"></i>
                            <div>
                                <strong>Snapshot Export</strong>
                                <div class="small">{total_executed}/{total_test_cases} tests executed</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Project Overview Section -->
        <div class="row g-3 mt-4 mb-4">
            <div class="col-12">
                <div class="card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h5 class="mb-3" style="font-size: 1.1rem; font-weight: 600;">
                        <i class="bi bi-info-circle me-2"></i>Project Overview
                    </h5>
                    <div class="row g-3">
                        <div class="col-md-3">
                            <div class="small text-muted">Project Code</div>
                            <div class="fw-semibold">{project_overview_data.get('project_code', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">VCU Platform</div>
                            <div class="fw-semibold">{project_overview_data.get('vcu_platform', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Hardware Part Number</div>
                            <div class="fw-semibold">{project_overview_data.get('hw_part_number', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Software Part Number</div>
                            <div class="fw-semibold">{project_overview_data.get('software_part_number', project_overview_data.get('sw_part_number', '-'))}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Project Stage</div>
                            <div class="fw-semibold">{project_overview_data.get('project_stage', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Developer</div>
                            <div class="fw-semibold">{project_overview_data.get('developer', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Test Engineer</div>
                            <div class="fw-semibold">{project_overview_data.get('test_engineer', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Application SW Version</div>
                            <div class="fw-semibold">{project_overview_data.get('application_sw_version', project_overview_data.get('app_sw_version', snapshot.app_sw_version or '-'))}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Bootloader SW Version</div>
                            <div class="fw-semibold">{project_overview_data.get('bootloader_sw_version', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">Checksum Value</div>
                            <div class="fw-semibold">{project_overview_data.get('checksum_value', '-')}</div>
                        </div>
                        <div class="col-md-3">
                            <div class="small text-muted">DBC test_it</div>
                            <div class="fw-semibold">{project_overview_data.get('dbc_test_it', '-')}</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Filters and Pie Chart Section -->
        <div class="row g-3 mt-4 mb-4">
            <!-- Filters Column -->
            <div class="col-md-6">
                <div class="kpi-card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h5 class="mb-3" style="font-size: 1rem; font-weight: 600;">
                        <i class="bi bi-funnel me-2"></i>Filters
                    </h5>
                    <div class="row g-2">
                        <div class="col-md-12">
                            <label class="form-label small"><strong>Sheet:</strong></label>
                            <select class="form-select form-select-sm" id="sheetFilter" onchange="filterTable()">
                                <option value="">All Sheets</option>
                                {''.join([f'<option value="{sheet}">{sheet}</option>' for sheet in sheet_names])}
                            </select>
                        </div>
                        <div class="col-md-12">
                            <label class="form-label small"><strong>SW Part Number:</strong></label>
                            <select class="form-select form-select-sm" id="swFilter" onchange="filterTable()">
                                <option value="">All SW Part Numbers</option>
                                {''.join([f'<option value="{sw}">{sw}</option>' for sw in sw_list])}
                            </select>
                        </div>
                        <div class="col-md-12">
                            <label class="form-label small"><strong>Feature:</strong></label>
                            <select class="form-select form-select-sm" id="featureFilter" onchange="filterTable()">
                                <option value="">All Features</option>
                                {''.join([f'<option value="{feature}">{feature}</option>' for feature in feature_list])}
                            </select>
                        </div>
                    </div>
                </div>
            </div>
            <!-- Pie Chart Column -->
            <div class="col-md-6">
                <div class="chart-container" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h4 class="mb-3 text-center" style="font-size: 1.1rem; font-weight: 600;">
                        <i class="bi bi-pie-chart me-2"></i>Status Distribution
                    </h4>
                    <div class="chart-box" style="height: 300px; position: relative;">
                        <canvas id="statusPie" aria-label="Status distribution pie chart" role="img"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <!-- KPI Cards Row -->
        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="kpi-card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <div class="kpi-title" style="font-size: 0.85rem; color: #6c757d; margin-bottom: 10px;">Pass Current Total</div>
                    <div class="kpi-progress-wrapper" style="position: relative; width: 100px; height: 100px; margin: 0 auto 15px;">
                        <canvas id="passProgress" width="100" height="100"></canvas>
                        <div class="kpi-percentage" id="passPercentage" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 1.5rem; font-weight: 700; color: #28a745;">{pass_percentage:.2f}%</div>
                    </div>
                    <div class="kpi-details" style="text-align: center;">
                        <div style="font-size: 0.75rem; color: #6c757d;">Total: <strong id="passTotal">{passed_count}</strong></div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="kpi-card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <div class="kpi-title" style="font-size: 0.85rem; color: #6c757d; margin-bottom: 10px;">Fail Current Total</div>
                    <div class="kpi-progress-wrapper" style="position: relative; width: 100px; height: 100px; margin: 0 auto 15px;">
                        <canvas id="failProgress" width="100" height="100"></canvas>
                        <div class="kpi-percentage" id="failPercentage" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 1.5rem; font-weight: 700; color: #dc3545;">{fail_percentage:.2f}%</div>
                    </div>
                    <div class="kpi-details" style="text-align: center;">
                        <div style="font-size: 0.75rem; color: #6c757d;">Total: <strong id="failTotal">{failed_count}</strong></div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="kpi-card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <div class="kpi-title" style="font-size: 0.85rem; color: #6c757d; margin-bottom: 10px;">Not Executed Current Total</div>
                    <div class="kpi-progress-wrapper" style="position: relative; width: 100px; height: 100px; margin: 0 auto 15px;">
                        <canvas id="notExecProgress" width="100" height="100"></canvas>
                        <div class="kpi-percentage" id="notExecPercentage" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 1.5rem; font-weight: 700; color: #ffc107;">{not_exec_percentage:.2f}%</div>
                    </div>
                    <div class="kpi-details" style="text-align: center;">
                        <div style="font-size: 0.75rem; color: #6c757d;">Total: <strong id="notExecTotal">{not_executed_count}</strong></div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="kpi-card" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <div class="kpi-title" style="font-size: 0.85rem; color: #6c757d; margin-bottom: 10px;">Total Test Cases</div>
                    <div class="kpi-progress-wrapper" style="position: relative; width: 100px; height: 100px; margin: 0 auto 15px;">
                        <canvas id="totalProgress" width="100" height="100"></canvas>
                        <div class="kpi-percentage" id="totalPercentage" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 1.5rem; font-weight: 700; color: #0052A5;">{executed_percentage:.2f}%</div>
                    </div>
                    <div class="kpi-details" style="text-align: center;">
                        <div style="font-size: 0.75rem; color: #6c757d;">Total: <strong id="totalTestCases">{total_test_cases}</strong></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Bar Chart Section -->
        <div class="row g-4 mt-2 mb-4">
            <div class="col-12">
                <div class="chart-container" style="background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h4 class="mb-3 text-center" style="font-size: 1.1rem; font-weight: 600;" id="barChartTitle">
                        <i class="bi bi-bar-chart me-2"></i><span id="barChartTitleText">Test Cases Distribution</span>
                    </h4>
                    <div class="chart-box" style="height: 350px; position: relative;">
                        <canvas id="swBarChart" aria-label="Bar chart" role="img"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <div class="table-container">
            <div class="table-responsive">
                <table class="table table-striped">
                    <thead>
                        <tr>
                            <th>SL.NO</th>
                            <th>SW Part Number</th>
                            <th>Feature</th>
                            <th>Requirement ID</th>
                            <th>Requirement Description</th>
                            <th>Test Case ID</th>
                            <th>Test Case Summary</th>
                            <th>Pre Conditions</th>
                            <th>Inputs</th>
                            <th>Periodic Time</th>
                            <th>Test Steps</th>
                            <th>Expected Result</th>
                            <th>Status</th>
                            <th>Reports</th>
                            <th>Comments</th>
                        </tr>
                    </thead>
                    <tbody id="testTableBody">
"""
        
        # FEATURE GROUPING: Sort execution data by sheet_name, then by feature, then by test_case_id
        # This ensures test cases are grouped by feature within each sheet
        execution_data_sorted = sorted(
            execution_data,
            key=lambda x: (
                x.get('sheet_name', ''),
                x.get('feature', ''),
                x.get('base_test_case_id', '') or x.get('test_case_id', '')
            )
        )
        
        # Add table rows from execution data (grouped by feature within each sheet)
        current_sheet = None
        current_feature = None
        idx = 0
        for exec_data in execution_data_sorted:
            sheet_name = exec_data.get('sheet_name', '')
            feature_name = exec_data.get('feature', '')
            
            # Add feature group header if feature changed within same sheet
            if sheet_name != current_sheet:
                current_sheet = sheet_name
                current_feature = None  # Reset feature when sheet changes
            
            if feature_name != current_feature and feature_name:
                current_feature = feature_name
                # Add feature group header row
                html_content += f"""
                        <tr class="feature-group-header" style="background-color: #f8f9fa; font-weight: bold;">
                            <td colspan="15" style="padding: 10px; border-top: 2px solid #dee2e6;">
                                <i class="bi bi-grid-3x3-gap me-2"></i>Feature: {feature_name}
                            </td>
                        </tr>"""
            
            idx += 1
            # Always use base_test_case_id for display (version suffix never shown)
            display_test_case_id = exec_data.get('base_test_case_id', '') or exec_data.get('test_case_id', '')
            requirement_id = exec_data.get('requirement_id', '')
            execution_status = exec_data.get('execution_status', '')
            
            # Determine status badge
            status_html = '<span class="badge bg-secondary">Not Executed</span>'
            if execution_status:
                status_upper = execution_status.upper()
                if status_upper == "PASS":
                    status_html = '<span class="badge bg-success">Pass</span>'
                elif status_upper == "FAIL":
                    status_html = '<span class="badge bg-danger">Fail</span>'
                elif status_upper in ("NOT RELEVANT", "NOT_RELEVANT"):
                    status_html = '<span class="badge bg-info text-white">Not Relevant</span>'
                else:
                    status_html = f'<span class="badge bg-warning text-dark">{execution_status}</span>'
            
            # Reports column
            reports = exec_data.get('execution_reports', '') or ''
            reports_html = '<span class="text-muted">-</span>'
            if reports:
                if reports.lower().startswith(('http://', 'https://')):
                    reports_html = f'<a href="{reports}" target="_blank" class="btn btn-sm btn-outline-primary"><i class="bi bi-box-arrow-up-right me-1"></i>Open Report</a>'
                else:
                    # Truncate long text
                    reports_text = reports[:100] + '...' if len(reports) > 100 else reports
                    reports_html = f'<small>{reports_text}</small>'
            
            # Comments column
            comments = exec_data.get('execution_comments', '') or ''
            comments_html = '<span class="text-muted">-</span>'
            if comments:
                comments_text = comments[:100] + '...' if len(comments) > 100 else comments
                comments_html = f'<small>{comments_text}</small>'
            
            html_content += f"""
                        <tr data-sheet="{exec_data.get('sheet_name', '')}" data-sw="{exec_data.get('sw_part_number', '')}" data-feature="{exec_data.get('feature', '')}">
                            <td>{idx}</td>
                            <td>{exec_data.get('sw_part_number', '')}</td>
                            <td>{exec_data.get('feature', '')}</td>
                            <td>{requirement_id}</td>
                            <td>{exec_data.get('requirement_description', '')}</td>
                            <td><code>{display_test_case_id}</code></td>
                            <td>{exec_data.get('test_case_summary', '')}</td>
                            <td>{exec_data.get('pre_conditions', '')}</td>
                            <td>{exec_data.get('inputs', '')}</td>
                            <td>{exec_data.get('periodic_time', '')}</td>
                            <td>{exec_data.get('test_steps', '')}</td>
                            <td>{exec_data.get('expected_result', '')}</td>
                            <td>{status_html}</td>
                            <td>{reports_html}</td>
                            <td>{comments_html}</td>
                        </tr>"""
        
        html_content += """
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer-note">
            <p class="mb-0 text-muted">
                <i class="bi bi-lock me-2"></i>
                This is a view-only export. Data is read-only and cannot be modified.
            </p>
        </div>
    </div>

    <!-- Bootstrap JS - Inlined for offline use -->
    <script>
""" + bootstrap_js + """
    </script>
    
    <!-- Chart.js - Inlined for offline use -->
    <script>
""" + chart_js + """
    </script>
    
    <!-- Embedded Execution Data -->
    <script id="executionData" type="application/json">
"""
        
        # Embed execution data as JSON
        html_content += json.dumps(execution_data, indent=8, default=str)
        
        html_content += f"""
    </script>
    
    <!-- Chart Data -->
    <script id="statusLabels" type="application/json">{json.dumps(status_labels)}</script>
    <script id="statusValues" type="application/json">{json.dumps(status_values)}</script>
    <script id="sheetLabels" type="application/json">{json.dumps(sheet_labels)}</script>
    <script id="sheetValues" type="application/json">{json.dumps(sheet_values)}</script>
    <script id="barMode" type="application/json">{json.dumps(bar_mode)}</script>
    
    <script>
        window.PASS_PERCENTAGE = {pass_percentage};
        window.FAIL_PERCENTAGE = {fail_percentage};
        window.NOT_EXEC_PERCENTAGE = {not_exec_percentage};
        window.EXECUTED_PERCENTAGE = {executed_percentage};
        
        // Pure JavaScript filtering (no backend calls)
        function filterTable() {{
            const sheetFilter = document.getElementById('sheetFilter').value;
            const swFilter = document.getElementById('swFilter').value;
            const featureFilter = document.getElementById('featureFilter').value;
            
            const rows = document.querySelectorAll('#testTableBody tr');
            
            rows.forEach(row => {{
                const rowSheet = row.getAttribute('data-sheet') || '';
                const rowSw = row.getAttribute('data-sw') || '';
                const rowFeature = row.getAttribute('data-feature') || '';
                
                const sheetMatch = !sheetFilter || rowSheet === sheetFilter;
                const swMatch = !swFilter || rowSw === swFilter;
                const featureMatch = !featureFilter || rowFeature === featureFilter;
                
                if (sheetMatch && swMatch && featureMatch) {{
                    row.classList.remove('hidden');
                }} else {{
                    row.classList.add('hidden');
                }}
            }});
        }}
        
        // Initialize on page load
        document.addEventListener('DOMContentLoaded', function() {{
            filterTable();
        }});
        
        {js_content}
    </script>
</body>
</html>"""
        
        # Create html_reports directory if it doesn't exist
        reports_dir = os.path.join(settings.BASE_DIR, 'html_reports')
        os.makedirs(reports_dir, exist_ok=True)
        
        # Generate filename with timestamp (STRICT FORMAT)
        # Format: YYYY-MM-DD_HH-MM-SS_FeatureExport.html
        timestamp = snapshot.exported_at.strftime('%Y-%m-%d_%H-%M-%S')
        # If selected_features provided, include in filename
        if hasattr(snapshot, '_selected_features') and snapshot._selected_features:
            features_str = "_".join([f.replace(" ", "_")[:10] for f in snapshot._selected_features[:2]])
            filename = f"{timestamp}_{features_str}_FeatureExport.html"
        else:
            filename = f"{timestamp}_FeatureExport.html"
        file_path = os.path.join(reports_dir, filename)
        
        # Write HTML file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return file_path
        
    except Exception as e:
        log_error("export_views.py:generate_offline_html_report", "Error generating offline HTML report", 
                 {"error": str(e), "type": type(e).__name__, "snapshot_id": snapshot.id if snapshot else None}, exc_info=True)
        return None


@login_required
@manager_required
def get_exportable_versions_api(request):
    """
    NEW FEATURE-BASED EXPORT API (VERSION-SCOPED)
    
    Returns JSON with structure:
    {
      "features": [
        {
          "name": "<feature name>",
          "versions": [
            {
              "version": "<version>",
              "sw_part_number": "<sw>",
              "version_id": <id>,
              "total": <int>,
              "executed": <int>,
              "completed": <true|false>,
              "is_exported": <true|false>
            }
          ]
        }
      ]
    }
    
    CRITICAL: Feature completion is PER VERSION, not aggregated across versions.
    """
    try:
        active_instance = get_active_instance()
        
        from ..version_service import can_user_access_version
        from ..services import (
            get_exportable_features_for_versions,
            is_feature_version_exported
        )
        
        # Get all accessible versions for the user
        all_versions = TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at')
        accessible_versions = []
        for version_obj in all_versions:
            if can_user_access_version(request.user, version_obj):
                accessible_versions.append(version_obj)
        
        # Get all features with their completion status PER VERSION
        features_data = get_exportable_features_for_versions(accessible_versions, active_instance)
        
        # Add export status to each feature+version combination
        for feature in features_data:
            for version_info in feature['versions']:
                # Find the version object
                version_obj = next(
                    (v for v in accessible_versions if v.id == version_info['version_id']),
                    None
                )
                if version_obj:
                    version_info['is_exported'] = is_feature_version_exported(
                        feature['name'], version_obj, active_instance
                    )
                else:
                    version_info['is_exported'] = False
        
        return JsonResponse({
            'ok': True,
            'features': features_data,
        })
    except Exception as e:
        log_error("export_views.py:get_exportable_versions_api", "Error loading exportable data", {
            "error": str(e),
            "type": type(e).__name__,
            "user": request.user.username if request.user.is_authenticated else "anonymous"
        }, exc_info=True)
        return JsonResponse({
            'ok': False,
            'error': f'Error loading exportable data: {str(e)}'
        }, status=500)


@login_required
@manager_required
def export_excel(request):
    """
    NEW FEATURE-BASED EXPORT: Export test cases to Excel.
    
    FEATURE-DRIVEN EXPORT:
    - User selects features (checkboxes) - only completed features are selectable
    - User selects versions (checkboxes) - multi-select allowed
    - Export includes ONLY selected features and versions
    - Export spans ALL sheets and ALL SW part numbers
    - Tracks exported feature+version combinations to prevent duplicates
    """
    active_instance = get_active_instance()
    
    # Parse JSON payload (new structure: feature_versions array)
    import json
    selected_features = []
    version_objs = []
    
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            features = data.get('features', [])
            versions = data.get('versions', [])
            
            # If features/versions provided, use them; otherwise export ALL
            if features and versions:
                # Get version objects from IDs
                version_objs = list(TestCaseVersion.objects.filter(
                    instance=active_instance,
                    id__in=versions
                ))
                selected_features = features
            else:
                # Export ALL - no selection means export everything
                version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
                # Get all unique features
                selected_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
        except (json.JSONDecodeError, TypeError):
            # On error, export ALL data
            version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
            selected_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
    else:
        # Fallback: export ALL data
        version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
        selected_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
    
    # If still empty, export ALL available data
    if not version_objs:
        version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
    if not selected_features:
        selected_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
    
    # Build version_selections dict for backward compatibility with excel_export module
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
    
    # Build workbook with selected features and versions
    try:
        wb = build_testcase_export_workbook(
            sheet_filter="",  # Export ALL sheets
            sw="",  # Export ALL SW part numbers
            app_sw_version="",  # Not used - version_selections dict is used instead
            feature="",  # Will filter by selected_features in the function
            query="",  # Export all matching test cases
            versions_list=None,  # Not used
            latest_versions_only=False,
            version_selections=version_selections,
            selected_features=selected_features,  # Pass selected features
            selected_version_objs=version_objs,  # Pass version objects
        )
    except Exception as e:
        log_error("views.py:export_excel", "Error creating workbook", {"error": str(e), "type": type(e).__name__}, exc_info=True)
        messages.error(request, f"Error creating export: {str(e)}")
        return redirect("testcase_list")
    
    # Create HTTP response
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    feature_names_str = "_".join([f.replace(" ", "_") for f in selected_features[:3]])  # Limit length
    filename = f"TestCases_Export_{timestamp}_{feature_names_str}.xlsx"
    response["Content-Disposition"] = f"attachment; filename={filename}"
    wb.save(response)
    
    # Create snapshots to mark feature+version combinations as exported (no completion check - always create)
    for feature_name in selected_features:
        for version_obj in version_objs:
            # Get test cases for this feature and version
            test_cases = TestCase.objects.filter(
                instance=active_instance,
                feature=feature_name,
                sw_part_number=version_obj.sw_part_number,
                app_sw_version=version_obj.app_sw_version
            )
            total_count = test_cases.count()
            
            # Get executions
            test_case_ids = list(test_cases.values_list('id', flat=True))
            executions = TestExecution.objects.filter(
                instance=active_instance,
                version=version_obj,
                test_case_id__in=test_case_ids
            )
            executed_count = executions.values_list('test_case_id', flat=True).distinct().count()
            
            # Always create snapshot (no completion check)
            if True:
                try:
                    # Get test cases for this feature and version
                    test_cases = TestCase.objects.filter(
                        instance=active_instance,
                        feature=feature_name,
                        sw_part_number=version_obj.sw_part_number,
                        app_sw_version=version_obj.app_sw_version
                    )
                    
                    # Get executions
                    test_case_ids = list(test_cases.values_list('id', flat=True))
                    executions = TestExecution.objects.filter(
                        instance=active_instance,
                        version=version_obj,
                        test_case_id__in=test_case_ids
                    ).select_related('test_case', 'user')
                    
                    # Prepare execution data for snapshot
                    execution_data = []
                    for exec_obj in executions:
                        tc = exec_obj.test_case
                        from ..utils import get_requirement_id
                        display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
                        
                        exec_dict = {
                            'test_case_id': tc.test_case_id,
                            'base_test_case_id': tc.base_test_case_id or tc.test_case_id,
                            'test_case_db_id': tc.id,
                            'sheet_name': tc.sheet_name,
                            'feature': tc.feature,
                            'requirement_id': display_requirement_id,
                            'execution_status': exec_obj.status,
                            'execution_app_sw_version': version_obj.app_sw_version,
                            'executed_at': exec_obj.executed_at.isoformat() if exec_obj.executed_at else None,
                        }
                        execution_data.append(exec_dict)
                    
                    snapshot = TestExecutionSnapshot.objects.create(
                        instance=active_instance,
                        snapshot_name=f"Excel Export - {feature_name} - {version_obj.sw_part_number} - {version_obj.app_sw_version}",
                        sheet_name="",  # All sheets
                        sw_part_number=version_obj.sw_part_number,
                        app_sw_version=version_obj.app_sw_version,
                        execution_data=execution_data,  # Store execution data to track exported features
                        exported_by=request.user,
                        total_test_cases=total_count,
                        total_executed=executed_count,
                        total_passed=0,
                        total_failed=0,
                        total_not_executed=0,
                        notes=json.dumps({'exported_features': [feature_name]}),  # Track exported features
                    )
                except Exception as e:
                    log_error("views.py:export_excel", "Error creating snapshot", {"error": str(e), "type": type(e).__name__}, exc_info=True)
                    # Continue even if snapshot creation fails
    
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
    NEW FEATURE-BASED EXPORT: Export test cases as HTML view-only page with charts
    
    FEATURE-DRIVEN EXPORT:
    - User selects features (checkboxes) - only completed features are selectable
    - User selects versions (checkboxes) - multi-select allowed
    - Export includes ONLY selected features and versions
    - Export spans ALL sheets and ALL SW part numbers
    - Tracks exported feature+version combinations to prevent duplicates
    - Generates offline HTML report with filename: YYYY-MM-DD_HH-MM-SS_FeatureExport.html
    """
    from django.urls import reverse
    
    active_instance = get_active_instance()
    
    # Parse JSON payload
    import json
    validated_combinations = []
    
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            features = data.get('features', [])
            versions = data.get('versions', [])
            
            # If features/versions provided, use them; otherwise export ALL
            if features and versions:
                # Get version objects from IDs
                version_objs = list(TestCaseVersion.objects.filter(
                    instance=active_instance,
                    id__in=versions
                ))
                # Create combinations for all feature+version pairs
                for feature_name in features:
                    for version_obj in version_objs:
                        validated_combinations.append({
                            'feature': feature_name,
                            'version_obj': version_obj
                        })
            else:
                # Export ALL - no selection means export everything
                version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
                all_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
                for feature_name in all_features:
                    for version_obj in version_objs:
                        validated_combinations.append({
                            'feature': feature_name,
                            'version_obj': version_obj
                        })
        except (json.JSONDecodeError, TypeError):
            # On error, export ALL data
            version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
            all_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
            for feature_name in all_features:
                for version_obj in version_objs:
                    validated_combinations.append({
                        'feature': feature_name,
                        'version_obj': version_obj
                    })
    else:
        # Fallback: export ALL data
        version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
        all_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
        for feature_name in all_features:
            for version_obj in version_objs:
                validated_combinations.append({
                    'feature': feature_name,
                    'version_obj': version_obj
                })
    
    # If still empty, export ALL available data
    if not validated_combinations:
        version_objs = list(TestCaseVersion.objects.filter(instance=active_instance).order_by('-created_at'))
        all_features = list(TestCase.objects.filter(instance=active_instance).values_list('feature', flat=True).distinct())
        for feature_name in all_features:
            for version_obj in version_objs:
                validated_combinations.append({
                    'feature': feature_name,
                    'version_obj': version_obj
                })
    
    # Create snapshots for each feature+version combination
    snapshots = []
    for combo in validated_combinations:
        feature_name = combo['feature']
        version_obj = combo['version_obj']
        
        # Get test cases for this feature and version
        test_cases = TestCase.objects.filter(
            instance=active_instance,
            feature=feature_name,
            sw_part_number=version_obj.sw_part_number,
            app_sw_version=version_obj.app_sw_version
        )
        
        total_count = test_cases.count()
        if total_count == 0:
            continue
        
        # Get executions
        test_case_ids = list(test_cases.values_list('id', flat=True))
        executions = TestExecution.objects.filter(
            instance=active_instance,
            version=version_obj,
            test_case_id__in=test_case_ids
        ).exclude(status__isnull=True).exclude(status__exact="")
        
        executed_count = executions.values_list('test_case_id', flat=True).distinct().count()
        
        # Only create snapshot if feature is completed for this version
        if executed_count == total_count:
            try:
                # Get executions with related data
                executions = TestExecution.objects.filter(
                    instance=active_instance,
                    version=version_obj,
                    test_case_id__in=test_case_ids
                ).select_related('test_case', 'user')
                
                # Prepare execution data for snapshot
                execution_data = []
                for exec_obj in executions:
                    tc = exec_obj.test_case
                    display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
                    
                    exec_dict = {
                        'test_case_id': tc.test_case_id,
                        'base_test_case_id': tc.base_test_case_id or tc.test_case_id,
                        'test_case_db_id': tc.id,
                        'sheet_name': tc.sheet_name,
                        'feature': tc.feature,
                        'requirement_id': display_requirement_id,
                        'execution_status': exec_obj.status,
                        'execution_app_sw_version': version_obj.app_sw_version,
                        'executed_at': exec_obj.executed_at.isoformat() if exec_obj.executed_at else None,
                    }
                    execution_data.append(exec_dict)
                
                snapshot = TestExecutionSnapshot.objects.create(
                    instance=active_instance,
                    snapshot_name=f"HTML Export - {feature_name} - {version_obj.sw_part_number} - {version_obj.app_sw_version}",
                    sheet_name="",  # All sheets
                    sw_part_number=version_obj.sw_part_number,
                    app_sw_version=version_obj.app_sw_version,
                    execution_data=execution_data,  # Store execution data to track exported features
                    exported_by=request.user,
                    total_test_cases=total_count,
                    total_executed=executed_count,
                    total_passed=0,
                    total_failed=0,
                    total_not_executed=0,
                    notes=json.dumps({'exported_features': [feature_name]}),  # Track exported features
                )
                snapshots.append(snapshot)
            except Exception as e:
                log_error("views.py:export_html", "Error creating snapshot", {"error": str(e), "type": type(e).__name__}, exc_info=True)
                messages.error(request, f"Failed to create snapshot for {feature_name} - {version_obj.sw_part_number} - {version_obj.app_sw_version}.")
                return redirect("testcase_list")
    
    # If no snapshots created, create a default one with all data
    if not snapshots:
        # Create a default snapshot with all available data
        try:
            all_test_cases = TestCase.objects.filter(instance=active_instance)
            all_executions = TestExecution.objects.filter(instance=active_instance).select_related('test_case', 'user')
            
            execution_data = []
            for exec_obj in all_executions:
                tc = exec_obj.test_case
                display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
                exec_dict = {
                    'test_case_id': tc.test_case_id,
                    'base_test_case_id': tc.base_test_case_id or tc.test_case_id,
                    'test_case_db_id': tc.id,
                    'sheet_name': tc.sheet_name,
                    'feature': tc.feature,
                    'requirement_id': display_requirement_id,
                    'execution_status': exec_obj.status,
                    'execution_app_sw_version': exec_obj.version.app_sw_version if exec_obj.version else '',
                    'executed_at': exec_obj.executed_at.isoformat() if exec_obj.executed_at else None,
                }
                execution_data.append(exec_dict)
            
            snapshot = TestExecutionSnapshot.objects.create(
                instance=active_instance,
                snapshot_name="HTML Export - All Data",
                sheet_name="",
                sw_part_number="",
                app_sw_version="",
                execution_data=execution_data,
                exported_by=request.user if request.user.is_authenticated else None,
                total_test_cases=all_test_cases.count(),
                total_executed=all_executions.values_list('test_case_id', flat=True).distinct().count(),
                total_passed=0,
                total_failed=0,
                total_not_executed=0,
                notes=json.dumps({'exported_features': []}),
            )
            snapshots.append(snapshot)
        except Exception as e:
            log_error("views.py:export_html", "Error creating default snapshot", {"error": str(e), "type": type(e).__name__}, exc_info=True)
    
    # Build version_selections dict for backward compatibility
    version_selections = {}
    version_objs = [c['version_obj'] for c in validated_combinations]
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
    
    # Generate offline HTML report (use first snapshot as primary, but include all data)
    primary_snapshot = snapshots[0]
    # Store selected features in snapshot for filename generation
    primary_snapshot._selected_features = validated_combinations
    offline_file_path = generate_offline_html_report(
        primary_snapshot, 
        version_selections=version_selections, 
        all_snapshots=snapshots
    )
    
    if offline_file_path:
        messages.success(request, f"HTML report generated successfully. Saved to: {offline_file_path}")
        # Open HTML file in browser
        import webbrowser
        file_url = f"file:///{offline_file_path.replace(os.sep, '/')}"
        try:
            webbrowser.open(file_url)
        except Exception as e:
            log_error("views.py:export_html", "Error opening HTML file", {"error": str(e)}, exc_info=True)
    else:
        messages.warning(request, f"Snapshots created successfully, but offline HTML report generation failed.")
    
    # Redirect to snapshot view (use primary snapshot)
    return redirect("export_html_snapshot", export_id=primary_snapshot.export_id)
    
    # Build base queryset with view-only filters (sheet, sw, feature)
    # STRICT: Filter by active instance only - exports only include active instance data
    base_qs = TestCase.objects.filter(instance=active_instance)
    
    if selected_sheet:
        base_qs = base_qs.filter(sheet_name=selected_sheet)
    if selected_sw:
        base_qs = base_qs.filter(sw_part_number=selected_sw)
    if selected_feature:
        base_qs = base_qs.filter(feature=selected_feature)
    
    # CRITICAL: TestCase doesn't have version - version is in TestCaseVersion
    # We don't filter TestCase by version - we filter executions by version FK instead
    # base_qs already filtered by instance, sheet, sw, feature - that's sufficient
    
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
        
        # CRITICAL: Filter executions by active version FK, not legacy fields
        if selected_sw in active_versions:
            active_version_obj = active_versions[selected_sw]
            sw_executions = sw_executions.filter(
                version=active_version_obj  # Use explicit FK
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
    
    # STRICT: sl_no does NOT exist - removed after hierarchy refactor
    # Order by id (primary key) instead of deprecated sl_no field
    # Note: sheet_name also doesn't exist, but ordering is disabled until TestCaseVersion/TestCaseSheet models exist
    base_qs = base_qs.order_by("id")
    
    # CRITICAL: Get executions using version FK ONLY
    # Filter to ONLY selected version (already resolved above)
    test_case_ids = base_qs.values_list('id', flat=True)
    executions = TestExecution.objects.filter(
        instance=active_instance, 
        test_case__in=test_case_ids,
        version_id__in=active_version_ids  # CRITICAL: Filter by selected version FK only
    ).select_related("test_case", "version")
    
    # CRITICAL: Do NOT filter by sw_part_number or app_sw_version (legacy fields)
    # Version filtering is already done via version FK above
    
    # CRITICAL: Build execution map using version FK, not legacy fields
    # Key is (test_case.id, version_id) to ensure version isolation
    execution_map = {}
    for e in executions:
        tc_id = e.test_case.id
        version_id = e.version_id if e.version else None
        key = (tc_id, version_id)
        execution_map[key] = e
    
    # Convert queryset to list for sorting
    test_cases_list = list(base_qs)
    
    # CRITICAL: Match executions to test cases using version FK
    # Resolve version for each test case and match by (tc_id, version_id)
    execution_map_by_tc = {}
    for tc in test_cases_list:
        tc_id = tc.id
        tc_sw = tc.sw_part_number or ""
        
        # CRITICAL: Get active version for this test case's SW part number
        if tc_sw in active_versions:
            active_version_obj = active_versions[tc_sw]
            key = (tc_id, active_version_obj.id)
            if key in execution_map:
                execution_map_by_tc[tc_id] = execution_map[key]
    
    # Attach executions to test cases - always display base_test_case_id (version suffix never shown)
    for tc in test_cases_list:
        # Use execution_map_by_tc for lookup
        tc.exec = execution_map_by_tc.get(tc.id)
        # Always display base_test_case_id (version suffix never shown in UI/exports)
        tc.display_test_case_id = tc.base_test_case_id or tc.test_case_id
        # Auto-derive requirement_id from base_test_case_id if requirement_id is empty
        tc.display_requirement_id = get_requirement_id(tc.base_test_case_id or tc.test_case_id, tc.requirement_id)
    
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
    
    # CRITICAL: Get Project Overview data using version FK
    # ProjectOverview is linked to TestCaseVersion, so get it via active versions
    project_overview_data = {}
    # Try to get ProjectOverview for the first active version (or selected SW if available)
    if selected_sw and selected_sw in active_versions:
        po = ProjectOverview.objects.filter(version=active_versions[selected_sw]).first()
        if po:
            project_overview_data = {
                "project_code": po.project_code or "",
                "vcu_platform": po.vcu_platform or "",
                "hw_part_number": po.hardware_part_number or "",
                "sw_part_number": po.sw_part_number or po.software_part_number or "",
                "project_stage": po.project_stage or "",
                "developer": po.developer or "",
                "test_engineer": po.test_engineer or "",
                "app_sw_version": active_versions[selected_sw].app_sw_version or "",
                "bootloader_sw_version": po.bootloader_sw_version or "",
                "checksum_value": po.checksum_value or "",
                "dbc_test_it": po.dbc_test_it or "",
            }
    elif active_versions:
        # Get ProjectOverview for first active version
        first_version = list(active_versions.values())[0]
        po = ProjectOverview.objects.filter(version=first_version).first()
        if po:
            project_overview_data = {
                "project_code": po.project_code or "",
                "vcu_platform": po.vcu_platform or "",
                "hw_part_number": po.hardware_part_number or "",
                "sw_part_number": po.sw_part_number or po.software_part_number or "",
                "project_stage": po.project_stage or "",
                "developer": po.developer or "",
                "test_engineer": po.test_engineer or "",
                "app_sw_version": first_version.app_sw_version or "",
                "bootloader_sw_version": po.bootloader_sw_version or "",
                "checksum_value": po.checksum_value or "",
                "dbc_test_it": po.dbc_test_it or "",
            }
    
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
        # CRITICAL: Do NOT filter by TestCase.sheet_name - it's a legacy field
        # Get SW part numbers from TestCaseSheet relationship instead
        from ..models import TestCaseSheet
        if selected_sheet:
            sheet_objs = TestCaseSheet.objects.filter(
                version__instance=active_instance,
                sheet_name=selected_sheet
            ).select_related("version")
            sw_raw = set(sheet_objs.values_list("version__sw_part_number", flat=True))
        else:
            sw_raw = TestCase.objects.filter(instance=active_instance).values_list("sw_part_number", flat=True).distinct()
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
            self.base_test_case_id = data.get('base_test_case_id', '')  # Base ID from snapshot
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
            
            # Always display base_test_case_id (version suffix never shown)
            self.display_test_case_id = self.base_test_case_id or self.test_case_id
            # Auto-derive requirement_id from base_test_case_id if requirement_id is empty
            self.display_requirement_id = get_requirement_id(self.base_test_case_id or self.test_case_id, self.requirement_id)
            
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
    
    # STRICT: sl_no does NOT exist - removed after hierarchy refactor
    # Sort by id (primary key) instead
    def sort_key(tc):
        tc_id = getattr(tc, 'id', 999999)
        sheet_name = getattr(tc, 'sheet_name', '') or ''
        return (sheet_name, tc_id)
    
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

