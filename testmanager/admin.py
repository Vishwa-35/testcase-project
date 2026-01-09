"""
═══════════════════════════════════════════════════════════════════════════════
DJANGO ADMIN - STRICT HIERARCHICAL STRUCTURE
═══════════════════════════════════════════════════════════════════════════════

ADMIN HOME SHOWS ONLY:
1. Test Sheets (TestCaseSheet)  ← PRIMARY ENTRY POINT
2. Test Case Versions           ← MANAGER ONLY
3. Project Overviews            ← MANAGER ONLY

HIDDEN FROM ADMIN INDEX:
- TestCase
- TestExecution
- TestExecutionSnapshot
- SheetMeta

═══════════════════════════════════════════════════════════════════════════════
ADMIN NAVIGATION FLOW (MANDATORY):
═══════════════════════════════════════════════════════════════════════════════

Admin Home
 └── Test Sheets
       └── Sheet (TestCaseSheet)
             └── SW Part Number (derived from TestCaseVersion)
                   └── Version (TestCaseVersion)
                         └── Test Executions (TestExecution)

NO OTHER NAVIGATION PATH IS ALLOWED.

═══════════════════════════════════════════════════════════════════════════════
LOCK ENFORCEMENT (CRITICAL):
═══════════════════════════════════════════════════════════════════════════════

WHEN version.is_locked == True:
- Prevent save() on: TestExecution, ProjectOverview, TestCaseVersion
- Allow READ-ONLY access only
- Raise PermissionDenied on any write attempt

═══════════════════════════════════════════════════════════════════════════════
"""

from django.contrib import admin, messages
from django.contrib.admin import AdminSite
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import reverse, path
from django import forms
from django.contrib.admin import SimpleListFilter
from django.db.models import Count, Q
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, get_object_or_404
from django.template.response import TemplateResponse
import io
import openpyxl
from openpyxl.utils import get_column_letter
from .models import (
    TestExecution, TestCase, SheetMeta, TestExecutionSnapshot, 
    SWVersionMapping, ProjectOverview, TestCaseVersion, TestCaseSheet,
    TestInstance, ActivityLog, UserProfile
)
from .decorators import is_manager, is_developer
from .services import get_active_instance
from .admin_site import custom_admin_site


# ═══════════════════════════════════════════════════════════════════════════════
# BASE MODEL ADMIN WITH STANDARD CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class BaseModelAdmin(admin.ModelAdmin):
    """
    Base ModelAdmin class with standard configurations:
    - Bulk actions enabled (delete_selected)
    - Consistent list view settings
    - Role-based action restrictions
    - Modern UI support
    """
    
    # Enable bulk actions by default
    actions = ["delete_selected"]
    actions_on_top = True
    actions_on_bottom = True
    
    # Standard list view settings
    list_per_page = 50
    list_max_show_all = 200
    
    class Media:
        css = {
            'all': ('testmanager/css/admin_custom.css',)
        }
        js = ('testmanager/js/admin_bulk_actions.js',)
    
    def get_actions(self, request):
        """
        Role-based action restrictions.
        Only managers/superusers can delete.
        """
        actions = super().get_actions(request)
        
        # Remove delete_selected for non-managers
        if not (is_manager(request.user) or request.user.is_superuser):
            if 'delete_selected' in actions:
                del actions['delete_selected']
        
        return actions
    
    def delete_selected(self, request, queryset):
        """
        Custom bulk delete with confirmation and logging.
        """
        if not (is_manager(request.user) or request.user.is_superuser):
            self.message_user(request, "Only managers can delete records.", level=messages.ERROR)
            return
        
        count = queryset.count()
        if count == 0:
            self.message_user(request, "No items selected.", level=messages.WARNING)
            return
        
        # Log deletion
        for obj in queryset:
            try:
                ActivityLog.objects.create(
                    user=request.user,
                    action="DELETE",
                    reference=f"{obj.__class__.__name__} #{obj.id}",
                    remarks=f"Bulk deleted via admin",
                    content_type=obj.__class__.__name__,
                )
            except Exception:
                pass  # Don't fail if logging fails
        
        queryset.delete()
        self.message_user(
            request,
            f"Successfully deleted {count} item(s).",
            level=messages.SUCCESS
        )
    
    delete_selected.short_description = "Delete selected items"


class HiddenModelAdmin(BaseModelAdmin):
    """Base class for models that should be hidden from admin index."""
    
    def has_module_permission(self, request):
        """Hide from admin index by returning False for module permission."""
        return False


class TestCaseAdmin(BaseModelAdmin):
    """
    HIDDEN: TestCase is master definition only.
    Not accessible from admin index - only referenced via hierarchy.
    """
    list_display = (
        "id",
        "base_test_case_id",
        "test_case_id",
        "sw_part_number",
        "app_sw_version",
        "feature",
        "status",
    )
    list_display_links = ("base_test_case_id", "test_case_id")
    ordering = ("base_test_case_id", "-id")
    search_fields = (
        "base_test_case_id",
        "test_case_id",
        "sw_part_number",
        "feature",
    )
    readonly_fields = (
        "instance",
        "sheet_name",
        "sl_no",
        "sw_part_number",
        "feature",
        "requirement_id",
        "app_sw_version",
        "requirement_description",
        "base_test_case_id",
        "test_case_id",
        "test_case_summary",
        "pre_conditions",
        "inputs",
        "periodic_time",
        "test_steps",
        "expected_result",
        "status",
        "reports",
        "comments",
        "created_at",
        "updated_at",
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


class SheetMetaAdmin(BaseModelAdmin):
    """HIDDEN: SheetMeta is internal metadata only."""
    list_display = ("sheet_name",)
    search_fields = ("sheet_name",)
    readonly_fields = ("headers",)
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


class TestExecutionSnapshotAdmin(HiddenModelAdmin):
    """HIDDEN: Snapshots are accessed via export links only."""
    list_display = ('snapshot_name', 'sheet_name', 'sw_part_number', 'app_sw_version', 'exported_at')
    ordering = ('sheet_name', '-app_sw_version', '-exported_at')
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


# Register hidden models (they won't appear in admin index due to has_module_permission)
custom_admin_site.register(TestCase, TestCaseAdmin)
custom_admin_site.register(SheetMeta, SheetMetaAdmin)
custom_admin_site.register(TestExecutionSnapshot, TestExecutionSnapshotAdmin)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION INLINE - SHOWN ONLY WITHIN SHEET CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionInline(admin.TabularInline):
    """
    Inline admin for TestExecution within TestCaseSheet context.
    
    CRITICAL RULES:
    - Executions are bound to (sheet + version + test_case)
    - Sheet and version are NEVER inferred
    - Editable ONLY if version.is_locked == False
    - Feature belongs to TestCase, shown as read-only
    """
    model = TestExecution
    extra = 0
    can_delete = False
    fields = (
        "test_case_id_display",
        "feature_display",
        "test_case_summary_display",
        "status",
        "reports",
        "comments",
        "user_display",
        "executed_at",
    )
    readonly_fields = (
        "test_case_id_display",
        "feature_display",
        "test_case_summary_display",
        "user_display",
        "executed_at",
    )
    
    def test_case_id_display(self, obj):
        """Display test case ID"""
        if obj.test_case:
            return obj.test_case.base_test_case_id or obj.test_case.test_case_id
        return "-"
    test_case_id_display.short_description = "Test Case ID"
    
    def feature_display(self, obj):
        """Display feature from TestCase (read-only, feature belongs to TestCase)"""
        if obj.test_case:
            return obj.test_case.feature or "-"
        return "-"
    feature_display.short_description = "Feature"
    
    def test_case_summary_display(self, obj):
        """Display test case summary (truncated)"""
        if obj.test_case:
            summary = obj.test_case.test_case_summary or ""
            return summary[:80] + "..." if len(summary) > 80 else summary
        return "-"
    test_case_summary_display.short_description = "Summary"
    
    def user_display(self, obj):
        """Display user who executed"""
        if obj.user:
            return obj.user.username
        return "-"
    user_display.short_description = "Executed By"
    
    def has_add_permission(self, request, obj=None):
        """Prevent adding executions from admin"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """
        LOCK ENFORCEMENT: Prevent editing if version is locked.
        obj is the parent TestCaseSheet.
        """
        if obj and obj.version:
            if obj.version.is_locked:
                return False
        return super().has_change_permission(request, obj)
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deleting executions"""
        return False
    
    def get_queryset(self, request):
        """Filter executions by sheet and version FK."""
        qs = super().get_queryset(request).select_related(
            'test_case', 'version', 'sheet', 'user'
        )
        return qs


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE SHEET ADMIN - PRIMARY ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseSheetAdmin(BaseModelAdmin):
    """
    ═══════════════════════════════════════════════════════════════════════════
    PRIMARY ADMIN ENTRY POINT
    ═══════════════════════════════════════════════════════════════════════════
    
    NAVIGATION FLOW:
    Test Sheets → Sheet → SW Part Number → Version → Executions
    
    LIST VIEW:
    - Shows sheet_name ONLY (as per requirement)
    - Clicking sheet drills down to SW Part Numbers
    
    VISIBILITY RULES:
    - Manager: See ALL sheets with all versions
    - Non-manager: See ONLY sheets with active versions (is_active=True)
    
    LOCK ENFORCEMENT:
    - If version.is_locked == True: READ-ONLY for ALL users
    """
    
    # LIST VIEW: Show only sheet_name
    list_display = (
        "sheet_name",
        "sw_part_numbers_display",
        "versions_count",
        "executions_summary",
    )
    list_display_links = ("sheet_name",)
    ordering = ("sheet_name",)
    
    list_filter = (
        "sheet_name",
        "version__is_locked",
        "version__is_active",
    )
    
    search_fields = (
        "sheet_name",
        "version__sw_part_number",
        "version__app_sw_version",
    )
    
    # ALL fields read-only - sheet cannot be edited from admin
    readonly_fields = (
        "sheet_name",
        "version",
        "hierarchy_context",
        "created_at",
    )
    
    fieldsets = (
        ("Hierarchy Context", {
            "fields": ("hierarchy_context",),
            "description": "Navigation: Sheet → SW Part Number → Version → Executions"
        }),
        ("Sheet Information (Read-Only)", {
            "fields": ("sheet_name", "version", "created_at"),
        }),
    )
    
    inlines = [TestExecutionInline]
    
    class Media:
        css = {
            'all': ('testmanager/css/admin_custom.css',)
        }
    
    def hierarchy_context(self, obj):
        """Display full hierarchy path with navigation links."""
        if not obj.version:
            return "-"
        
        # Guard against None values - use strings for format_html (not mark_safe)
        locked_badge = '<span style="color: #dc3545; font-weight: bold;"> [LOCKED - READ ONLY]</span>' if obj.version.is_locked else ''
        active_badge = '<span style="color: #28a745;"> [ACTIVE]</span>' if obj.version.is_active else '<span style="color: #6c757d;"> [INACTIVE]</span>'
        
        status_colors = {
            'NOT_STARTED': '#6c757d',
            'IN_PROGRESS': '#ffc107',
            'COMPLETED': '#28a745',
            'APPROVED': '#007bff',
        }
        # Guard against None execution_status
        execution_status = obj.version.execution_status if obj.version else 'N/A'
        status_color = status_colors.get(execution_status, '#000000')
        
        # Get features for this sheet+version combination (from TestCase via executions)
        features = set()
        executions = TestExecution.objects.filter(
            sheet=obj,
            version=obj.version
        ).select_related('test_case')
        for exec_obj in executions:
            if exec_obj.test_case and exec_obj.test_case.feature:
                features.add(exec_obj.test_case.feature)
        features_str = ", ".join(sorted(features)) if features else "N/A"
        
        # Guard against None/empty values
        sheet_name = obj.sheet_name or "-"
        sw_part = obj.version.sw_part_number if obj.version else "-"
        version = obj.version.app_sw_version if obj.version else "-"
        locked = "🔒" if (obj.version and obj.version.is_locked) else ""
        
        return format_html(
            '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #007bff;">'
            '<h3 style="margin: 0 0 10px 0; color: #333;">📋 Current Context</h3>'
            '<table style="width: 100%; border-collapse: collapse;">'
            '<tr><td style="padding: 5px 10px; font-weight: bold; width: 150px;">Sheet:</td><td style="padding: 5px 10px;">{}</td></tr>'
            '<tr><td style="padding: 5px 10px; font-weight: bold;">SW Part Number:</td><td style="padding: 5px 10px;">{}</td></tr>'
            '<tr><td style="padding: 5px 10px; font-weight: bold;">Version:</td><td style="padding: 5px 10px;">{}{}</td></tr>'
            '<tr><td style="padding: 5px 10px; font-weight: bold;">Features:</td><td style="padding: 5px 10px;">{}</td></tr>'
            '<tr><td style="padding: 5px 10px; font-weight: bold;">Status:</td><td style="padding: 5px 10px;"><span style="color: {}; font-weight: bold;">{}</span>{}</td></tr>'
            '</table>'
            '</div>',
            sheet_name,
            sw_part,
            version,
            locked,
            features_str,
            status_color,
            execution_status,
            locked_badge
        )
    hierarchy_context.short_description = "Hierarchy Context"
    
    def sw_part_numbers_display(self, obj):
        """Display SW Part Number for this sheet."""
        if obj.version:
            return obj.version.sw_part_number
        return "-"
    sw_part_numbers_display.short_description = "SW Part Number"
    sw_part_numbers_display.admin_order_field = "version__sw_part_number"
    
    def versions_count(self, obj):
        """Display version info."""
        if obj.version:
            # Guard against None/empty values
            version = obj.version.app_sw_version or "-"
            locked = "🔒" if obj.version.is_locked else ""
            active = "✓" if obj.version.is_active else ""
            return format_html(
                '{} {} {}',
                version,
                locked,
                active
            )
        return "-"
    versions_count.short_description = "Version"
    versions_count.admin_order_field = "version__app_sw_version"
    
    def executions_summary(self, obj):
        """
        Display execution statistics.
        CRITICAL: Query by version FK and sheet FK, NOT by CharField.
        """
        if not obj.version:
            return "-"
        
        # CORRECT: Query using FK relationships
        executions = TestExecution.objects.filter(
            sheet=obj,
            version=obj.version
        )
        
        total = executions.count()
        if total == 0:
            return mark_safe('<span style="color: #6c757d;">No executions</span>')
        
        passed = executions.filter(status__iexact='PASS').count()
        failed = executions.filter(status__iexact='FAIL').count()
        pending = total - passed - failed
        
        # format_html is safe here - all values are guaranteed integers (count())
        return format_html(
            '<span style="color: #28a745;">✓{}</span> '
            '<span style="color: #dc3545;">✗{}</span> '
            '<span style="color: #6c757d;">⏳{}</span>',
            passed, failed, pending
        )
    executions_summary.short_description = "Pass/Fail/Pending"
    
    def get_queryset(self, request):
        """
        VISIBILITY RULES:
        - Manager: See ALL sheets with all versions (active + old)
        - Non-manager: See ONLY sheets with active versions (is_active=True)
        
        CRITICAL: Filter using version FK, NOT CharField.
        """
        qs = super().get_queryset(request).select_related('version', 'version__instance')
        
        # Filter by active instance
        active_instance = get_active_instance()
        if active_instance:
            qs = qs.filter(version__instance=active_instance)
        
        # VISIBILITY: Non-managers see only active versions
        if not (is_manager(request.user) or request.user.is_superuser):
            qs = qs.filter(version__is_active=True)
        
        return qs
    
    def has_add_permission(self, request):
        """Sheet cannot be added from admin - use import flow."""
        return False
    
    def has_change_permission(self, request, obj=None):
        """
        LOCK ENFORCEMENT:
        - If version.is_locked == True: READ-ONLY for ALL users
        - Sheet itself is always read-only (only executions can be edited)
        """
        if obj and obj.version and obj.version.is_locked:
            return False
        return super().has_change_permission(request, obj)
    
    def has_delete_permission(self, request, obj=None):
        """Sheet cannot be deleted from admin."""
        return False
    
    def save_model(self, request, obj, form, change):
        """
        LOCK ENFORCEMENT: Prevent save if version is locked.
        """
        if obj.version and obj.version.is_locked:
            raise PermissionDenied("Cannot modify: Version is locked.")
        super().save_model(request, obj, form, change)
    
    def save_formset(self, request, form, formset):
        """
        CRITICAL SAVE RULES FOR EXECUTIONS:
        
        1. Execution MUST be bound to: (sheet + version + test_case) via FK
        2. LOCK ENFORCEMENT: Prevent save if version is locked
        3. Sync legacy fields from version FK (NOT from CharField)
        """
        sheet_obj = form.instance
        
        # LOCK ENFORCEMENT
        if sheet_obj.version and sheet_obj.version.is_locked:
            raise PermissionDenied("Cannot modify executions: Version is locked.")
        
        instances = formset.save(commit=False)
        
        for instance in instances:
            # CRITICAL: Bind to sheet and version via FK
            instance.sheet = sheet_obj
            instance.version = sheet_obj.version
            
            if not instance.test_case:
                continue
            
            # Sync legacy fields FROM version FK (NOT from CharField)
            if instance.version:
                instance.sw_part_number = instance.version.sw_part_number
                instance.app_sw_version = instance.version.app_sw_version
                instance.instance = instance.version.instance
            
            if not instance.user:
                instance.user = request.user
            
            instance.save()
        
        formset.save_m2m()
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        LOCK ENFORCEMENT & VERSION CONTEXT: Show read-only message and version context.
        """
        if extra_context is None:
            extra_context = {}
        obj = self.get_object(request, object_id)
        
        
        return super().change_view(request, object_id, form_url, extra_context)


# ════════════════════════════════════════════════════════════════════════════
# TEST EXECUTION ADMIN - HIDDEN FROM INDEX, ACCESSIBLE ONLY VIA HIERARCHY
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionAdmin(BaseModelAdmin):
    """
    HIDDEN: TestExecution is NOT accessible from admin index.
    Executions are accessed ONLY via Sheet → Version hierarchy.
    
    This admin exists only for internal use and direct URL access
    (which should be blocked in production).
    """
    list_display = (
        "id",
        "sheet_display",
        "version_display",
        "test_case_display",
        "status",
        "user",
        "executed_at",
    )
    list_display_links = ("id", "test_case_display")
    ordering = ("sheet__sheet_name", "version__sw_part_number", "-version__app_sw_version")
    
    list_filter = (
        "status",
        "version__is_locked",
        "version__is_active",
        "executed_at",
    )
    
    search_fields = (
        "test_case__test_case_id",
        "test_case__base_test_case_id",
        "version__sw_part_number",
        "version__app_sw_version",
        "user__username",
    )
    
    readonly_fields = (
        "sheet",
        "version",
        "test_case",
        "user",
        "executed_at",
        "is_locked",
    )
    
    def sheet_display(self, obj):
        return obj.sheet.sheet_name if obj.sheet else "-"
    sheet_display.short_description = "Sheet"
    
    def version_display(self, obj):
        if obj.version:
            return f"{obj.version.sw_part_number} / {obj.version.app_sw_version}"
        return "-"
    version_display.short_description = "Version"
    
    def test_case_display(self, obj):
        if obj.test_case:
            return obj.test_case.base_test_case_id or obj.test_case.test_case_id
        return "-"
    test_case_display.short_description = "Test Case"
    
    def get_queryset(self, request):
        """
        CRITICAL: Filter using FK relationships, NOT CharField.
        """
        qs = super().get_queryset(request).select_related(
            'sheet', 'version', 'test_case', 'user'
        )
        
        active_instance = get_active_instance()
        if active_instance:
            # Filter by version FK's instance, NOT by instance CharField
            qs = qs.filter(version__instance=active_instance)
        
        # Non-managers see only active versions
        if not (is_manager(request.user) or request.user.is_superuser):
            qs = qs.filter(version__is_active=True)
        
        return qs
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        """LOCK ENFORCEMENT"""
        if obj and obj.version and obj.version.is_locked:
            return False
        return super().has_change_permission(request, obj)
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def save_model(self, request, obj, form, change):
        """LOCK ENFORCEMENT"""
        if obj.version and obj.version.is_locked:
            raise PermissionDenied("Cannot modify: Version is locked.")
        
        # Sync legacy fields from version FK
        if obj.version:
            obj.sw_part_number = obj.version.sw_part_number
            obj.app_sw_version = obj.version.app_sw_version
            obj.instance = obj.version.instance
        
        super().save_model(request, obj, form, change)


custom_admin_site.register(TestExecution, TestExecutionAdmin)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE VERSION ADMIN - MANAGER ONLY
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseVersionAdmin(HiddenModelAdmin):
    """
    ═══════════════════════════════════════════════════════════════════════════
    MANAGER ONLY: Test Case Version Admin
    ═══════════════════════════════════════════════════════════════════════════
    
    REQUIRED BEHAVIOR:
    - Show ALL versions (past + present) grouped by SW Part Number
    - DO NOT delete or hide old versions
    - Clear version history per SW Part Number
    
    VISIBILITY:
    - Manager: Can see ALL versions (active + old)
    - Non-manager: Can see ONLY is_active=True version
    
    LOCK ENFORCEMENT:
    - If is_locked=True: Version is READ-ONLY for ALL users (including manager)
    
    VERSION GROUPING:
    - Versions are organized under each SW Part Number
    - Order: sw_part_number, then -created_at (newest first per SW)
    """
    
    list_display = (
        "sw_part_number",
        "version_display",
        "is_active_display",
        "is_locked_display",
        "execution_status_display",
        "instance_display",
        "created_at",
    )
    list_display_links = ("sw_part_number", "version_display")
    ordering = ("sw_part_number", "-created_at")
    
    list_filter = (
        "sw_part_number",
        "is_active",
        "is_locked",
        "execution_status",
        "instance",
    )
    
    search_fields = (
        "sw_part_number",
        "app_sw_version",
    )
    
    # execution_status is READ-ONLY (calculated from TestExecution records)
    readonly_fields = (
        "execution_status",
        "instance",
        "created_at",
        "updated_at",
    )
    
    fieldsets = (
        ("Version Identification (Critical)", {
            "fields": (
                "sw_part_number",
                "app_sw_version",
                "instance",
            ),
            "description": "⚠️ CRITICAL: Verify SW Part Number and Version before editing. Editing wrong version can cause data corruption.",
            "classes": ("wide",),
        }),
        ("Status & Lifecycle", {
            "fields": (
                "execution_status",
                "is_active",
                "is_locked",
            ),
            "description": "execution_status is automatically calculated. LOCKED versions are immutable.",
        }),
        ("Audit Information", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
            "description": "Metadata fields - collapsed by default to reduce clutter.",
        }),
    )
    
    actions = ["lock_version", "approve_version"]
    
    def has_module_permission(self, request):
        """MANAGER ONLY: Hide from admin index for non-managers."""
        return is_manager(request.user) or request.user.is_superuser
    
    @admin.display(description="Version", ordering="app_sw_version")
    def version_display(self, obj):
        """Display version number with lock indicator."""
        # Guard against None/empty app_sw_version
        version = obj.app_sw_version or "-"
        locked = " 🔒" if obj.is_locked else ""
        # Use format_html only when we have guaranteed non-None values
        return format_html(
            '<strong>{}</strong>{}',
            version,
            locked
        )
    
    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj):
        """Display active status with clear visual indicator."""
        # Use mark_safe for static HTML (no variable interpolation)
        if obj.is_active:
            return mark_safe(
                '<span style="color: #28a745; font-weight: bold;">✓ Active</span>'
            )
        return mark_safe(
            '<span style="color: #6c757d;">Inactive</span>'
        )
    
    @admin.display(description="Locked", boolean=True)
    def is_locked_display(self, obj):
        """Display lock status with clear visual indicator."""
        if obj.is_locked:
            return mark_safe(
                '<span style="color: #dc3545; font-weight: bold;">🔒 Locked</span>'
            )
        return mark_safe(
            '<span style="color: #6c757d;">Unlocked</span>'
        )
    
    @admin.display(description="Status", ordering="execution_status")
    def execution_status_display(self, obj):
        """Display execution status with color coding."""
        status = obj.execution_status or "NOT_STARTED"
        color_map = {
            "NOT_STARTED": "#6c757d",
            "IN_PROGRESS": "#ffc107",
            "COMPLETED": "#28a745",
            "APPROVED": "#007bff",
        }
        color = color_map.get(status, "#6c757d")
        return mark_safe(
            f'<span style="color: {color}; font-weight: bold;">{status}</span>'
        )
    
    @admin.display(description="Instance", ordering="instance__id")
    def instance_display(self, obj):
        """Display instance/cycle reference."""
        if obj.instance:
            status = "ACTIVE" if obj.instance.is_active else "ARCHIVED"
            return f"Instance {obj.instance.id} ({status})"
        return "-"
    
    def get_queryset(self, request):
        """
        VISIBILITY RULES:
        - Manager: See ALL versions (active + old) for active instance - NO filtering by is_active
        - Non-manager: See ONLY is_active=True versions for active instance
        
        CRITICAL: Do NOT filter out old versions for managers.
        All versions must remain visible to preserve version history.
        
        VERSION GROUPING:
        - Versions are grouped by SW Part Number via ordering: (sw_part_number, -created_at)
        - All versions (past + present) for the active instance are shown
        - Historical versions remain visible and are never deleted
        """
        qs = super().get_queryset(request).select_related('instance')
        
        # Show versions for active instance only (preserves version history per instance)
        active_instance = get_active_instance()
        if active_instance:
            qs = qs.filter(instance=active_instance)
        
        # Non-managers see only active versions
        # Managers see ALL versions (active + old) - preserve version history
        if not (is_manager(request.user) or request.user.is_superuser):
            qs = qs.filter(is_active=True)
        
        return qs
    
    def get_readonly_fields(self, request, obj=None):
        """
        LOCK ENFORCEMENT & VERSION RESTRICTIONS:
        - If is_locked=True: ALL fields become read-only
        - If not latest version: ALL fields become read-only (historical versions)
        - Developers cannot edit historical versions (only Managers can)
        """
        readonly = list(self.readonly_fields)
        
        if obj:
            # Check if this is the latest version for this SW Part Number
            is_latest = self._is_latest_version(obj)
            
            # Lock enforcement
            if obj.is_locked:
                readonly += ["sw_part_number", "app_sw_version", "is_active", "is_locked", "instance"]
            # Historical version restriction
            elif not is_latest:
                # Only Managers can edit historical versions
                if not (is_manager(request.user) or request.user.is_superuser):
                    readonly += ["sw_part_number", "app_sw_version", "is_active", "is_locked", "instance"]
                # Even Managers see historical versions as read-only by default (safety)
                readonly += ["sw_part_number", "app_sw_version", "instance"]
        
        return readonly
    
    def _is_latest_version(self, obj):
        """Check if this is the latest version for the SW Part Number."""
        if not obj.instance or not obj.sw_part_number:
            return False
        
        # Get the most recent version for this SW Part Number and instance
        latest = TestCaseVersion.objects.filter(
            instance=obj.instance,
            sw_part_number=obj.sw_part_number
        ).order_by('-created_at').first()
        
        return latest and latest.id == obj.id
    
    def has_add_permission(self, request):
        """Only manager can add versions."""
        return is_manager(request.user) or request.user.is_superuser
    
    def has_change_permission(self, request, obj=None):
        """
        LOCK ENFORCEMENT:
        - If is_locked=True: Version is READ-ONLY for ALL users
        """
        if obj and obj.is_locked:
            return False
        return is_manager(request.user) or request.user.is_superuser
    
    def has_delete_permission(self, request, obj=None):
        """
        STRICT PROHIBITION: Do NOT delete old versions.
        Historical execution & snapshot logic depends on them.
        Only superuser can delete, and only if not locked.
        """
        # Prevent deleting locked versions
        if obj and obj.is_locked:
            return False
        # Prevent deleting old versions - preserve version history
        # Only allow deletion in extreme cases (superuser only)
        return False  # Changed: Prevent all deletions to preserve history
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Add version context and read-only enforcement for historical versions.
        """
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        

        return super().change_view(request, object_id, form_url, extra_context)
    
    def save_model(self, request, obj, form, change):
        """LOCK ENFORCEMENT"""
        if change and obj.is_locked:
            # Check if we're trying to modify a locked version
            old_obj = TestCaseVersion.objects.get(pk=obj.pk)
            if old_obj.is_locked:
                raise PermissionDenied("Cannot modify: Version is locked.")
        super().save_model(request, obj, form, change)
    
    @admin.action(description="🔒 Lock selected versions (make read-only)")
    def lock_version(self, request, queryset):
        """Lock versions to make them read-only."""
        if not (is_manager(request.user) or request.user.is_superuser):
            self.message_user(request, "Only managers can lock versions.", level=messages.ERROR)
            return
        
        locked_count = 0
        for version in queryset:
            if not version.is_locked:
                version.is_locked = True
                version.save(update_fields=['is_locked'])
                locked_count += 1
        
        if locked_count > 0:
            self.message_user(
                request,
                f"Successfully locked {locked_count} version(s). They are now read-only.",
                level=messages.SUCCESS
            )
    
    @admin.action(description="✓ Approve selected versions (COMPLETED → APPROVED)")
    def approve_version(self, request, queryset):
        """Approve COMPLETED versions (sets APPROVED and locks)."""
        if not (is_manager(request.user) or request.user.is_superuser):
            self.message_user(request, "Only managers can approve versions.", level=messages.ERROR)
            return
        
        approved_count = 0
        for version in queryset:
            if version.execution_status == 'APPROVED':
                continue
            
            if version.execution_status != 'COMPLETED':
                self.message_user(
                    request,
                    f"Version {version.app_sw_version} cannot be approved. Status must be COMPLETED.",
                    level=messages.WARNING
                )
                continue
            
            version.execution_status = 'APPROVED'
            version.is_locked = True
            version.save()
            approved_count += 1
        
        if approved_count > 0:
            self.message_user(
                request,
                f"Successfully approved {approved_count} version(s).",
                level=messages.SUCCESS
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT OVERVIEW ADMIN - MANAGER ONLY
# ═══════════════════════════════════════════════════════════════════════════════

class ProjectOverviewAdmin(BaseModelAdmin):
    """
    ═══════════════════════════════════════════════════════════════════════════
    MANAGER ONLY: Project Overview Admin
    ═══════════════════════════════════════════════════════════════════════════
    
    RULES:
    - Must be linked ONLY via version FK
    - Editable ONLY by manager or developer
    - Editable ONLY if version.is_locked == False
    - Admin list MUST NOT show legacy fields
    - Selection MUST be driven by version FK
    """
    
    # NO legacy fields in list display
    list_display = (
        "version_display",
        "project_code",
        "project_stage",
        "developer",
        "test_engineer",
        "is_locked_display",
        "updated_at",
    )
    list_display_links = ("version_display",)
    ordering = ("-version__app_sw_version", "version__sw_part_number")
    
    # Filter by version FK, NOT by CharField
    list_filter = (
        "version__sw_part_number",
        "version__app_sw_version",
        "version__is_locked",
        "project_stage",
    )
    
    search_fields = (
        "version__sw_part_number",
        "version__app_sw_version",
        "project_code",
        "developer",
        "test_engineer",
    )
    
    # Version FK is read-only (selection driven by hierarchy)
    readonly_fields = (
        "version",
        "instance",
        "is_locked_display",
        "created_at",
        "updated_at",
        "created_by",
        # Legacy fields are hidden but kept read-only for safety
        "software_part_number",
        "application_sw_version",
        "sw_part_number",
        "app_sw_version",
    )
    
    # NO legacy fields in fieldsets
    fieldsets = (
        ("Version Context (Read-Only)", {
            "fields": (
                "version",
                "is_locked_display",
            ),
            "description": "⚠️ CRITICAL: Project Overview is linked to a specific version. Verify version before editing.",
            "classes": ("wide",),
        }),
        ("Project Overview - Basic Information", {
            "fields": (
                "project_code",
                "project_stage",
                "developer",
                "test_engineer",
            )
        }),
        ("Project Overview - Technical Details", {
            "fields": (
                "vcu_platform",
                "hardware_part_number",
                "bootloader_sw_version",
                "checksum_value",
                "dbc_test_it",
            )
        }),
        ("Audit Information", {
            "fields": ("created_by", "created_at", "updated_at"),
            "classes": ("collapse",),
            "description": "Metadata fields - collapsed by default to reduce clutter.",
        }),
    )
    
    def has_module_permission(self, request):
        """MANAGER ONLY: Hide from admin index for non-managers."""
        return is_manager(request.user) or is_developer(request.user) or request.user.is_superuser
    
    def version_display(self, obj):
        """Display version info from FK."""
        if obj.version:
            locked = "🔒" if obj.version.is_locked else ""
            return format_html(
                '{} / {} {}',
                obj.version.sw_part_number,
                obj.version.app_sw_version,
                locked
            )
        return "-"
    version_display.short_description = "Version"
    version_display.admin_order_field = "version__app_sw_version"
    
    @admin.display(boolean=True, description="Locked")
    def is_locked_display(self, obj):
        """Display lock status from version FK."""
        if obj.version:
            return obj.version.is_locked
        return False
    
    def get_queryset(self, request):
        """Filter by version FK."""
        qs = super().get_queryset(request).select_related('version', 'version__instance')
        
        active_instance = get_active_instance()
        if active_instance:
            # Filter by version FK's instance
            qs = qs.filter(version__instance=active_instance)
        
        return qs
    
    def get_readonly_fields(self, request, obj=None):
        """
        LOCK ENFORCEMENT & ROLE-BASED RESTRICTIONS:
        - If version.is_locked == True: ALL fields become read-only
        - Developers can only edit active/latest versions
        - Managers can edit all versions (except locked)
        """
        readonly = list(self.readonly_fields)
        
        if obj and obj.version:
            # Lock enforcement
            if obj.version.is_locked:
                readonly += [
                    "project_code", "vcu_platform", "hardware_part_number",
                    "project_stage", "developer", "test_engineer",
                    "bootloader_sw_version", "checksum_value", "dbc_test_it"
                ]
            # Role-based restrictions
            elif is_developer(request.user) and not request.user.is_superuser:
                # Developers can only edit active versions
                if not obj.version.is_active:
                    readonly += [
                        "project_code", "vcu_platform", "hardware_part_number",
                        "project_stage", "developer", "test_engineer",
                        "bootloader_sw_version", "checksum_value", "dbc_test_it"
                    ]
        
        return readonly
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Add version context and role-based UI restrictions.
        """
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        
        return super().change_view(request, object_id, form_url, extra_context)
    
    def has_add_permission(self, request):
        """Only manager or developer can add."""
        return is_manager(request.user) or is_developer(request.user) or request.user.is_superuser
    
    def has_change_permission(self, request, obj=None):
        """
        LOCK ENFORCEMENT:
        - If version.is_locked == True: READ-ONLY for ALL users
        """
        if obj and obj.version and obj.version.is_locked:
            return False
        return is_manager(request.user) or is_developer(request.user) or request.user.is_superuser
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deleting if version is locked."""
        if obj and obj.version and obj.version.is_locked:
            return False
        return request.user.is_superuser
    
    def save_model(self, request, obj, form, change):
        """LOCK ENFORCEMENT"""
        if obj.version and obj.version.is_locked:
            raise PermissionDenied("Cannot modify: Version is locked.")
        
        if not obj.created_by:
            obj.created_by = request.user
        
        super().save_model(request, obj, form, change)
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Filter version choices to active instance only."""
        if db_field.name == "version":
            active_instance = get_active_instance()
            if active_instance:
                kwargs["queryset"] = TestCaseVersion.objects.filter(
                    instance=active_instance
                ).order_by('-app_sw_version', 'sw_part_number')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTER ALL MODELS WITH CUSTOM ADMIN SITE
# ═══════════════════════════════════════════════════════════════════════════════

# Register visible models (appear in admin index)
custom_admin_site.register(TestCaseSheet, TestCaseSheetAdmin)
custom_admin_site.register(TestCaseVersion, TestCaseVersionAdmin)
custom_admin_site.register(ProjectOverview, ProjectOverviewAdmin)

# Register additional models (if needed for admin access)
# TestInstance - Manager only
class TestInstanceAdmin(BaseModelAdmin):
    list_display = ('id', 'is_active', 'created_at', 'instance_info')
    list_display_links = ('id',)
    list_filter = ('is_active', 'created_at')
    search_fields = ('id',)
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    
    def has_module_permission(self, request):
        """Manager only"""
        return is_manager(request.user) or request.user.is_superuser
    
    def instance_info(self, obj):
        """Display instance information."""
        if obj.is_active:
            return format_html('<span style="color: #10B981; font-weight: bold;">✓ ACTIVE</span>')
        return format_html('<span style="color: #6B7280;">ARCHIVED</span>')
    instance_info.short_description = "Status"

custom_admin_site.register(TestInstance, TestInstanceAdmin)

# SWVersionMapping - Hidden (internal use)
class SWVersionMappingAdmin(HiddenModelAdmin):
    list_display = ('id', 'sw_part_number', 'version', 'is_active', 'instance')
    list_display_links = ('id', 'sw_part_number')
    list_filter = ('is_active', 'sw_part_number', 'instance')
    search_fields = ('sw_part_number', 'version')
    ordering = ('sw_part_number', '-created_at')
    readonly_fields = ('created_at', 'updated_at')

custom_admin_site.register(SWVersionMapping, SWVersionMappingAdmin)

# ActivityLog - Manager only
class ActivityLogAdmin(BaseModelAdmin):
    list_display = ('user', 'action', 'reference', 'content_type', 'timestamp')
    list_filter = ('action', 'content_type', 'timestamp')
    readonly_fields = ('user', 'action', 'reference', 'remarks', 'content_type', 'diff', 'timestamp')
    search_fields = ('user__username', 'reference', 'remarks')
    ordering = ('-timestamp',)
    
    def has_module_permission(self, request):
        """Manager only"""
        return is_manager(request.user) or request.user.is_superuser
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

custom_admin_site.register(ActivityLog, ActivityLogAdmin)

# UserProfile - Manager only
class UserProfileAdmin(BaseModelAdmin):
    list_display = ('full_name', 'employee_id', 'role', 'user', 'created_at')
    list_filter = ('role', 'created_at')
    search_fields = ('full_name', 'employee_id', 'user__username')
    readonly_fields = ('created_at', 'updated_at')
    
    def has_module_permission(self, request):
        """Manager only"""
        return is_manager(request.user) or request.user.is_superuser

custom_admin_site.register(UserProfile, UserProfileAdmin)
