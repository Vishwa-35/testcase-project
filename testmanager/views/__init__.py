"""
Views Package - Aggregates all view functions

This module re-exports all view functions from their respective modules
to maintain backward compatibility with existing imports.

RISK REMOVAL: Split large views.py (3020 lines) into smaller, maintainable modules
organized by functionality. All functions remain unchanged internally.
"""

# Home and dashboard views
from .home import home, post_login_redirect, get_project_overview, save_project_overview

# Test case management views
from .testcases import (
    testcase_list,
    testcase_add,
    testcase_test_it,
    view_test_execution,
    create_testcases,
    create_new_version,
    get_next_sl_no_api,
)

# Excel import views
from .import_views import (
    upload_excel,
    input_versions,
    import_excel,
    import_full_excel,
)

# Export views
from .export_views import (
    export_excel,
    export_html,
    export_html_snapshot,
    get_sheets_api,
    get_versions_for_sheet_api,
    get_features_for_selection_api,
    get_completed_features_for_selection_api,
    get_sw_part_numbers_for_sheet_api,
    get_completed_features_for_sw_api,
)

# Admin and manager views
from .admin_views import (
    admin_page,
    create_user,
    toggle_tested_status,
    update_project_overview,
    reset_execution_data,
    history,
    instruction_page,
    custom_logout,
    create_new_test_instance,
    get_feature_completion_status_api,
    UserCreateForm,
)

# Make all functions available at package level for backward compatibility
__all__ = [
    # Home
    'home',
    'post_login_redirect',
    'get_project_overview',
    'save_project_overview',
    # Test cases
    'testcase_list',
    'testcase_add',
    'testcase_test_it',
    'view_test_execution',
    'create_testcases',
    'create_new_version',
    'get_next_sl_no_api',
    # Import
    'upload_excel',
    'input_versions',
    'import_excel',
    'import_full_excel',
    # Export
    'export_excel',
    'export_html',
    'export_html_snapshot',
    'get_sheets_api',
    'get_versions_for_sheet_api',
    'get_features_for_selection_api',
    'get_completed_features_for_selection_api',
    'get_sw_part_numbers_for_sheet_api',
    'get_completed_features_for_sw_api',
    # Admin
    'admin_page',
    'create_user',
    'toggle_tested_status',
    'update_project_overview',
    'reset_execution_data',
    'history',
    'instruction_page',
    'custom_logout',
    'create_new_test_instance',
    'get_feature_completion_status_api',
    'UserCreateForm',
]

