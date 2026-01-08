"""
Custom Admin Views for UI-driven logic

These views provide custom UI functionality while reusing Django admin permissions
and querysets. They handle the navigation flow:
sheet → sw part number → version → executions

CRITICAL RULES:
- Reuse admin permissions (use admin permission checks)
- Reuse admin querysets (use ModelAdmin.get_queryset())
- Read-only for non-managers when version is locked
- Do NOT place business logic in templates
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.urls import reverse

from .models import TestCaseSheet, TestCaseVersion, TestExecution, TestInstance
from .decorators import is_manager
from .services import get_active_instance
from .admin import TestCaseSheetAdmin, TestCaseVersionAdmin, TestExecutionAdmin


@staff_member_required
def sheet_detail_view(request, sheet_id):
    """
    Custom view for sheet detail with executions.
    Reuses admin permissions and querysets.
    """
    sheet = get_object_or_404(TestCaseSheet, id=sheet_id)
    
    # Reuse admin permission check
    admin_instance = TestCaseSheetAdmin(TestCaseSheet, None)
    if not admin_instance.has_view_permission(request, sheet):
        raise PermissionDenied
    
    # Reuse admin queryset
    qs = admin_instance.get_queryset(request)
    sheet = qs.get(id=sheet_id)
    
    # Get executions for this sheet
    executions = TestExecution.objects.filter(
        sheet=sheet,
        version=sheet.version
    ).select_related('test_case', 'user')
    
    # Check if version is locked
    is_locked = sheet.version.is_locked if sheet.version else False
    can_edit = not is_locked and (
        is_manager(request.user) or request.user.is_superuser
    )
    
    context = {
        'sheet': sheet,
        'executions': executions,
        'is_locked': is_locked,
        'can_edit': can_edit,
        'opts': TestCaseSheet._meta,
        'has_view_permission': admin_instance.has_view_permission(request, sheet),
        'has_change_permission': admin_instance.has_change_permission(request, sheet),
    }
    
    return render(request, 'admin/testmanager/testcasesheet/detail.html', context)


@staff_member_required
def version_detail_view(request, version_id):
    """
    Custom view for version detail with sheets and executions.
    Reuses admin permissions and querysets.
    """
    version = get_object_or_404(TestCaseVersion, id=version_id)
    
    # Reuse admin permission check
    admin_instance = TestCaseVersionAdmin(TestCaseVersion, None)
    if not admin_instance.has_view_permission(request, version):
        raise PermissionDenied
    
    # Reuse admin queryset
    qs = admin_instance.get_queryset(request)
    version = qs.get(id=version_id)
    
    # Get sheets for this version
    sheets = TestCaseSheet.objects.filter(version=version)
    
    # Get executions for this version
    executions = TestExecution.objects.filter(
        version=version
    ).select_related('test_case', 'sheet', 'user')
    
    # Check if version is locked
    is_locked = version.is_locked
    can_edit = not is_locked and (
        is_manager(request.user) or request.user.is_superuser
    )
    
    context = {
        'version': version,
        'sheets': sheets,
        'executions': executions,
        'is_locked': is_locked,
        'can_edit': can_edit,
        'opts': TestCaseVersion._meta,
        'has_view_permission': admin_instance.has_view_permission(request, version),
        'has_change_permission': admin_instance.has_change_permission(request, version),
    }
    
    return render(request, 'admin/testmanager/testcaseversion/detail.html', context)


@staff_member_required
def execution_list_view(request, sheet_id=None, version_id=None):
    """
    Custom view for execution list filtered by sheet or version.
    Reuses admin permissions and querysets.
    """
    # Reuse admin queryset
    admin_instance = TestExecutionAdmin(TestExecution, None)
    qs = admin_instance.get_queryset(request)
    
    # Filter by sheet if provided
    if sheet_id:
        sheet = get_object_or_404(TestCaseSheet, id=sheet_id)
        qs = qs.filter(sheet=sheet)
        
        # Check sheet permissions
        sheet_admin = TestCaseSheetAdmin(TestCaseSheet, None)
        if not sheet_admin.has_view_permission(request, sheet):
            raise PermissionDenied
    else:
        sheet = None
    
    # Filter by version if provided
    if version_id:
        version = get_object_or_404(TestCaseVersion, id=version_id)
        qs = qs.filter(version=version)
        
        # Check version permissions
        version_admin = TestCaseVersionAdmin(TestCaseVersion, None)
        if not version_admin.has_view_permission(request, version):
            raise PermissionDenied
    else:
        version = None
    
    executions = qs.select_related('test_case', 'sheet', 'version', 'user')
    
    # Check if any version is locked
    is_locked = False
    if version:
        is_locked = version.is_locked
    elif sheet and sheet.version:
        is_locked = sheet.version.is_locked
    
    can_edit = not is_locked and (
        is_manager(request.user) or request.user.is_superuser
    )
    
    context = {
        'executions': executions,
        'sheet': sheet,
        'version': version,
        'is_locked': is_locked,
        'can_edit': can_edit,
        'opts': TestExecution._meta,
    }
    
    return render(request, 'admin/testmanager/testexecution/list.html', context)

