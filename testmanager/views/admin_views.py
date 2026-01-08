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
        # INLINE SELECTION: Show form with dependent dropdowns and completion status
        active_instance = get_active_instance()
        
        # Get all sheets, SW part numbers, versions, and features with completion status
        from ..services import get_feature_completion
        
        # Get all versions
        versions = TestCaseVersion.objects.filter(instance=active_instance).select_related()
        
        # Build data structure for dependent dropdowns
        sheets_data = []  # List of {sheet_name, sw_part_numbers: [...]}
        features_data = []  # List of {feature_name, sheet_name, sw_part_number, version, completion: {...}}
        
        sheet_set = set()
        sw_set = set()
        version_set = set()
        
        for version_obj in versions:
            # Get all sheets for this version
            sheets = TestCaseSheet.objects.filter(version=version_obj).select_related()
            
            for sheet_obj in sheets:
                sheet_name = sheet_obj.sheet_name
                sw_part_number = version_obj.sw_part_number
                app_sw_version = version_obj.app_sw_version
                
                sheet_set.add(sheet_name)
                sw_set.add(sw_part_number)
                version_set.add((sw_part_number, app_sw_version))
                
                # Get all features for this sheet+version combination
                features = TestCase.objects.filter(
                    instance=active_instance,
                    sw_part_number=sw_part_number,
                    app_sw_version=app_sw_version,
                    sheet_name=sheet_name
                ).exclude(feature__isnull=True).exclude(feature__exact="").values_list('feature', flat=True).distinct()
                
                for feature_name in features:
                    # Calculate completion for this feature scope
                    total_count, completed_count, is_completed = get_feature_completion(
                        active_instance, version_obj, sheet_obj, feature_name
                    )
                    
                    features_data.append({
                        'feature_name': feature_name,
                        'sheet_name': sheet_name,
                        'sw_part_number': sw_part_number,
                        'app_sw_version': app_sw_version,
                        'total_count': total_count,
                        'completed_count': completed_count,
                        'is_completed': is_completed,
                    })
        
        # Sort for display
        sheets_list = sorted(list(sheet_set))
        sw_list = sorted(list(sw_set))
        versions_list = sorted([(sw, ver) for sw, ver in version_set])
        
        # Convert features_data to JSON for JavaScript
        features_data_json = json_module.dumps(features_data)
        
        # Pre-select if parameters provided (convert to JSON-safe format)
        selected_sheet = json_module.dumps(sheet_name) if sheet_name else 'null'
        selected_sw = json_module.dumps(sw_part_number) if sw_part_number else 'null'
        selected_version = json_module.dumps(app_sw_version) if app_sw_version else 'null'
        selected_feature = json_module.dumps(feature_name) if feature_name else 'null'
        
        # URLs as JSON (no template tags in JavaScript)
        urls_json = json_module.dumps({
            'create_new_test_instance': reverse('create_new_test_instance'),
            'testcase_list': reverse('testcase_list'),
        })
        
        return render(request, 'testmanager/create_new_test_instance.html', {
            'sheets_list': sheets_list,
            'sw_list': sw_list,
            'versions_list': versions_list,
            'features_data': features_data_json,  # Pass as JSON string
            'selected_sheet': selected_sheet,
            'selected_sw': selected_sw,
            'selected_version': selected_version,
            'selected_feature': selected_feature,
            'urls_json': urls_json,  # Pass URLs as JSON string
        })
    
    # POST: Process version creation
    try:
        import json
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        
        # Get feature scope from form (required) - support both single feature (backward compat) and multiple features
        feature_names = data.get('features', [])
        if not feature_names:
            # Backward compatibility: check for single 'feature' field
            single_feature = data.get('feature', '').strip()
            if single_feature:
                feature_names = [single_feature]
        
        # Convert to list if it's a string (backward compatibility)
        if isinstance(feature_names, str):
            feature_names = [feature_names] if feature_names else []
        
        sheet_name = data.get('sheet', '').strip() or sheet_name
        sw_part_number = data.get('sw', '').strip() or sw_part_number
        app_sw_version = data.get('version', '').strip() or app_sw_version
        new_version = data.get('new_version', '').strip()
        
        # Validate feature scope is selected
        if not (feature_names and len(feature_names) > 0 and sheet_name and sw_part_number and app_sw_version):
            return JsonResponse({
                'success': False,
                'error': 'Please select at least one feature, and provide Sheet, SW Part Number, and Version.'
            })
        
        # Resolve version and sheet objects
        version_obj = TestCaseVersion.objects.filter(
            instance=active_instance,
            sw_part_number=sw_part_number,
            app_sw_version=app_sw_version
        ).first()
        
        if not version_obj:
            return JsonResponse({
                'success': False,
                'error': f'Version "{app_sw_version}" not found for SW Part Number "{sw_part_number}".'
            })
        
        sheet_obj = TestCaseSheet.objects.filter(
            version=version_obj,
            sheet_name=sheet_name
        ).first()
        
        if not sheet_obj:
            return JsonResponse({
                'success': False,
                'error': f'Sheet "{sheet_name}" not found for version "{app_sw_version}".'
            })
        
        # Validate all selected features are completed before allowing instance creation
        from ..services import get_feature_completion
        incomplete_features = []
        for feature_name in feature_names:
            if not feature_name or not feature_name.strip():
                continue
            feature_name = feature_name.strip()
            total_count, completed_count, is_completed = get_feature_completion(
                active_instance, version_obj, sheet_obj, feature_name
            )
            
            if not is_completed:
                incomplete_features.append(f'{feature_name} ({completed_count}/{total_count} tests executed)')
        
        if incomplete_features:
            return JsonResponse({
                'success': False,
                'error': f'The following features are not fully completed: {", ".join(incomplete_features)}. All tests must be executed (PASS/FAIL) and version must be locked before creating a new instance.'
            })
        
        # Get new version number
        if not new_version:
            return JsonResponse({
                'success': False,
                'error': 'Please enter a new version number.'
            })
        
        # FEATURE-WISE: Only create version mapping for this SW part number
        version_mappings = {sw_part_number: new_version}
        
        with transaction.atomic():  # type: ignore[assignment]
            # Get current active instance
            current_instance = get_active_instance()
            
            # FEATURE-WISE: Filter test cases and executions by features (REQUIRED)
            # Instance creation is now feature-scoped, not global
            test_cases = TestCase.objects.filter(instance=current_instance)
            executions = TestExecution.objects.filter(instance=current_instance).select_related('test_case')
            
            # FEATURE-WISE: Always filter to specific features (required for feature-scoped creation)
            if feature_names and sheet_name and sw_part_number and app_sw_version:
                # FEATURE-WISE: Filter to selected features only
                test_cases = test_cases.filter(
                    feature__in=feature_names,
                    sheet_name=sheet_name,
                    sw_part_number=sw_part_number,
                    app_sw_version=app_sw_version
                )
                
                # Resolve version and sheet objects for execution filtering
                version_obj = TestCaseVersion.objects.filter(
                    instance=current_instance,
                    sw_part_number=sw_part_number,
                    app_sw_version=app_sw_version
                ).first()
                
                if version_obj:
                    sheet_obj = TestCaseSheet.objects.filter(
                        version=version_obj,
                        sheet_name=sheet_name
                    ).first()
                    
                    if sheet_obj:
                        executions = executions.filter(
                            version=version_obj,
                            sheet=sheet_obj,
                            test_case__feature__in=feature_names
                        )
            
            # STRICT: Create snapshot with all execution data (preserve all existing data permanently)
            # Previous version data must remain untouched and intact
            # This snapshot preserves execution data: status, reports, comments, execution results
            execution_data = []
            for exec in executions:
                tc = exec.test_case
                display_requirement_id = get_requirement_id(tc.test_case_id, tc.requirement_id)
                
                exec_dict = {
                    'test_case_id': tc.test_case_id,
                    'test_case_db_id': tc.id,
                    'sheet_name': tc.sheet_name,
                    'sl_no': tc.sl_no,
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
                    'app_sw_version': tc.app_sw_version,
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
                }
                execution_data.append(exec_dict)
            
            # Calculate snapshot statistics (feature-scoped if applicable)
            total_count = test_cases.count()
            executed_count = executions.exclude(status__isnull=True).exclude(status__exact="").count()
            total_passed = sum(1 for e in executions if e.status and e.status.upper() == 'PASS')
            total_failed = sum(1 for e in executions if e.status and e.status.upper() == 'FAIL')
            total_not_executed = total_count - executed_count
            
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
            
            # FEATURE-WISE: Create snapshot name with feature info if applicable
            snapshot_name = f"Instance {current_instance.id} - {current_instance.created_at.strftime('%Y-%m-%d %H:%M')}"
            if feature_names:
                if len(feature_names) == 1:
                    snapshot_name += f" - Feature: {feature_names[0]}"
                else:
                    snapshot_name += f" - Features: {', '.join(feature_names)}"
            
            snapshot = TestExecutionSnapshot.objects.create(
                instance=current_instance,
                export_id=export_id,
                snapshot_name=snapshot_name,
                exported_by=request.user,
                execution_data=execution_data,
                total_test_cases=total_count,
                total_executed=executed_count,
                total_passed=total_passed,
                total_failed=total_failed,
                total_not_executed=total_not_executed,
            )
            
            # Get old active versions BEFORE archiving (needed for test_case_id processing)
            old_active_versions = {}
            for sw_part_number in version_mappings.keys():
                old_mapping = SWVersionMapping.objects.filter(
                    instance=current_instance,
                    sw_part_number=sw_part_number,
                    is_active=True
                ).first()
                if old_mapping:
                    old_active_versions[sw_part_number] = old_mapping.version
            
            # FEATURE-WISE: Only archive instance if ALL features are completed (backward compatibility)
            # If feature-scoped, create new instance without archiving old one
            # This allows parallel testing of different features
            if feature_names:
                # FEATURE-WISE: Create new instance WITHOUT archiving old one
                # Old instance remains active for other features
                new_instance = TestInstance.objects.create(is_active=True)
            else:
                # GLOBAL: Archive current instance (backward compatibility)
                # STRICT: Archive current instance (preserve all data permanently)
                # Previous version data remains untouched - status, reports, comments preserved
                # Old versions are marked as inactive but data is never modified or overwritten
                current_instance.is_active = False
                current_instance.save()
                
                # STRICT: Mark all previous versions as inactive (data remains preserved)
                # Previous versions retain their original status, reports, comments, execution results
                # Old version data is never refreshed, reset, or overwritten
                SWVersionMapping.objects.filter(instance=current_instance).update(is_active=False)
                
                # Create new active instance
                new_instance = TestInstance.objects.create(is_active=True)
            
            # Create new version records and test cases for new instance
            for sw_part_number, new_version in version_mappings.items():
                # CRITICAL: Mark all previous versions of this SW Part Number as inactive
                # This ensures only ONE active version per SW Part Number per instance
                TestCaseVersion.objects.filter(
                    instance=current_instance,
                    sw_part_number=sw_part_number,
                    is_active=True
                ).update(is_active=False)
                
                # CRITICAL: Create EXACTLY ONE TestCaseVersion record per SW Part Number
                # This version applies to ALL test cases with this SW Part Number
                new_version_obj, created = TestCaseVersion.objects.get_or_create(
                    instance=new_instance,
                    sw_part_number=sw_part_number,
                    app_sw_version=new_version,
                    defaults={
                        'is_active': True,
                        'is_locked': False
                    }
                )
                
                # If version already exists, ensure it's active
                if not created:
                    new_version_obj.is_active = True
                    new_version_obj.is_locked = False
                    new_version_obj.save()
                
                # Create new version mapping (is_active=True) for backward compatibility
                SWVersionMapping.objects.create(
                    instance=new_instance,
                    sw_part_number=sw_part_number,
                    version=new_version,
                    is_active=True
                )
                
                # FEATURE-WISE: Get test cases for this SW part number
                # If feature-scoped, only get test cases for selected features
                # Otherwise, get all test cases (backward compatibility)
                old_test_cases = test_cases.filter(sw_part_number=sw_part_number)
                
                if feature_names:
                    # FEATURE-WISE: Filter to selected features only
                    old_test_cases = old_test_cases.filter(feature__in=feature_names)
                
                # Get old version for this SW part number (if exists)
                old_version = old_active_versions.get(sw_part_number)
                
                # Get all unique sheet names for this SW Part Number
                sheet_names = set(old_test_cases.values_list('sheet_name', flat=True).distinct())
                
                # Create TestCaseSheet records for this version (one per sheet)
                for sheet_name in sheet_names:
                    if sheet_name:
                        TestCaseSheet.objects.get_or_create(
                            version=new_version_obj,
                            sheet_name=sheet_name
                        )
                
                # Create new test cases with new version
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
                        instance=new_instance,
                        test_case_id=new_test_case_id
                    ).exists():
                        continue
                    
                    # STRICT: Create new test case with new version
                    # Previous version test case data remains untouched and preserved
                    # Execution data (status, reports, comments) is NOT copied from previous versions
                    # New version starts with empty execution fields - old version data stays intact
                    new_tc = TestCase(
                        instance=new_instance,
                        sheet_name=old_tc.sheet_name,
                        sl_no=old_tc.sl_no,
                        sw_part_number=sw_part_number,
                        feature=old_tc.feature,
                        requirement_id=old_tc.requirement_id,
                        app_sw_version=new_version,
                        requirement_description=old_tc.requirement_description,
                        test_case_id=new_test_case_id,
                        test_case_summary=old_tc.test_case_summary,
                        pre_conditions=old_tc.pre_conditions,
                        inputs=old_tc.inputs,
                        periodic_time=old_tc.periodic_time,
                        test_steps=old_tc.test_steps,
                        expected_result=old_tc.expected_result,
                        status="",  # STRICT: Reset status for new version - do NOT copy from old version
                        reports="",  # STRICT: Reset reports for new version - do NOT copy from old version
                        comments="",  # STRICT: Reset comments for new version - do NOT copy from old version
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
                                    version=new_version_obj,  # CRITICAL: Link to TestCaseVersion (not just app_sw_version)
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
                remarks=f"Created new test instance. Archived instance {current_instance.id}, created instance {new_instance.id}",
                content_type="System",
            )
        
        return JsonResponse({
            'success': True,
            'message': f'New test instance created successfully. Instance {current_instance.id} archived, Instance {new_instance.id} is now active.',
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

