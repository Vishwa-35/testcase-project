"""
Business Logic Services

RISK REMOVAL: Extracts business logic from views to improve separation of concerns.
Views should be thin (request → service → response), not contain complex filtering/processing.
"""

from functools import reduce
from operator import or_
from django.db.models import Q, Case, When, IntegerField, Value
from django.db.models.functions import Cast
from django.core.paginator import Paginator

from .models import TestCase, TestExecution, ProjectOverview, SWVersionMapping, TestInstance, TestCaseVersion
from .constants import (
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_NOT_EXECUTED,
    STATUS_NOT_EXECUTED_ALIASES,
    ITEMS_PER_PAGE,
    PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP,
)
from .version_service import (
    get_latest_versions,
    filter_queryset_by_latest_versions,
    filter_execution_queryset_by_latest_versions,
    sort_versions,
)
from .logging_utils import log_debug


def get_active_instance():
    """
    Get the currently active test instance.
    Creates one if none exists.
    
    Returns:
        TestInstance: The active test instance
    """
    instance = TestInstance.objects.filter(is_active=True).first()
    if not instance:
        # Create the first active instance if none exists
        instance = TestInstance.objects.create(is_active=True)
    return instance


def get_current_active_version(sw_part_number):
    """
    Get the current active (most recent) version for a SW part number.
    
    RULE 1: When creating a new test case, use ONLY the most recent version
    of the active instance. This ensures new test cases are NEVER created in past versions.
    
    Version determination priority:
    1. SWVersionMapping (if exists and version exists in active instance) - most recent updated_at
    2. Most recently created TestCase in active instance (by created_at timestamp)
    
    Args:
        sw_part_number: SW Part Number to get version for
        
    Returns:
        str: Current active version, or empty string if none exists
    """
    if not sw_part_number:
        return ""
    
    # Get active instance
    active_instance = get_active_instance()
    
    # Priority 1: Check SWVersionMapping for the active version (is_active=True)
    # PART 3: SWVersionMapping is now instance-aware - only check active instance mappings
    mapping = SWVersionMapping.objects.filter(
        instance=active_instance,  # PART 3: Only active instance mappings
        sw_part_number=sw_part_number,
        is_active=True  # Only get active version
    ).first()
    
    if mapping:
        # Verify this version exists in active instance (has test cases)
        version_exists = TestCase.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=mapping.version
        ).exists()
        
        if version_exists:
            return mapping.version
    
    # Priority 2: Get the most recently created version from TestCase in active instance
    # Group by version and get the most recent created_at for each version
    test_cases = TestCase.objects.filter(
        instance=active_instance,
        sw_part_number=sw_part_number
    ).exclude(app_sw_version__isnull=True).exclude(app_sw_version__exact="")
    
    if not test_cases.exists():
        return ""
    
    # Group by version and find the most recent created_at timestamp for each version
    version_timestamps = {}
    for tc in test_cases:
        version = tc.app_sw_version
        if version:
            if version not in version_timestamps:
                version_timestamps[version] = tc.created_at
            elif tc.created_at and version_timestamps[version]:
                if tc.created_at > version_timestamps[version]:
                    version_timestamps[version] = tc.created_at
    
    if version_timestamps:
        # Find version with most recent timestamp
        # This ensures we use the version that was most recently created
        most_recent_version = max(
            version_timestamps.items(),
            key=lambda x: x[1] if x[1] else x[0]
        )[0]
        return most_recent_version
    
    return ""


def build_search_filters(query):
    """
    Build Django Q() filters for search query.
    
    RISK REMOVAL: Search filter building was duplicated in:
    - testcase_list(), export_html(), excel_export.py
    
    Args:
        query: Search query string
        
    Returns:
        Q: Django Q object for filtering queryset
    """
    if not query:
        return Q()
    
    search_filters = [
        # STRICT: sl_no does NOT exist - removed from search fields
        # Q(sl_no__icontains=query),  # Removed - sl_no field doesn't exist
        Q(sw_part_number__icontains=query),
        Q(feature__icontains=query),
        Q(requirement_id__icontains=query),
        Q(requirement_description__icontains=query),
        Q(test_case_id__icontains=query),
        Q(test_case_summary__icontains=query),
        Q(pre_conditions__icontains=query),
        Q(inputs__icontains=query),
        Q(periodic_time__icontains=query),
        Q(test_steps__icontains=query),
        Q(expected_result__icontains=query),
        Q(status__icontains=query),
        Q(reports__icontains=query),
        Q(comments__icontains=query)
    ]
    return reduce(or_, search_filters)


def build_testcase_queryset(filters):
    """
    Build filtered and annotated TestCase queryset.
    
    RISK REMOVAL: Queryset building logic was duplicated across multiple views.
    This centralizes the logic and ensures consistent filtering.
    
    CRITICAL: Do NOT use legacy fields (sheet_name, app_sw_version) for filtering.
    Use FK relationships: TestCaseSheet.sheet_name, TestCaseVersion.app_sw_version
    
    Args:
        filters: Dict with keys: sheet, sw, version, feature, query, latest_versions_only, version_obj
        
    Returns:
        QuerySet: Filtered and annotated TestCase queryset
    """
    qs = TestCase.objects.all()
    active_instance = get_active_instance()
    
    # Apply basic filters
    # CRITICAL: Sheet filtering must use TestCaseSheet FK relationship
    # TestCase doesn't have direct FK to TestCaseSheet, so we match via TestCaseVersion
    if filters.get('sheet'):
        from .models import TestCaseSheet
        # Find TestCaseSheet objects with this sheet_name
        sheets = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=filters['sheet']
        ).select_related('version')
        # Get versions from these sheets
        versions = [sheet.version for sheet in sheets]
        # Filter TestCase by matching sw_part_number + app_sw_version to these versions
        if versions:
            version_filters = reduce(
                or_,
                [
                    Q(
                        instance=active_instance,
                        sw_part_number=version.sw_part_number,
                        app_sw_version=version.app_sw_version
                    )
                    for version in versions
                ]
            )
            qs = qs.filter(version_filters)
    
    if filters.get('sw'):
        qs = qs.filter(sw_part_number=filters['sw'])
    
    if filters.get('feature'):
        qs = qs.filter(feature=filters['feature'])
    
    # Apply version filtering
    # CRITICAL: Version filtering must use TestCaseVersion FK relationship
    if filters.get('version_obj'):
        # Use explicit version FK object
        version_obj = filters['version_obj']
        qs = qs.filter(
            instance=version_obj.instance,
            sw_part_number=version_obj.sw_part_number,
            app_sw_version=version_obj.app_sw_version
        )
    elif filters.get('latest_versions_only', False):
        # Filter to latest versions only (active versions)
        sw_part_numbers = list(qs.values_list('sw_part_number', flat=True).distinct())
        sw_part_numbers = [sw for sw in sw_part_numbers if sw]
        if sw_part_numbers:
            qs = filter_queryset_by_latest_versions(qs, sw_part_numbers, include_empty_versions=True, active_instance=active_instance)
    elif filters.get('version'):
        # CRITICAL: Do NOT filter by app_sw_version CharField directly
        # Instead, resolve version_obj first and filter by FK
        from .models import TestCaseVersion
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            app_sw_version=filters['version']
        ).first()
        if version_obj:
            qs = qs.filter(
                instance=version_obj.instance,
                sw_part_number=version_obj.sw_part_number,
                app_sw_version=version_obj.app_sw_version
            )
    
    # Apply search query
    if filters.get('query'):
        search_filter = build_search_filters(filters['query'])
        qs = qs.filter(search_filter)
    
    # STRICT: sl_no does NOT exist - removed after hierarchy refactor
    # Order by id (primary key) instead of deprecated sl_no field
    qs = qs.order_by("id")
    
    return qs


def get_execution_map(test_case_ids, sw_part_numbers=None, version_obj=None, active_instance=None):
    """
    Build execution map for test cases.
    
    INSTANCE ISOLATION: Execution mapping must filter by instance to ensure
    instance isolation. Version alone is NOT sufficient.
    
    RISK REMOVAL: Execution mapping logic was duplicated and caused N+1 queries.
    This function uses prefetch_related to optimize queries.
    
    CRITICAL: Do NOT use legacy fields (app_sw_version CharField) for filtering.
    Use version FK relationship only.
    
    Args:
        test_case_ids: List of test case IDs
        sw_part_numbers: Optional list of SW part numbers to filter
        version_obj: Optional TestCaseVersion object to filter by (preferred over app_sw_version)
        active_instance: Optional TestInstance to filter by (if None, uses get_active_instance())
        
    Returns:
        dict: {(test_case_id, version_id): execution} mapping (using version FK ID, not CharField)
    """
    if not test_case_ids:
        return {}
    
    # CRITICAL: Get active instance if not provided
    if active_instance is None:
        active_instance = get_active_instance()
    
    # INSTANCE ISOLATION: Filter by instance to ensure instance isolation
    executions = TestExecution.objects.filter(
        test_case_id__in=test_case_ids,
        instance=active_instance  # CRITICAL: Filter by instance
    ).select_related('test_case', 'version', 'user')
    
    if sw_part_numbers:
        # CRITICAL: Filter by version FK's sw_part_number, not execution's legacy field
        executions = executions.filter(version__sw_part_number__in=sw_part_numbers)
    
    if version_obj:
        # CRITICAL: Filter by version FK, not legacy app_sw_version CharField
        executions = executions.filter(version=version_obj)
    
    execution_map = {}
    for exec_obj in executions:
        # CRITICAL: Verify instance matches (extra safety)
        if exec_obj.instance != active_instance:
            continue
        # CRITICAL: Use version FK ID in key, not legacy CharField values
        version_id = exec_obj.version_id if exec_obj.version else None
        key = (exec_obj.test_case_id, version_id)
        execution_map[key] = exec_obj
    
    return execution_map


def get_status_distribution(execution_queryset, test_case_queryset=None):
    """
    Calculate status distribution from executions.
    
    RISK REMOVAL: Status grouping logic was duplicated in home() and export_html().
    
    Args:
        execution_queryset: TestExecution queryset
        test_case_queryset: Optional TestCase queryset for counting non-executed tests
        
    Returns:
        dict: {status: count} mapping
    """
    status_map = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_NOT_EXECUTED: 0, "OTHER": 0}
    
    for exec_obj in execution_queryset:
        status_upper = (exec_obj.status or "").strip().upper()
        if status_upper == STATUS_PASS:
            status_map[STATUS_PASS] += 1
        elif status_upper == STATUS_FAIL:
            status_map[STATUS_FAIL] += 1
        elif status_upper in [alias.upper() for alias in STATUS_NOT_EXECUTED_ALIASES]:
            status_map[STATUS_NOT_EXECUTED] += 1
        else:
            status_map["OTHER"] += 1
    
    # Count test cases without executions
    if test_case_queryset is not None:
        test_cases_with_executions = execution_queryset.values_list('test_case_id', flat=True).distinct()
        test_cases_without_executions = test_case_queryset.exclude(id__in=test_cases_with_executions).count()
        status_map[STATUS_NOT_EXECUTED] += test_cases_without_executions
    
    return status_map


def get_feature_completion(instance, version_obj, sheet_obj, feature_name):
    """
    Get completion statistics for a specific feature scope.
    
    Args:
        instance: TestInstance object
        version_obj: TestCaseVersion object
        sheet_obj: TestCaseSheet object
        feature_name: Feature name string
        
    Returns:
        tuple: (total_count: int, completed_count: int, is_completed: bool)
            - total_count: Total number of test cases for this feature scope
            - completed_count: Number of test cases with PASS/FAIL status
            - is_completed: True if all tests are PASS/FAIL and version is locked
    """
    if not instance or not version_obj or not sheet_obj or not feature_name:
        return 0, 0, False
    
    # Get all test cases for this feature, version, and sheet
    test_cases = TestCase.objects.filter(
        instance=instance,
        feature=feature_name,
        sw_part_number=version_obj.sw_part_number,
        app_sw_version=version_obj.app_sw_version,
        sheet_name=sheet_obj.sheet_name
    )
    
    total_count = test_cases.count()
    if total_count == 0:
        return 0, 0, False
    
    # Get all executions for these test cases, version, and sheet
    executions = TestExecution.objects.filter(
        instance=instance,
        version=version_obj,
        sheet=sheet_obj,
        test_case__feature=feature_name,
        test_case__in=test_cases
    )
    
    # Count executions with PASS or FAIL status
    completed_count = executions.filter(
        status__in=["PASS", "FAIL"]
    ).count()
    
    # Check if version is locked (manager approval required)
    version_locked = version_obj.is_locked
    
    # Check if all test cases have executions
    test_case_ids_with_executions = set(executions.values_list('test_case_id', flat=True))
    test_case_ids_all = set(test_cases.values_list('id', flat=True))
    all_test_cases_executed = (test_case_ids_all == test_case_ids_with_executions)
    
    # Feature is completed if: all tests executed, all have PASS/FAIL, and version is locked
    is_completed = (
        completed_count == total_count and
        all_test_cases_executed and
        version_locked
    )
    
    return total_count, completed_count, is_completed


def is_feature_completed(instance, version_obj, sheet_obj, feature_name):
    """
    Check if a specific feature is completed for a given instance, version, and sheet.
    
    A FEATURE is considered COMPLETED when:
    - For a given feature, TestCaseSheet, SW Part Number, TestCaseVersion
    - ALL related TestExecution rows satisfy:
        - status IN (PASS / FAIL)
        - version.is_locked = True (manager approval)
    
    NOT allowed:
    - Partial executions
    - NOT_EXECUTED rows
    - Unlocked version
    
    Args:
        instance: TestInstance object
        version_obj: TestCaseVersion object
        sheet_obj: TestCaseSheet object
        feature_name: Feature name string
        
    Returns:
        tuple: (is_completed: bool, executed_count: int, total_count: int)
    """
    # Use the new helper function
    total_count, completed_count, is_completed = get_feature_completion(
        instance, version_obj, sheet_obj, feature_name
    )
    return is_completed, completed_count, total_count


def check_all_tests_completed(sheet_filter="", sw="", version_obj=None):
    """
    Check if all test cases have been executed for the active instance.
    
    RISK REMOVAL: This logic was in views.py as _check_all_tests_completed().
    Moved to services for better separation of concerns.
    
    CRITICAL: Do NOT use legacy fields (sheet_name, app_sw_version CharField) for filtering.
    Use FK relationships only.
    
    Args:
        sheet_filter: Optional sheet name filter (use TestCaseSheet FK)
        sw: Optional SW part number filter
        version_obj: Optional TestCaseVersion object to filter by (preferred over version string)
        
    Returns:
        tuple: (all_completed: bool, executed_count: int, total_count: int)
    """
    # Get active instance - only check tests for active instance
    active_instance = get_active_instance()
    
    qs = TestCase.objects.filter(instance=active_instance)
    
    # CRITICAL: Sheet filtering must use TestCaseSheet FK relationship
    # TestCase doesn't have direct FK to TestCaseSheet, so we match via TestCaseVersion
    if sheet_filter:
        from .models import TestCaseSheet
        # Find TestCaseSheet objects with this sheet_name
        sheets = TestCaseSheet.objects.filter(
            version__instance=active_instance,
            sheet_name=sheet_filter
        ).select_related('version')
        # Get versions from these sheets and filter TestCase by matching sw_part_number + app_sw_version
        if sheets.exists():
            version_filters = reduce(
                or_,
                [
                    Q(
                        instance=active_instance,
                        sw_part_number=sheet.version.sw_part_number,
                        app_sw_version=sheet.version.app_sw_version
                    )
                    for sheet in sheets
                ]
            )
            qs = qs.filter(version_filters)
    
    if sw:
        qs = qs.filter(sw_part_number=sw)
    
    # CRITICAL: Version filtering must use TestCaseVersion FK relationship
    if version_obj:
        qs = qs.filter(
            instance=version_obj.instance,
            sw_part_number=version_obj.sw_part_number,
            app_sw_version=version_obj.app_sw_version
        )
    
    total_tests = qs.count()
    if total_tests == 0:
        return False, 0, 0
    
    # Get executions for these test cases (active instance only)
    # CRITICAL: Filter executions by version FK, not legacy app_sw_version CharField
    execution_qs = TestExecution.objects.filter(
        instance=active_instance,
        test_case__in=qs
    ).select_related('version')
    
    if sw:
        # CRITICAL: Filter by version FK's sw_part_number
        execution_qs = execution_qs.filter(version__sw_part_number=sw)
    
    if version_obj:
        # CRITICAL: Filter by version FK, not legacy CharField
        execution_qs = execution_qs.filter(version=version_obj)
    
    # Count executions with non-empty status
    executed_count = execution_qs.exclude(status__isnull=True).exclude(status__exact="").count()
    
    return executed_count == total_tests, executed_count, total_tests


def paginate_queryset(queryset, page_number, per_page=None):
    """
    Paginate queryset.
    
    RISK REMOVAL: Pagination logic was duplicated in testcase_list().
    
    Args:
        queryset: QuerySet to paginate
        page_number: Page number (from request.GET)
        per_page: Items per page (defaults to constant)
        
    Returns:
        Paginator page object
    """
    if per_page is None:
        per_page = ITEMS_PER_PAGE
    
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(page_number)
    return page_obj


def recalculate_version_status(version_obj):
    """
    Recalculate and update execution_status for a TestCaseVersion based on its TestExecution records.
    
    STATUS RULES:
    - NOT_STARTED: No executions exist
    - IN_PROGRESS: Some tests executed, but not all (or some have empty status)
    - COMPLETED: All tests have non-empty status
    - APPROVED: Cannot be changed (set manually by manager)
    
    Args:
        version_obj: TestCaseVersion instance to update
        
    Returns:
        str: The new status that was set
    """
    if not version_obj:
        return None
    
    # CRITICAL: APPROVED status is immutable - never recalculate
    if version_obj.execution_status == 'APPROVED':
        return 'APPROVED'
    
    # Get all test cases for this version (via sheets)
    from .models import TestCaseSheet
    sheets = TestCaseSheet.objects.filter(version=version_obj)
    if not sheets.exists():
        # No sheets = no test cases = NOT_STARTED
        version_obj.execution_status = 'NOT_STARTED'
        version_obj.save(update_fields=['execution_status'])
        return 'NOT_STARTED'
    
    # CRITICAL: Get all test cases that belong to this version
    # Use version FK relationship via sheets to get test cases
    # Note: TestCase still has app_sw_version for backward compatibility, but filtering
    # should prefer TestCaseSheet relationship when available
    from .models import TestCaseSheet
    sheets = TestCaseSheet.objects.filter(version=version_obj)
    sheet_names = list(sheets.values_list('sheet_name', flat=True).distinct())
    
    test_cases = TestCase.objects.filter(
        instance=version_obj.instance,
        sw_part_number=version_obj.sw_part_number,
        app_sw_version=version_obj.app_sw_version,
        sheet_name__in=sheet_names  # Filter by sheet names that belong to this version
    )
    
    if not test_cases.exists():
        version_obj.execution_status = 'NOT_STARTED'
        version_obj.save(update_fields=['execution_status'])
        return 'NOT_STARTED'
    
    # Get all executions for this version with non-empty status
    executions = TestExecution.objects.filter(
        version=version_obj
    ).exclude(status__in=['', None])
    
    # Count unique test cases that have been executed
    executed_test_case_ids = executions.values_list('test_case__id', flat=True).distinct()
    total_test_case_ids = set(test_cases.values_list('id', flat=True))
    executed_test_case_ids_set = set(executed_test_case_ids)
    
    total_count = len(total_test_case_ids)
    executed_count = len(executed_test_case_ids_set)
    
    if executed_count == 0:
        # No executions yet
        new_status = 'NOT_STARTED'
    elif executed_count < total_count:
        # Some tests executed, but not all
        new_status = 'IN_PROGRESS'
    else:
        # All tests executed
        new_status = 'COMPLETED'
    
    # Only update if status changed
    if version_obj.execution_status != new_status:
        version_obj.execution_status = new_status
        version_obj.save(update_fields=['execution_status'])
    
    return new_status


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE-BASED EXPORT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def check_feature_completion(feature_name, version_obj, active_instance=None):
    """
    Check if a feature is fully completed (all test cases under that feature are completed).
    
    A feature is completed if:
    - ALL test cases under that feature have executions with non-empty status
    - Across ALL sheets and ALL SW part numbers
    
    Args:
        feature_name: Name of the feature to check
        version_obj: TestCaseVersion object to check completion for
        active_instance: Optional TestInstance (defaults to get_active_instance())
        
    Returns:
        tuple: (is_completed: bool, executed_count: int, total_count: int)
    """
    if active_instance is None:
        active_instance = get_active_instance()
    
    if not feature_name or not version_obj:
        return False, 0, 0
    
    # Get all test cases for this feature and version
    # Filter by feature name, version's sw_part_number, and version's app_sw_version
    test_cases = TestCase.objects.filter(
        instance=active_instance,
        feature=feature_name,
        sw_part_number=version_obj.sw_part_number,
        app_sw_version=version_obj.app_sw_version
    )
    
    total_count = test_cases.count()
    if total_count == 0:
        return False, 0, 0
    
    # Get executions for these test cases using version FK
    test_case_ids = list(test_cases.values_list('id', flat=True))
    executions = TestExecution.objects.filter(
        instance=active_instance,
        version=version_obj,
        test_case_id__in=test_case_ids
    ).exclude(status__isnull=True).exclude(status__exact="")
    
    executed_count = executions.values_list('test_case_id', flat=True).distinct().count()
    is_completed = executed_count == total_count
    
    return is_completed, executed_count, total_count


def get_all_features(active_instance=None):
    """
    Get all unique feature names from the active instance.
    
    Args:
        active_instance: Optional TestInstance (defaults to get_active_instance())
        
    Returns:
        list: Sorted list of unique feature names
    """
    if active_instance is None:
        active_instance = get_active_instance()
    
    features = TestCase.objects.filter(
        instance=active_instance
    ).exclude(feature__isnull=True).exclude(feature__exact="").values_list('feature', flat=True).distinct()
    
    return sorted([f.strip() for f in features if f and f.strip()])


def get_exportable_features_for_versions(version_objs, active_instance=None):
    """
    Get all features with their completion status PER VERSION.
    
    CRITICAL: Feature completion is VERSION-SCOPED. Never aggregate across versions.
    
    Returns structure:
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
              "completed": <true|false>
            }
          ]
        }
      ]
    }
    
    Args:
        version_objs: List of TestCaseVersion objects
        active_instance: Optional TestInstance (defaults to get_active_instance())
        
    Returns:
        list: List of dicts with keys: name, versions (list of version dicts)
    """
    if active_instance is None:
        active_instance = get_active_instance()
    
    if not version_objs:
        return []
    
    # Get all unique features across all versions
    all_features = get_all_features(active_instance)
    
    # Build feature -> versions mapping
    features_data = []
    for feature_name in all_features:
        versions_list = []
        
        # Check completion for EACH version separately (no aggregation)
        for version_obj in version_objs:
            # Check if this feature exists for this version
            test_cases = TestCase.objects.filter(
                instance=active_instance,
                feature=feature_name,
                sw_part_number=version_obj.sw_part_number,
                app_sw_version=version_obj.app_sw_version
            )
            total_count = test_cases.count()
            
            if total_count == 0:
                # Feature doesn't exist for this version, skip
                continue
            
            # Get executions for this feature+version
            test_case_ids = list(test_cases.values_list('id', flat=True))
            executions = TestExecution.objects.filter(
                instance=active_instance,
                version=version_obj,
                test_case_id__in=test_case_ids
            ).exclude(status__isnull=True).exclude(status__exact="")
            
            executed_count = executions.values_list('test_case_id', flat=True).distinct().count()
            
            # Feature is completed for this version if all test cases are executed
            is_completed = (total_count > 0 and executed_count == total_count)
            
            versions_list.append({
                'version': version_obj.app_sw_version,
                'sw_part_number': version_obj.sw_part_number,
                'version_id': version_obj.id,
                'total': total_count,
                'executed': executed_count,
                'completed': is_completed,
            })
        
        # Only include feature if it has at least one version
        if versions_list:
            features_data.append({
                'name': feature_name,
                'versions': versions_list,
            })
    
    return features_data


def is_feature_version_exported(feature_name, version_obj, active_instance=None):
    """
    Check if a feature+version combination has already been exported.
    
    A feature+version is considered exported if there's a snapshot that:
    - Matches the version (app_sw_version and sw_part_number)
    - Has notes field containing exported_features list with this feature
    - OR has execution_data containing test cases with this feature
    
    Args:
        feature_name: Name of the feature
        version_obj: TestCaseVersion object
        active_instance: Optional TestInstance (defaults to get_active_instance())
        
    Returns:
        bool: True if already exported, False otherwise
    """
    import json as json_lib
    
    if active_instance is None:
        active_instance = get_active_instance()
    
    if not feature_name or not version_obj:
        return False
    
    # Check snapshots for this version
    snapshots = TestExecution.objects.filter(
        instance=active_instance,
        app_sw_version=version_obj.app_sw_version,
        sw_part_number=version_obj.sw_part_number,
        feature=feature_name
    )
    
    # Check if any snapshot contains this feature
    for snapshot in snapshots:
        # First check notes field for exported_features
        if snapshot.notes:
            try:
                notes_data = json_lib.loads(snapshot.notes)
                exported_features = notes_data.get('exported_features', [])
                if feature_name in exported_features:
                    return True
            except (json_lib.JSONDecodeError, TypeError):
                pass
        
        # Also check execution_data for feature matches (backward compatibility)
        if snapshot.execution_data:
            for exec_data in snapshot.execution_data:
                exec_feature = exec_data.get('feature', '').strip()
                if exec_feature == feature_name.strip():
                    # Found a matching feature in this snapshot - version already matches from filter
                    return True
    
    return False

