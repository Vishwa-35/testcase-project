"""
Version Management Service

RISK REMOVAL: Version sorting, filtering, and query building logic was duplicated
across multiple views (home, testcase_list, export_html, excel_export).
This service provides a single source of truth for version-related operations.
"""

from functools import reduce
from operator import or_
from django.db.models import Q
from .models import SWVersionMapping, TestCase, TestExecution, TestCaseVersion, TestCaseSheet
from .constants import VERSION_PREFIX, VERSION_SEPARATOR, VERSION_MAX_PARTS


def parse_version(version_str):
    """
    Parse version string into tuple (major, minor, patch).
    
    RISK REMOVAL: Version parsing logic was duplicated in:
    - sort_versions(), get_version_sort_key(), sort_test_cases_by_version()
    
    Args:
        version_str: Version string (e.g., "2.1.0", "V2.1", "2")
        
    Returns:
        tuple: (major, minor, patch) as integers, or (0, 0, 0) if invalid
    """
    if not version_str:
        return (0, 0, 0)
    
    # Remove prefix if present
    v = str(version_str).upper().replace(VERSION_PREFIX, '').strip()
    
    # Split by separator
    parts = v.split(VERSION_SEPARATOR)
    
    try:
        major = int(parts[0]) if parts[0] else 0
        minor = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        patch = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        # If parsing fails, return invalid version (sorts to end)
        return (999999, 0, 0)


def get_version_sort_key(version_str, descending_major=True):
    """
    Get sort key for version string.
    
    RISK REMOVAL: Replaces duplicated get_version_sort_key() function.
    
    Args:
        version_str: Version string to sort
        descending_major: If True, newer versions sort first (negative major)
        
    Returns:
        tuple: Sort key tuple (negative_major, minor, patch) for descending order
    """
    major, minor, patch = parse_version(version_str)
    
    if descending_major:
        # Negative major for descending order (newest first)
        return (-major, minor, patch)
    else:
        # Positive major for ascending order (oldest first)
        return (major, minor, patch)


def sort_versions(versions, descending=True):
    """
    Sort list of version strings.
    
    RISK REMOVAL: Replaces duplicated sort_versions() function in views.py.
    
    Args:
        versions: Iterable of version strings
        descending: If True, newest versions first
        
    Returns:
        list: Sorted list of versions
    """
    sorted_versions = sorted(
        versions,
        key=lambda v: get_version_sort_key(v, descending_major=descending)
    )
    return sorted_versions


def get_latest_versions(sw_part_numbers=None, active_instance=None):
    """
    Get latest version for each SW Part Number from SWVersionMapping.
    
    STRICT VERSION LIFECYCLE: Only returns versions with is_active=True.
    This ensures only the most recent version is returned, enforcing strict
    version lifecycle control where only the latest version is visible and editable.
    
    PART 1 & 3: Version mappings are instance-aware. Only return versions from active instance.
    
    Args:
        sw_part_numbers: Optional list of SW part numbers to filter by
        active_instance: TestInstance object (required)
        
    Returns:
        dict: {sw_part_number: version_string}
    """
    if not active_instance:
        return {}
    
    latest_versions = {}
    
    if sw_part_numbers:
        for sw_num in sw_part_numbers:
            if not sw_num:
                continue
            
            # Get active version from SWVersionMapping
            mapping = SWVersionMapping.objects.filter(
                instance=active_instance,
                sw_part_number=sw_num,
                is_active=True
            ).first()
            
            if mapping:
                latest_versions[sw_num] = mapping.version
    else:
        # Get all active versions for active instance
        mappings = SWVersionMapping.objects.filter(
            instance=active_instance,
            is_active=True
        )
        
        for mapping in mappings:
            latest_versions[mapping.sw_part_number] = mapping.version
    
    return latest_versions


def get_versions_for_user(user, active_instance, sw_part_number=None):
    """
    Get versions accessible to a user based on their role.
    
    SECURITY CRITICAL: This function enforces role-based version access at query level.
    
    Manager:
        - Returns ALL versions (active + inactive)
        - Can access historical data
    
    Non-manager (Tester/Developer):
        - Returns ONLY active versions (is_active=True)
        - Cannot access historical data
    
    Args:
        user: User object
        active_instance: TestInstance object
        sw_part_number: Optional SW part number to filter by
        
    Returns:
        QuerySet: Filtered TestCaseVersion queryset
    """
    from .decorators import is_manager
    
    # Check if user is manager
    is_manager_check = is_manager(user)
    if user.is_superuser:
        is_manager_check = True
    
    # Build base query
    qs = TestCaseVersion.objects.filter(instance=active_instance)
    
    if sw_part_number:
        qs = qs.filter(sw_part_number=sw_part_number)
    
    # CRITICAL: Apply role-based filtering
    if not is_manager_check:
        # Non-manager: ONLY active versions
        qs = qs.filter(is_active=True)
    
    # Always order by newest first (descending created_at)
    return qs.order_by('-created_at')


def get_active_version_for_user(user, active_instance, sw_part_number):
    """
    Get the active version for a user.
    
    For non-managers, this returns the ONLY version they can access.
    For managers, this returns the most recent active version.
    
    Args:
        user: User object
        active_instance: TestInstance object
        sw_part_number: SW part number
        
    Returns:
        TestCaseVersion or None
    """
    versions = get_versions_for_user(user, active_instance, sw_part_number)
    # For non-managers, filter to active only (already done in get_versions_for_user)
    # For managers, get the most recent active version
    return versions.filter(is_active=True).first()


def can_user_access_version(user, version_obj):
    """
    Check if a user can access a specific version.
    
    SECURITY CRITICAL: This function enforces access control at query level.
    
    Args:
        user: User object
        version_obj: TestCaseVersion object
        
    Returns:
        bool: True if user can access the version, False otherwise
    """
    from .decorators import is_manager
    
    # Check if user is manager
    is_manager_check = is_manager(user)
    if user.is_superuser:
        is_manager_check = True
    
    # Managers can access all versions
    if is_manager_check:
        return True
    
    # Non-managers can ONLY access active versions
    return version_obj.is_active


def can_user_execute_version(user, version_obj):
    """
    Check if a user can create/edit executions for a specific version.
    
    SECURITY CRITICAL: This function enforces execution access control.
    
    Args:
        user: User object
        version_obj: TestCaseVersion object
        
    Returns:
        tuple: (can_execute: bool, reason: str)
    """
    from .decorators import is_manager
    
    # Check if user is manager
    is_manager_check = is_manager(user)
    if user.is_superuser:
        is_manager_check = True
    
    # Check if version is locked
    if version_obj.is_locked:
        return False, "This version is locked and cannot be modified."
    
    # Non-managers can ONLY execute active versions
    if not is_manager_check:
        if not version_obj.is_active:
            return False, "Cannot create executions for inactive versions. Please select an active version."
    
    return True, ""


def sort_test_cases_by_version(test_cases):
    """
    Sort test cases by: Sheet → Version (newest first) → id (ascending).
    
    RISK REMOVAL: Replaces duplicated sort_test_cases_by_version() function.
    
    STRICT: sl_no does NOT exist on TestCase - removed after hierarchy refactor.
    Use id (primary key) instead for consistent ordering.
    
    Args:
        test_cases: List or queryset of TestCase objects
        
    Returns:
        list: Sorted list of test cases
    """
    def sort_key(tc):
        # 1. First sort by sheet_name (alphabetically)
        # STRICT: sheet_name does NOT exist on TestCase - use relationship if available
        sheet_name = getattr(tc, 'sheet_name', '')
        
        # 2. Then sort by version (newest first)
        # STRICT: app_sw_version does NOT exist on TestCase - get from execution or version FK
        version_str = ""
        if hasattr(tc, 'exec') and tc.exec and tc.exec.version:
            version_str = tc.exec.version.app_sw_version
        elif hasattr(tc, 'app_sw_version'):
            version_str = tc.app_sw_version
        
        version_key = get_version_sort_key(version_str, descending_major=True)
        
        # 3. Finally sort by id (ascending)
        return (sheet_name, version_key, tc.id)
    
    return sorted(test_cases, key=sort_key)


def get_base_test_case_id(test_case_id, app_sw_version):
    """
    Remove version suffix from test_case_id if present.
    
    RISK REMOVAL: This logic was duplicated in:
    - testcase_list(), view_test_execution(), excel_export.py
    
    Args:
        test_case_id: Full test case ID (may include version suffix)
        app_sw_version: Application SW version
        
    Returns:
        str: Test case ID without version suffix
    """
    if not test_case_id:
        return test_case_id
    
    # Check if it ends with version suffix
    if app_sw_version and test_case_id.endswith(f"_{app_sw_version}"):
        return test_case_id[:-len(f"_{app_sw_version}")]
    
    # Also check if it ends with underscore and any version-like pattern
    if "_" in test_case_id:
        parts = test_case_id.rsplit("_", 1)
        if len(parts) == 2:
            # Check if second part looks like a version (contains dot or is numeric)
            if VERSION_SEPARATOR in parts[1] or parts[1].replace(VERSION_SEPARATOR, "").isdigit():
                return parts[0]
    
    return test_case_id


def filter_queryset_by_latest_versions(qs, sw_part_numbers=None, include_empty_versions=False, active_instance=None):
    """
    Filter TestCase queryset to only include test cases from active versions.
    
    SECURITY CRITICAL: This function enforces filtering to active versions only.
    Used for non-managers who should only see active version test cases.
    
    Args:
        qs: TestCase QuerySet to filter
        sw_part_numbers: Optional list of SW part numbers to filter by
        include_empty_versions: If True, include test cases without versions
        active_instance: TestInstance object (required)
        
    Returns:
        QuerySet: Filtered TestCase queryset containing only active version test cases
    """
    if not active_instance:
        from .models import TestInstance
        active_instance = TestInstance.objects.filter(is_active=True).first()
        if not active_instance:
            # Return empty queryset if no active instance
            return qs.none()
    
    # Get active TestCaseVersion objects
    version_qs = TestCaseVersion.objects.filter(
        instance=active_instance,
        is_active=True
    )
    
    if sw_part_numbers:
        version_qs = version_qs.filter(sw_part_number__in=sw_part_numbers)
    
    # Build filter conditions: match (sw_part_number, app_sw_version) pairs
    version_filters = []
    for version_obj in version_qs:
        version_filters.append(
            Q(sw_part_number=version_obj.sw_part_number) &
            Q(app_sw_version=version_obj.app_sw_version)
        )
    
    if version_filters:
        # Combine all version filters with OR
        combined_filter = reduce(or_, version_filters)
        qs = qs.filter(combined_filter)
    elif not include_empty_versions:
        # If no active versions found and we shouldn't include empty, return empty queryset
        qs = qs.none()
    
    return qs


def is_version_current(app_sw_version, sw_part_number, active_instance=None):
    """
    Check if a version is the current active version for a SW part number.
    
    Args:
        app_sw_version: Application SW version string
        sw_part_number: SW part number
        active_instance: TestInstance object (optional)
        
    Returns:
        bool: True if version is active, False otherwise
    """
    if not active_instance:
        from .models import TestInstance
        active_instance = TestInstance.objects.filter(is_active=True).first()
        if not active_instance:
            return False
    
    # Check if version exists and is active
    version_obj = TestCaseVersion.objects.filter(
        instance=active_instance,
        sw_part_number=sw_part_number,
        app_sw_version=app_sw_version,
        is_active=True
    ).first()
    
    return version_obj is not None


def get_execution_for_version(test_case, sw_part_number, app_sw_version):
    """
    Get TestExecution for a specific test case and version.
    
    SECURITY CRITICAL: This function filters by instance and version FK.
    
    Args:
        test_case: TestCase object
        sw_part_number: SW part number
        app_sw_version: Application SW version
        
    Returns:
        TestExecution or None
    """
    from .models import TestExecution
    from .services import get_active_instance
    
    active_instance = get_active_instance()
    
    # Get version object
    version_obj = TestCaseVersion.objects.filter(
        instance=active_instance,
        sw_part_number=sw_part_number,
        app_sw_version=app_sw_version
    ).first()
    
    if not version_obj:
        return None
    
    # Get execution using explicit version FK
    execution = TestExecution.objects.filter(
        instance=active_instance,
        test_case=test_case,
        version=version_obj
    ).first()
    
    return execution


def can_edit_execution_version(user, execution, version_obj):
    """
    Check if a user can edit an execution for a specific version.
    
    SECURITY CRITICAL: This function enforces execution edit permissions.
    
    Args:
        user: User object
        execution: TestExecution object (can be None)
        version_obj: TestCaseVersion object
        
    Returns:
        tuple: (can_edit: bool, reason: str)
    """
    # Check if execution is locked
    if execution and execution.is_locked:
        return False, "This execution is locked and cannot be modified."
    
    # Check version permissions
    can_execute, reason = can_user_execute_version(user, version_obj)
    return can_execute, reason


def filter_execution_queryset_by_latest_versions(qs, sw_part_numbers=None, active_instance=None):
    """
    Filter TestExecution queryset to only include executions from active versions.
    
    SECURITY CRITICAL: This function enforces filtering to active versions only.
    Used for non-managers who should only see active version executions.
    
    Args:
        qs: TestExecution QuerySet to filter
        sw_part_numbers: Optional list of SW part numbers to filter by
        active_instance: TestInstance object (required)
        
    Returns:
        QuerySet: Filtered TestExecution queryset containing only active version executions
    """
    if not active_instance:
        from .models import TestInstance
        active_instance = TestInstance.objects.filter(is_active=True).first()
        if not active_instance:
            # Return empty queryset if no active instance
            return qs.none()
    
    # Get active TestCaseVersion objects
    version_qs = TestCaseVersion.objects.filter(
        instance=active_instance,
        is_active=True
    )
    
    if sw_part_numbers:
        version_qs = version_qs.filter(sw_part_number__in=sw_part_numbers)
    
    # Filter executions by version FK (explicit FK relationship)
    version_ids = list(version_qs.values_list('id', flat=True))
    
    if version_ids:
        qs = qs.filter(version_id__in=version_ids)
    else:
        # If no active versions found, return empty queryset
        qs = qs.none()
    
    return qs
