"""
Decorators for role-based access control

RISK REMOVAL: Manager/Developer role checks were duplicated 10+ times across views.
This decorator eliminates duplication and ensures consistent permission handling.

ROLE DEFINITIONS:
- MANAGER: Can create snapshots, approve test completion, view history, export HTML/Excel
- TESTER: Can execute test cases, save status/reports/comments, create NEW TEST INSTANCE
- DEVELOPER: Can edit project overview, add test cases
- TEST_ENGINEER: Similar to Tester with additional permissions
"""

from functools import wraps
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.contrib import messages
from .constants import ROLE_MANAGER, ROLE_DEVELOPER, ROLE_TESTER, ROLE_TEST_ENGINEER


def _check_user_role(user, required_roles):
    """
    Helper to check if user has one of the required roles.
    
    RISK REMOVAL: Centralizes role checking logic that was duplicated across views.
    
    Args:
        user: Django User instance
        required_roles: Tuple of role strings to check
        
    Returns:
        bool: True if user has required role or is superuser
    """
    if not user.is_authenticated:
        return False
    
    # Superusers have all permissions
    if user.is_superuser:
        return True
    
    # Check user profile role
    try:
        if hasattr(user, 'profile'):
            user_role = user.profile.role
            if user_role and user_role.upper() in [r.upper() for r in required_roles]:
                return True
    except Exception:
        # If profile doesn't exist or role is missing, deny access
        pass
    
    return False


def manager_required(view_func=None, json_response=False):
    """
    Decorator to require Manager role or superuser.
    
    RISK REMOVAL: Replaces duplicated manager check logic in:
    - toggle_tested_status, toggle_export_approval, testcase_test_it,
    - export_excel, export_html, and others.
    
    Usage:
        @manager_required
        def my_view(request):
            ...
            
        @manager_required(json_response=True)
        def api_view(request):
            ...  # Returns JsonResponse on failure
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not _check_user_role(request.user, (ROLE_MANAGER,)):
                if json_response:
                    return JsonResponse(
                        {"ok": False, "error": "Only managers can perform this action."},
                        status=403
                    )
                messages.error(request, "You don't have permission. Manager access required.")
                return redirect('home')
            return func(request, *args, **kwargs)
        return wrapper
    
    if view_func:
        return decorator(view_func)
    return decorator


def developer_or_manager_required(view_func=None, json_response=False):
    """
    Decorator to require Developer or Manager role or superuser.
    
    RISK REMOVAL: Replaces duplicated developer/manager check logic in:
    - update_project_overview, testcase_add, and others.
    
    Usage:
        @developer_or_manager_required
        def my_view(request):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not _check_user_role(request.user, (ROLE_DEVELOPER, ROLE_MANAGER)):
                if json_response:
                    return JsonResponse(
                        {"ok": False, "error": "Developer or Manager access required."},
                        status=403
                    )
                messages.error(request, "You don't have permission. Developer or Manager access required.")
                return redirect('home')
            return func(request, *args, **kwargs)
        return wrapper
    
    if view_func:
        return decorator(view_func)
    return decorator


def is_manager(user):
    """
    Utility function to check if user is a manager.
    
    RISK REMOVAL: Provides reusable function for inline role checks
    where decorators aren't appropriate (e.g., in templates, conditional logic).
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user is manager or superuser
    """
    return _check_user_role(user, (ROLE_MANAGER,))


def is_developer(user):
    """
    Utility function to check if user is a developer.
    
    RISK REMOVAL: Provides reusable function for inline role checks.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user is developer or superuser
    """
    return _check_user_role(user, (ROLE_DEVELOPER,))


def is_manager_or_developer(user):
    """
    Utility function to check if user is manager or developer.
    
    RISK REMOVAL: Provides reusable function for inline role checks.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user is manager, developer, or superuser
    """
    return _check_user_role(user, (ROLE_MANAGER, ROLE_DEVELOPER))


def is_tester(user):
    """
    Utility function to check if user is a tester.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user is tester or superuser
    """
    return _check_user_role(user, (ROLE_TESTER,))


def is_test_engineer(user):
    """
    Utility function to check if user is a test engineer.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user is test engineer or superuser
    """
    return _check_user_role(user, (ROLE_TEST_ENGINEER,))


def can_execute_tests(user):
    """
    Check if user can execute tests (save status/reports/comments).
    
    ROLES ALLOWED: Tester, Test Engineer, Developer, Manager
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can execute tests
    """
    return _check_user_role(user, (ROLE_TESTER, ROLE_TEST_ENGINEER, ROLE_DEVELOPER, ROLE_MANAGER))


def can_create_instance(user):
    """
    Check if user can create a new test instance.
    
    ROLES ALLOWED: Tester, Test Engineer, Developer, Manager
    Testers CAN create new test instances (new testing cycle).
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can create new test instance
    """
    return _check_user_role(user, (ROLE_TESTER, ROLE_TEST_ENGINEER, ROLE_DEVELOPER, ROLE_MANAGER))


def can_view_history(user):
    """
    Check if user can view history/old versions.
    
    ROLES ALLOWED: Manager ONLY
    Testers CANNOT see old versions.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can view history
    """
    return _check_user_role(user, (ROLE_MANAGER,))


def can_export_reports(user):
    """
    Check if user can export HTML/Excel reports.
    
    ROLES ALLOWED: Manager ONLY
    Testers CANNOT export reports.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can export reports
    """
    return _check_user_role(user, (ROLE_MANAGER,))


def can_manage_versions(user):
    """
    Check if user can manage versions directly.
    
    ROLES ALLOWED: Manager ONLY
    Testers CANNOT manage versions directly.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can manage versions
    """
    return _check_user_role(user, (ROLE_MANAGER,))


def can_approve_tests(user):
    """
    Check if user can approve test completion.
    
    ROLES ALLOWED: Manager ONLY
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user can approve tests
    """
    return _check_user_role(user, (ROLE_MANAGER,))


def tester_or_above_required(view_func=None, json_response=False):
    """
    Decorator to require Tester, Test Engineer, Developer, or Manager role.
    
    Usage:
        @tester_or_above_required
        def my_view(request):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not can_execute_tests(request.user):
                if json_response:
                    return JsonResponse(
                        {"ok": False, "error": "You don't have permission to perform this action."},
                        status=403
                    )
                messages.error(request, "You don't have permission to perform this action.")
                return redirect('home')
            return func(request, *args, **kwargs)
        return wrapper
    
    if view_func:
        return decorator(view_func)
    return decorator

