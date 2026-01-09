"""
Admin and Manager Views

This module contains views for admin-only and manager-only functionality,
including user management, project overview updates, and system controls.
"""

import json
import json as json_module
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth.models import User, Group
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.forms import ModelForm, CharField, PasswordInput

from ..models import (
    ActivityLog, ProjectOverview, UserProfile, TestExecution, SWVersionMapping,
    TestCase, TestInstance, TestExecutionSnapshot, TestCaseVersion, TestCaseSheet
)
from ..decorators import manager_required, developer_or_manager_required, can_create_instance, is_manager, tester_or_above_required


class UserCreateForm(ModelForm):
    password = CharField(widget=PasswordInput, required=True, label="Password")
    confirm_password = CharField(widget=PasswordInput, required=True, label="Confirm Password")
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password']
        labels = {
            'username': 'Username',
            'email': 'Email',
        }
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        
        if password and confirm_password and password != confirm_password:
            raise ValidationError("Passwords do not match.")
        
        return cleaned_data


@login_required
@require_POST
def custom_logout(request):
    logout(request)
    # Always go back to login page
    return redirect(settings.LOGIN_URL)


@require_POST
@developer_or_manager_required(json_response=True)
def update_project_overview(request):
    """
    Update project overview key-value pairs.
    
    CLEAN IMPLEMENTATION: Saves all fields in ONE atomic transaction.
    This prevents "database is locked" errors by ensuring only ONE write operation.
    """
    try:
        data = json.loads(request.body)
        
        # Field mapping: form field names -> ProjectOverview keys
        field_mapping = {
            'project_code': 'project_code',
            'vcu_platform': 'vcu_platform',
            'hw_part_number': 'hw_part_number',
            'sw_part_number': 'sw_part_number',
            'project_stage': 'project_stage',
            'developer': 'developer',
            'test_engineer': 'test_engineer',
            'app_sw_version': 'app_sw_version',
            'bootloader_sw_version': 'bootloader_sw_version',
            'checksum_value': 'checksum_value',
            'dbc_test_it': 'dbc_test_it',
        }
        
        # Use atomic transaction to ensure all updates happen in ONE database operation
        # This prevents "database is locked" errors by ensuring only ONE write operation
        with transaction.atomic():  # type: ignore
            # Update all fields in a single transaction
            for form_field, db_key in field_mapping.items():
                value = data.get(form_field, '').strip()
                ProjectOverview.objects.update_or_create(
                    key=db_key,
                    defaults={"value": value}
                )
        
        return JsonResponse({"ok": True})
        
    except json.JSONDecodeError:
        return JsonResponse({
            "ok": False,
            "error": "Invalid request data."
        }, status=400)
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": f"Error updating Project Overview: {str(e)}"
        }, status=500)


@login_required
@require_POST
@manager_required(json_response=True)
def toggle_tested_status(request):
    """
    Manager checkbox = Version Finalization (PERMANENT LOCK)
    
    When manager checks this:
    - Set TestCaseVersion.is_active = False
    - Set TestCaseVersion.is_locked = True
    - Set is_locked = True for ALL related TestExecution rows
    
    Locked versions:
    - MUST be read-only for EVERYONE (including manager)
    - MUST NOT allow edit of status, report, or comments
    - MUST remain permanently stored for that version
    """
    
    try:
        import json
        from django.utils import timezone
        from django.shortcuts import get_object_or_404
        from django.db import transaction
        from ..models import TestCase, TestExecution, TestCaseVersion, TestCaseSheet
        
        data = json.loads(request.body)
        test_case_id = data.get("test_case_id")
        tested = data.get("tested", False)
        
        if not test_case_id:
            return JsonResponse({"ok": False, "error": "Test case ID is required."}, status=400)
        
        test_case = get_object_or_404(TestCase, id=test_case_id)
        
        # CRITICAL: Get active instance - executions must belong to active instance
        from ..services import get_active_instance
        active_instance = get_active_instance()
        
        # Verify test case belongs to active instance
        if test_case.instance != active_instance:
            return JsonResponse({
                "ok": False,
                "error": "Test case does not belong to active instance."
            }, status=403)
        
        sw_part_number = test_case.sw_part_number or ""
        app_sw_version = test_case.app_sw_version or ""
        
        if not sw_part_number or not app_sw_version:
            return JsonResponse({
                "ok": False,
                "error": "SW Part Number and Version are required."
            }, status=400)
        
        # Get or create TestCaseVersion
        version, _ = TestCaseVersion.objects.get_or_create(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version,
            defaults={"is_active": True, "is_locked": False}
        )
        
        # Get or create TestCaseSheet
        sheet_name = test_case.sheet_name or ""
        if sheet_name:
            sheet, _ = TestCaseSheet.objects.get_or_create(
                version=version,
                sheet_name=sheet_name
            )
        else:
            sheet = None
        
        # Get or create execution for this test case
        execution = TestExecution.objects.filter(
            test_case=test_case,
            instance=active_instance,
            version=version,
            sheet=sheet
        ).first()
        
        with transaction.atomic():  # type: ignore
            if not execution:
                # Create execution if it doesn't exist
                execution = TestExecution.objects.create(
                    test_case=test_case,
                    instance=active_instance,
                    version=version,
                    sheet=sheet,
                    user=request.user,
                    sw_part_number=sw_part_number,
                    app_sw_version=app_sw_version,
                    status="",
                    reports="",
                    comments="",
                    manager_approved=tested,
                    approved_by=request.user if tested else None,
                    approved_at=timezone.now() if tested else None,
                    is_locked=False
                )
            else:
                # Update approval status
                execution.manager_approved = tested
                execution.approved_by = request.user if tested else None
                execution.approved_at = timezone.now() if tested else None
                execution.save()
            
            # CRITICAL: If manager checks the box, lock the version permanently
            if tested:
                # Set version to inactive and locked
                version.is_active = False
                version.is_locked = True
                version.save()
                
                # Lock ALL executions for this version
                TestExecution.objects.filter(
                    version=version,
                    instance=active_instance
                ).update(is_locked=True)
        
        return JsonResponse({"ok": True, "tested": tested})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@login_required
@developer_or_manager_required
def history(request):
    """
    Display all activity logs with pagination.
    
    MANAGER ONLY: Only managers can view history.
    Testers CANNOT access this page.
    """
    page_number = request.GET.get("page")
    activity_logs = ActivityLog.objects.order_by("-timestamp")
    
    paginator = Paginator(activity_logs, 50)
    page_obj = paginator.get_page(page_number)
    
    return render(request, "testmanager/history.html", {
        "activity_logs": page_obj,
        "page_obj": page_obj,
    })


@login_required
def admin_page(request):
    """Admin page for user management - superuser only"""
    if not request.user.is_superuser:
        messages.error(request, "Access denied. Superuser privileges required.")
        return redirect('home')
    
    users = User.objects.all().select_related('profile').order_by('-date_joined')
    
    context = {
        'users': users,
    }
    return render(request, 'testmanager/admin_page.html', context)


def create_user(request):
    """Create new user - anyone can create, assign to group based on role"""
    if request.method == 'POST':
        try:
            username = request.POST.get('username')
            email = request.POST.get('email')
            password = request.POST.get('password')
            full_name = request.POST.get('full_name')
            employee_id = request.POST.get('employee_id')
            role = request.POST.get('role')
            
            # Validate required fields
            if not username or not password or not employee_id or not full_name:
                messages.error(request, "Please fill in all required fields.")
                return redirect('create_user')
            
            # Check if username or employee_id already exists
            if User.objects.filter(username=username).exists():
                messages.error(request, f"Username '{username}' already exists.")
                return redirect('create_user')
            
            if UserProfile.objects.filter(employee_id=employee_id).exists():
                messages.error(request, f"Employee ID '{employee_id}' already exists.")
                return redirect('create_user')
            
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=full_name.split()[0] if full_name else '',
                last_name=' '.join(full_name.split()[1:]) if len(full_name.split()) > 1 else ''
            )
            
            # Create profile
            profile = UserProfile.objects.create(
                user=user,
                employee_id=employee_id,
                full_name=full_name,
                role=role
            )
            
            # Assign user to group based on role
            if role:
                # Get or create group based on role
                group_name = role.title()  # e.g., "Tester","Test Engineer", "Developer", "Manager", "Admin", "QA"
                group, created = Group.objects.get_or_create(name=group_name)
                user.groups.add(group)
                
                # Manager role gets full superuser access
                if role.upper() == 'MANAGER':
                    user.is_superuser = True
                    user.is_staff = True
                    user.save()
                # If role is Admin, also add to staff (but not superuser)
                elif role.upper() == 'ADMIN':
                    user.is_staff = True
                    user.save()
            
            messages.success(request, f"User '{full_name}' created successfully! Please login with your credentials.")
            
            # Always redirect to login page after user creation
            return redirect('admin:login')
        except Exception as e:
            messages.error(request, f"Error creating user: {str(e)}")
            return redirect('create_user')
    
    # GET request - show form
    context = {
        'roles': UserProfile.ROLE_CHOICES,
    }
    return render(request, 'testmanager/create_user.html', context)


def reset_execution_data(request):
    """
    Reset execution data (status, reports, comments) for all test cases
    Optionally clear versions and create new version
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': 'Authentication required'})
    
    try:
        import json
        data = json.loads(request.body)
        action = data.get('action', 'reset_same_data')
        
        if action == 'reset_same_data':
            # Reset status, reports, comments for all executions
            TestExecution.objects.all().update(
                status="",
                reports="",
                comments=""
            )
            return JsonResponse({'success': True, 'message': 'Execution data reset successfully'})
        elif action == 'reset_and_clear_versions':
            # Clear all versions and reset execution data
            SWVersionMapping.objects.all().delete()
            TestExecution.objects.all().update(
                status="",
                reports="",
                comments=""
            )
            return JsonResponse({'success': True, 'message': 'Versions cleared and execution data reset'})
        else:
            return JsonResponse({'success': False, 'error': 'Invalid action'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def instruction_page(request):
    """Instruction page for new users"""
    return render(request, 'testmanager/instruction.html')


@login_required
@tester_or_above_required
def create_new_test_instance(request):
    """
    Create a new test instance, scoped to a specific feature.
    
    FEATURE-WISE INSTANCE CREATION:
    - Instance creation is now feature-scoped, not global
    - A new instance can be created when ONE feature is fully completed
    - Other features remain untouched and editable
    
    ROLE-BASED ACCESS:
    - Tester: CAN create new test instances (new testing cycle)
    - Test Engineer: CAN create new test instances
    - Developer: CAN create new test instances
    - Manager: CAN create new test instances
    
    GET: Show version-entry popup for user to enter version numbers manually.
    POST: Create snapshot, archive old instance, create new instance with user-entered versions.
    
    FEATURE COMPLETION REQUIREMENTS:
    - For a given feature, sheet, sw_part_number, version:
    - ALL TestExecution rows must have status IN (PASS / FAIL)
    - version.is_locked = True (manager approval)
    
    This function:
    1. Checks feature completion (not global completion)
    2. Freezes current active version as historical snapshot (for that feature only)
    3. Preserves all existing execution data without modification (history preserved)
    4. Creates new instance scoped to that feature
    5. Creates new version records with user-entered versions (is_active=True)
    6. Copies TestCase master records for that feature only
    7. Creates new execution rows only for the new version (feature-scoped)
    8. Resets only for the new version: status="", reports="", comments=""
    
    CRITICAL: Other features remain untouched and editable.
    """
    from django.db import transaction
    from django.contrib import messages
    from django.http import JsonResponse
    from django.shortcuts import render, redirect
    from ..services import is_feature_completed, get_active_instance
    from ..utils import get_requirement_id
    
    # FEATURE-WISE: Get feature, sheet, sw_part_number, version from request
    feature_name = request.GET.get("feature", "").strip() or request.POST.get("feature", "").strip()
    sheet_name = request.GET.get("sheet", "").strip() or request.POST.get("sheet", "").strip()
    sw_part_number = request.GET.get("sw", "").strip() or request.POST.get("sw", "").strip()
    app_sw_version = request.GET.get("version", "").strip() or request.POST.get("version", "").strip()
    
    active_instance = get_active_instance()
    
    # REMOVED: Auto-blocking at page entry
    # The form will load first and show completion status for selection
    
    if request.method == 'GET':
        # NEW FLOW: Use same APIs as export (get_sheets_api, get_versions_for_sheet_api, get_completed_features_for_sw_api)
        # Template will use JavaScript to call these APIs dynamically
        # URLs as JSON (no template tags in JavaScript)
        urls_json = json_module.dumps({
            'create_new_test_instance': reverse('create_new_test_instance'),
            'testcase_list': reverse('testcase_list'),
            'get_sheets_api': reverse('get_sheets_api'),
            'get_sw_part_numbers_for_sheet_api': reverse('get_sw_part_numbers_for_sheet_api'),
            'get_completed_features_for_sw_api': reverse('get_completed_features_for_sw_api'),
        })
        
        return render(request, 'testmanager/create_new_test_instance.html', {
            'urls_json': urls_json,  # Pass URLs as JSON string
        })
    
    # POST: Process version creation
    try:
        import json
        from ..models import SWVersionMapping
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        
        # NEW CONTRACT: Accept sw_part_numbers array and version_mappings dictionary
        # INSTANCE RULE: Ask version ONCE per sw_part_number
        # Popup must list UNIQUE sw_part_numbers and ask version for each
        sheet_name = data.get('sheet', '').strip()
        sw_part_numbers = data.get('sw_part_numbers', [])  # Array of SW Part Numbers
        version_mappings_input = data.get('version_mappings', {})  # Dictionary: {sw_part_number: version}
        feature_names = data.get('features', [])  # Array of feature names (optional - not required for instance creation)
        
        # Convert to list if it's a string (backward compatibility)
        if isinstance(sw_part_numbers, str):
            sw_part_numbers = [sw_part_numbers] if sw_part_numbers else []
        if isinstance(feature_names, str):
            feature_names = [feature_names] if feature_names else []
        if isinstance(version_mappings_input, str):
            try:
                import json
                version_mappings_input = json.loads(version_mappings_input)
            except:
                version_mappings_input = {}
        
        # Validate inputs
        if not sheet_name:
            return JsonResponse({
                'success': False,
                'error': 'Sheet name is required.'
            })
        
        if not sw_part_numbers or len(sw_part_numbers) == 0:
            return JsonResponse({
                'success': False,
                'error': 'Please select at least one SW Part Number.'
            })
        
        # INSTANCE RULE: Validate that version is provided for each SW Part Number
        version_mappings = {}
        missing_versions = []
        for sw_part_number in sw_part_numbers:
            sw_part_number = sw_part_number.strip() if sw_part_number else ''
            if not sw_part_number:
                continue
            
            version = version_mappings_input.get(sw_part_number, '').strip() if isinstance(version_mappings_input, dict) else ''
            if not version:
                missing_versions.append(sw_part_number)
            else:
                version_mappings[sw_part_number] = version
        
        if missing_versions:
            return JsonResponse({
                'success': False,
                'error': f'Please enter a version for each SW Part Number. Missing versions for: {", ".join(missing_versions)}'
            })
        
        # Auto-detect current active version for each SW Part Number
        sw_version_map = {}  # Map sw_part_number -> {version, version_obj, sheet_obj}
        incomplete_features = []
        from ..services import get_feature_completion
        
        for sw_part_number in sw_part_numbers:
            if not sw_part_number or not sw_part_number.strip():
                continue
            
            sw_part_number = sw_part_number.strip()
            
            # Get current active version for this SW Part Number
            mapping = SWVersionMapping.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                is_active=True
            ).first()
            
            if not mapping:
                return JsonResponse({
                    'success': False,
                    'error': f'No active version found for SW Part Number "{sw_part_number}".'
                })
            
            old_version = mapping.version
            
            # Get version object
            version_obj = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=old_version
            ).first()
            
            if not version_obj:
                return JsonResponse({
                    'success': False,
                    'error': f'Version "{old_version}" not found for SW Part Number "{sw_part_number}".'
                })
            
            # Get sheet object
            sheet_obj = TestCaseSheet.objects.filter(
                version=version_obj,
                sheet_name=sheet_name
            ).first()
            
            if not sheet_obj:
                return JsonResponse({
                    'success': False,
                    'error': f'Sheet "{sheet_name}" not found for SW Part Number "{sw_part_number}".'
                })
            
            sw_version_map[sw_part_number] = {
                'version': old_version,
                'version_obj': version_obj,
                'sheet_obj': sheet_obj
            }
            
            # Check completion for each feature
            for feature_name in feature_names:
                if not feature_name or not feature_name.strip():
                    continue
                feature_name = feature_name.strip()
                
                # Check if feature exists for this SW/version combination
                feature_exists = TestCase.objects.filter(
                    instance=active_instance,
                    sheet_name=sheet_name,
                    sw_part_number=sw_part_number,
                    app_sw_version=old_version,
                    feature=feature_name
                ).exists()
                
                if feature_exists:
                    total_count, completed_count, is_completed = get_feature_completion(
                        active_instance, version_obj, sheet_obj, feature_name
                    )
                    
                    if not is_completed:
                        incomplete_features.append(f'{feature_name} ({completed_count}/{total_count} tests executed) for {sw_part_number}')
        
        if incomplete_features:
            return JsonResponse({
                'success': False,
                'error': f'The following features are not fully completed: {", ".join(incomplete_features)}. All tests must be executed (PASS/FAIL) and version must be locked before creating a new instance.'
            })
        
        # Validate that new versions do NOT already exist for each SW Part Number
        existing_versions = []
        for sw_part_number, new_version in version_mappings.items():
            if not sw_part_number or not new_version:
                continue
            
            # Check if version already exists in current instance
            existing_version = TestCaseVersion.objects.filter(
                instance=active_instance,
                sw_part_number=sw_part_number,
                app_sw_version=new_version
            ).first()
            
            if existing_version:
                existing_versions.append(f'{sw_part_number} (version {new_version})')
        
        if existing_versions:
            return JsonResponse({
                'success': False,
                'error': f'The following SW Part Number(s) already have these versions: {", ".join(existing_versions)}. Please enter different version numbers.'
            })
        
        with transaction.atomic():  # type: ignore[assignment]
            # Get current active instance
            current_instance = get_active_instance()
            
            # NEW LOGIC: Filter test cases and executions by selected features and SW Part Numbers
            # Only clone test cases for selected features under selected SW Part Numbers (using current active versions)
            selected_sw_part_numbers = list(sw_version_map.keys())
            selected_old_versions = [info['version'] for info in sw_version_map.values()]
            
            # Filter test cases: selected sheet, selected SW part numbers, selected old versions, selected features
            test_cases = TestCase.objects.filter(
                instance=current_instance,
                sheet_name=sheet_name,
                sw_part_number__in=selected_sw_part_numbers,
                app_sw_version__in=selected_old_versions,
                feature__in=feature_names
            )
            
            # Filter executions: get all executions for the selected test cases
            test_case_ids = list(test_cases.values_list('id', flat=True))
            executions = TestExecution.objects.filter(
                instance=current_instance,
                test_case_id__in=test_case_ids
            ).select_related('test_case')
            
            # REMOVED: Snapshot system completely removed
            # No snapshot creation, no DB reset, no auto-clear logic
            
            # INSTANCE RULE: Create new instance and deactivate old one
            # Old instance -> is_active=False (deactivate BEFORE creating new one to avoid conflicts)
            # New instance -> is_active=True
            # status/reports/comments reset ONLY during instance creation (new executions start empty)
            
            # Deactivate old instance first (only one instance can be active at a time)
            TestInstance.objects.filter(is_active=True).update(is_active=False)
            
            # Create new active instance
            new_instance = TestInstance.objects.create(is_active=True)
            
            # Create new version records and test cases for new instance
            # Process each selected SW Part Number separately
            for sw_part_number, new_version in version_mappings.items():
                # Get old version from sw_version_map (auto-detected)
                if sw_part_number not in sw_version_map:
                    continue
                
                version_info = sw_version_map[sw_part_number]
                old_version = version_info['version']
                version_obj = version_info['version_obj']
                
                # CRITICAL: Create EXACTLY ONE TestCaseVersion record per SW Part Number for new instance
                # This version applies to ALL test cases with this SW Part Number in the new instance
                # Note: Version existence was already validated above, so this should always create a new one
                new_version_obj = TestCaseVersion.objects.create(
                    instance=new_instance,
                    sw_part_number=sw_part_number,
                    app_sw_version=new_version,
                    is_active=True,
                    is_locked=False
                )
                
                # Create new version mapping (is_active=True) for backward compatibility
                SWVersionMapping.objects.create(
                    instance=new_instance,
                    sw_part_number=sw_part_number,
                    version=new_version,
                    is_active=True
                )
                
                # NEW LOGIC: Get test cases ONLY for this SW part number, selected features, and old version
                old_test_cases = test_cases.filter(
                    sw_part_number=sw_part_number,
                    app_sw_version=old_version,
                    feature__in=feature_names
                )
                
                # Get all unique sheet names for these test cases
                sheet_names = set(old_test_cases.values_list('sheet_name', flat=True).distinct())
                
                # Create TestCaseSheet records for this version (one per sheet)
                for sheet_name_item in sheet_names:
                    if sheet_name_item:
                        TestCaseSheet.objects.get_or_create(
                            version=new_version_obj,
                            sheet_name=sheet_name_item
                        )
                
                # Create new test cases with new version
                # CLONE all test case data from old version to new version
                new_test_cases = []
                for old_tc in old_test_cases:
                    # Build new test_case_id with new version
                    # Use base_test_case_id if available, otherwise derive from test_case_id
                    if hasattr(old_tc, 'base_test_case_id') and old_tc.base_test_case_id:
                        base_test_case_id_value = old_tc.base_test_case_id
                    else:
                        # Fallback: derive base from test_case_id
                        base_test_case_id_value = old_tc.test_case_id
                        # Remove old version suffix if exists
                        if old_version and base_test_case_id_value.endswith(f"_{old_version}"):
                            base_test_case_id_value = base_test_case_id_value[:-len(f"_{old_version}")]
                    
                    new_test_case_id = f"{base_test_case_id_value}_{new_version}" if new_version else base_test_case_id_value
                    
                    # Check if new test case already exists (by unique constraint: instance + base_test_case_id + app_sw_version)
                    if TestCase.objects.filter(
                        instance=new_instance,
                        base_test_case_id=base_test_case_id_value,
                        app_sw_version=new_version
                    ).exists():
                        continue
                    
                    # CLONE: Create new test case by copying ALL fields from old version
                    # CRITICAL: Old version test case data remains COMPLETELY UNCHANGED in old instance
                    # - Old test cases are NOT modified
                    # - Old execution data (status, reports, comments) is NOT modified
                    # - Old version data stays intact and visible for comparison
                    # New version starts with EMPTY execution fields (status, reports, comments)
                    new_tc = TestCase(
                        instance=new_instance,
                        sheet_name=old_tc.sheet_name,
                        sl_no=old_tc.sl_no,
                        sw_part_number=sw_part_number,
                        feature=old_tc.feature,
                        requirement_id=old_tc.requirement_id,
                        app_sw_version=new_version,  # USER-ENTERED VERSION
                        requirement_description=old_tc.requirement_description,
                        base_test_case_id=base_test_case_id_value,  # Copy base_test_case_id
                        test_case_id=new_test_case_id,  # New versioned test_case_id
                        test_case_summary=old_tc.test_case_summary,
                        pre_conditions=old_tc.pre_conditions,
                        inputs=old_tc.inputs,
                        periodic_time=old_tc.periodic_time,
                        test_steps=old_tc.test_steps,
                        expected_result=old_tc.expected_result,
                        # Execution fields are EMPTY for new version (not copied from old)
                        status="",  # EMPTY - new version starts clean
                        reports="",  # EMPTY - new version starts clean
                        comments="",  # EMPTY - new version starts clean
                    )
                    new_test_cases.append(new_tc)
                
                # Bulk create new test cases for this SW Part Number
                if new_test_cases:
                    try:
                        TestCase.objects.bulk_create(new_test_cases, batch_size=500)
                    except Exception as e:
                        messages.error(request, f'Error creating new test cases: {str(e)}')
                        return redirect('create_new_test_instance')
                    
                    # Refetch created test cases to get their IDs (needed for foreign key)
                    created_test_case_ids = [tc.test_case_id for tc in new_test_cases]
                    created_test_cases = TestCase.objects.filter(
                        instance=new_instance,
                        test_case_id__in=created_test_case_ids
                    )
                    
                    # CRITICAL: Create new execution rows linked to TestCaseVersion
                    # ALL test cases for this SW Part Number get the SAME version
                    new_executions = []
                    for new_tc in created_test_cases:
                        # Get TestCaseSheet for this test case
                        sheet_obj = TestCaseSheet.objects.filter(
                            version=new_version_obj,
                            sheet_name=new_tc.sheet_name
                        ).first()
                        
                        if not TestExecution.objects.filter(
                            instance=new_instance,
                            test_case=new_tc,
                            version=new_version_obj  # CRITICAL: Link to TestCaseVersion
                        ).exists():
                            new_executions.append(
                                TestExecution(
                                    instance=new_instance,
                                    test_case=new_tc,
                                    version=new_version_obj,  # CRITICAL: Link to TestCaseVersion
                                    sheet=sheet_obj,  # CRITICAL: Link to TestCaseSheet
                                    sw_part_number=sw_part_number,
                                    app_sw_version=new_version,  # For backward compatibility
                                    status="",  # Reset for new version
                                    reports="",  # Reset for new version
                                    comments="",  # Reset for new version
                                )
                            )
                    
                    if new_executions:
                        TestExecution.objects.bulk_create(new_executions, batch_size=500)
            
            # Log activity
            ActivityLog.objects.create(
                user=request.user,
                action="EDIT",
                reference=f"Instance {current_instance.id} → {new_instance.id}",
                remarks=f"Created new test instance for features: {', '.join(feature_names)}. Instance {new_instance.id} is now active.",
                content_type="System",
            )
        
        return JsonResponse({
            'success': True,
            'message': f'New test instance created successfully for selected features. Instance {new_instance.id} is now active.',
            'new_instance_id': new_instance.id,
            'redirect_url': reverse('testcase_list')
        })
        
    except Exception as e:
        import traceback
        return JsonResponse({
            'success': False,
            'error': f'Error creating new instance: {str(e)}',
            'traceback': traceback.format_exc()
        }, status=500)


@login_required
@tester_or_above_required(json_response=True)
def get_feature_completion_status_api(request):
    """
    API endpoint to get feature completion status for instance creation.
    
    Returns JSON with:
    - features: List of dicts, each containing:
      - feature_name: Name of the feature
      - sheet_name: Sheet name
      - sw_part_number: SW Part Number
      - app_sw_version: Application SW Version
      - is_completed: True if feature is fully completed (all tests PASS/FAIL and version locked)
      - executed_count: Number of executed tests
      - total_count: Total number of tests
      - can_create_instance: True if new instance can be created for this feature
    """
    from ..services import is_feature_completed, get_active_instance
    
    active_instance = get_active_instance()
    
    # Get all unique feature-sheet-sw-version combinations
    features_data = []
    
    # Get all versions
    versions = TestCaseVersion.objects.filter(instance=active_instance).select_related()
    
    for version_obj in versions:
        # Get all sheets for this version
        sheets = TestCaseSheet.objects.filter(version=version_obj).select_related()
        
        for sheet_obj in sheets:
            # Get all features for this sheet+version combination
            features = TestCase.objects.filter(
                instance=active_instance,
                sw_part_number=version_obj.sw_part_number,
                app_sw_version=version_obj.app_sw_version,
                sheet_name=sheet_obj.sheet_name
            ).exclude(feature__isnull=True).exclude(feature__exact="").values_list('feature', flat=True).distinct()
            
            for feature_name in features:
                is_completed, executed_count, total_count = is_feature_completed(
                    active_instance, version_obj, sheet_obj, feature_name
                )
                
                features_data.append({
                    'feature_name': feature_name,
                    'sheet_name': sheet_obj.sheet_name,
                    'sw_part_number': version_obj.sw_part_number,
                    'app_sw_version': version_obj.app_sw_version,
                    'is_completed': is_completed,
                    'executed_count': executed_count,
                    'total_count': total_count,
                    'can_create_instance': is_completed,
                })
    
    return JsonResponse({
        'ok': True,
        'features': features_data,
    })

