"""
Views Package - Aggregates all view functions

This module re-exports all view functions from their respective modules
to maintain backward compatibility with existing imports.

RISK REMOVAL: Split large views.py (3020 lines) into smaller, maintainable modules
organized by functionality. All functions remain unchanged internally.
"""

# Home and dashboard views
from .home import home, post_login_redirect

# Test case management views
from .testcases import (
    testcase_list,
    testcase_add,
    testcase_test_it,
    view_test_execution,
    create_testcases,
    create_new_version,
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
    _create_snapshot_and_reset,
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
    UserCreateForm,
)

# Make all functions available at package level for backward compatibility
__all__ = [
    # Home
    'home',
    'post_login_redirect',
    # Test cases
    'testcase_list',
    'testcase_add',
    'testcase_test_it',
    'view_test_execution',
    'create_testcases',
    'create_new_version',
    # Import
    'upload_excel',
    'input_versions',
    'import_excel',
    'import_full_excel',
    # Export
    'export_excel',
    'export_html',
    'export_html_snapshot',
    '_create_snapshot_and_reset',
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
    'UserCreateForm',
]

