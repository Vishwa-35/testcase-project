from __future__ import annotations

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class TestInstance(models.Model):
    """Represents a test instance cycle. Only one instance can be active at a time."""
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="Only one instance should be active at a time") # type: ignore
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Test Instance"
        verbose_name_plural = "Test Instances"
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        status = "ACTIVE" if self.is_active else "ARCHIVED"
        return f"Instance {self.id} - {status} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
    
    def save(self, *args, **kwargs):
        # Ensure only one instance is active at a time
        if self.is_active:
            TestInstance.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class TestCase(models.Model):
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="test_cases",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this test case belongs to"
    )
    sheet_name = models.CharField(max_length=100, db_index=True)

    sl_no = models.CharField(max_length=50, blank=True, db_index=True)
    sw_part_number = models.CharField(max_length=200, blank=True, db_index=True)
    feature = models.TextField(blank=True)

    requirement_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Requirement ID"
    )
    app_sw_version = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Application Software Version"
    )

    requirement_description = models.TextField(blank=True)

    base_test_case_id = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Original test case ID from Excel (without version suffix)"
    )
    
    test_case_id = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Versioned test case ID (base_test_case_id + version suffix)"
    )

    test_case_summary = models.TextField(blank=True)
    pre_conditions = models.TextField(blank=True)
    inputs = models.TextField(blank=True)
    periodic_time = models.CharField(max_length=100, blank=True)
    test_steps = models.TextField(blank=True)
    expected_result = models.TextField(blank=True)
    status = models.CharField(max_length=100, blank=True, db_index=True, help_text="Design / specification status only (NOT execution result)")
    reports = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # STRICT: sl_no and sheet_name do NOT exist - removed after hierarchy refactor
        # Order by id (primary key) instead of deprecated fields
        ordering = ["id"]
        # Unique constraint: (base_test_case_id + app_sw_version + instance) must be unique
        # This allows same base_test_case_id across different versions
        unique_together = [('instance', 'base_test_case_id', 'app_sw_version')]

    def __str__(self):
        return f"{self.sheet_name} → {self.test_case_id}"


class SheetMeta(models.Model):
    sheet_name = models.CharField(max_length=100, db_index=True)
    headers = models.JSONField(default=list)

    def __str__(self):
        return f"{self.sheet_name}"


class SWVersionMapping(models.Model):
    """
    Stores version numbers for each SW Part Number per instance.
    
    PART 3: Version entry popup is the SINGLE SOURCE OF TRUTH.
    This model stores the version mapping entered by the user in the popup.
    """
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="version_mappings",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this version mapping belongs to"
    )
    sw_part_number = models.CharField(max_length=200, db_index=True)
    version = models.CharField(max_length=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="Only one version per SW part number should be active at a time") # type: ignore
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SW Version Mapping"
        verbose_name_plural = "SW Version Mappings"
        ordering = ['sw_part_number']
        unique_together = [('instance', 'sw_part_number')]  # One version per SW part number per instance

    def __str__(self):
        return f"{self.sw_part_number} → {self.version} (Instance {self.instance_id if self.instance else 'None'})"

class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ("ADD", "Add"),
        ("EDIT", "Edit"),
        # ("DELETE", "Delete"),
        ("IMPORT", "Import"),
        ("LOGOUT", "Logout"),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reference = models.CharField(max_length=255, blank=True)
    remarks = models.TextField(blank=True)

    content_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="TestCase / SheetMeta / System"
    )

    # ✅ ADD THIS FIELD
    diff = models.JSONField(
        blank=True,
        null=True,
        help_text="Stores old → new field values"
    )

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)


class ProjectOverview(models.Model):
    """
    Project Overview model for version-specific structured data storage.
    
    CRITICAL: Linked explicitly to TestCaseVersion and SW Part Number.
    Each ProjectOverview row represents metadata for ONE:
    - SW Part Number
    - Application Version (via TestCaseVersion)
    
    ProjectOverview is frozen automatically when TestCaseVersion is locked.
    """
    # CRITICAL: Explicit FK to TestCaseVersion (version-aware binding)
    version = models.ForeignKey(
        'TestCaseVersion',
        on_delete=models.CASCADE,
        related_name="project_overviews",
        db_index=True,
        null=True,
        blank=True,
        help_text="The version this project overview belongs to (explicit FK)"
    )
    
    # Version-specific identifier fields (for backward compatibility and direct access)
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="project_overviews",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this project overview belongs to"
    )
    software_part_number = models.CharField(max_length=200, db_index=True, blank=True, help_text="Software Part Number")
    application_sw_version = models.CharField(max_length=100, db_index=True, blank=True, help_text="Application SW Version")
    
    # Legacy field name for backward compatibility (maps to software_part_number)
    sw_part_number = models.CharField(max_length=200, db_index=True, blank=True)
    app_sw_version = models.CharField(max_length=100, db_index=True, blank=True)
    
    # Legacy fields (for backward compatibility)
    key = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    value = models.TextField(blank=True)
    sheet_name = models.CharField(max_length=100, db_index=True, blank=True)
    overview_text = models.TextField(blank=True, help_text="Legacy overview text field")
    
    # Structured Project Overview fields
    project_code = models.CharField(max_length=200, blank=True, help_text="Project Code")
    vcu_platform = models.CharField(max_length=200, blank=True, help_text="VCU Platform")
    hardware_part_number = models.CharField(max_length=200, blank=True, help_text="Hardware Part Number")
    project_stage = models.CharField(max_length=200, blank=True, help_text="Project Stage")
    developer = models.CharField(max_length=200, blank=True, help_text="Developer")
    test_engineer = models.CharField(max_length=200, blank=True, help_text="Test Engineer")
    bootloader_sw_version = models.CharField(max_length=200, blank=True, help_text="Bootloader SW Version")
    checksum_value = models.CharField(max_length=200, blank=True, help_text="Checksum Value")
    dbc_test_it = models.TextField(blank=True, help_text="DBC test_it (file link or text reference)")
    
    # Metadata
    created_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_overviews",
        help_text="User who created this project overview"
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        permissions = [
            ("edit_project_overview", "Can edit project overview"),
        ]
        # Unique constraint: one ProjectOverview per TestCaseVersion
        unique_together = [
            ('version',)  # One ProjectOverview per version
        ]
        indexes = [
            models.Index(fields=['version']),
            models.Index(fields=['instance', 'software_part_number', 'application_sw_version']),
            models.Index(fields=['-updated_at']),
        ]
        verbose_name = "Project Overview"
        verbose_name_plural = "Project Overviews"
        ordering = ['-updated_at']
        
    def __str__(self):
        if self.version:
            return f"{self.version.sw_part_number} → {self.version.app_sw_version}"
        elif self.instance and self.software_part_number and self.application_sw_version:
            return f"{self.software_part_number} → {self.application_sw_version}"
        elif self.instance and self.sw_part_number and self.app_sw_version:
            return f"{self.sw_part_number} → {self.app_sw_version}"
        elif self.key:
            return self.key
        return f"Project Overview {self.id}"
    
    @property
    def is_locked(self):
        """Check if this ProjectOverview is locked (version is locked)"""
        if self.version:
            return self.version.is_locked
        return False
    
    def save(self, *args, **kwargs):
        """Sync fields from TestCaseVersion and legacy field names"""
        # Sync from TestCaseVersion if available
        if self.version:
            self.instance = self.version.instance
            self.software_part_number = self.version.sw_part_number
            self.application_sw_version = self.version.app_sw_version
            self.sw_part_number = self.version.sw_part_number
            self.app_sw_version = self.version.app_sw_version
        
        # Sync legacy field names for backward compatibility
        if self.software_part_number and not self.sw_part_number:
            self.sw_part_number = self.software_part_number
        if self.application_sw_version and not self.app_sw_version:
            self.app_sw_version = self.application_sw_version
        super().save(*args, **kwargs)

class TestCaseVersion(models.Model):
    """
    Represents a version for a SW Part Number.
    Only ONE active version per SW part number at any time.
    
    EXECUTION STATUS LIFECYCLE:
    - NOT_STARTED: No executions exist yet
    - IN_PROGRESS: Some tests executed, but not all
    - COMPLETED: All tests executed
    - APPROVED: Manager approved, version locked forever
    """
    EXECUTION_STATUS_CHOICES = [
        ('NOT_STARTED', 'Not Started'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('APPROVED', 'Approved'),
    ]
    
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="test_case_versions",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this version belongs to"
    )
    sw_part_number = models.CharField(max_length=200, db_index=True)
    app_sw_version = models.CharField(max_length=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="Only one active version per SW part number should exist") # type: ignore
    is_locked = models.BooleanField(default=False, db_index=True, help_text="Locked versions are read-only for everyone") # type: ignore
    execution_status = models.CharField(
        max_length=20,
        choices=EXECUTION_STATUS_CHOICES,
        default='NOT_STARTED',
        db_index=True,
        help_text="Final execution status of this version. APPROVED versions are immutable."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Test Case Version"
        verbose_name_plural = "Test Case Versions"
        unique_together = [('instance', 'sw_part_number', 'app_sw_version')]
        indexes = [
            models.Index(fields=['instance', 'sw_part_number', 'is_active']),
            models.Index(fields=['is_locked']),
            models.Index(fields=['execution_status']),
        ]
    
    def save(self, *args, **kwargs):
        # CRITICAL: Prevent any changes to APPROVED versions
        if self.pk:
            old_obj = TestCaseVersion.objects.get(pk=self.pk)
            if old_obj.execution_status == 'APPROVED':
                # APPROVED versions are immutable - only allow status to remain APPROVED
                if self.execution_status != 'APPROVED':
                    raise ValueError("Cannot modify APPROVED version. Status must remain APPROVED.")
                # Prevent changes to other fields
                self.is_locked = True  # Force locked
                self.is_active = old_obj.is_active
                self.sw_part_number = old_obj.sw_part_number
                self.app_sw_version = old_obj.app_sw_version
                self.instance = old_obj.instance
        
        # Ensure only one active version per SW part number
        if self.is_active:
            TestCaseVersion.objects.filter(
                instance=self.instance,
                sw_part_number=self.sw_part_number
            ).exclude(pk=self.pk).update(is_active=False)
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.sw_part_number} → {self.app_sw_version} (Instance {self.instance_id if self.instance else 'None'})"


class TestCaseSheet(models.Model):
    """
    Represents a sheet within a TestCaseVersion.
    """
    version = models.ForeignKey(
        'TestCaseVersion',
        on_delete=models.CASCADE,
        related_name="sheets",
        db_index=True
    )
    sheet_name = models.CharField(max_length=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Test Case Sheet"
        verbose_name_plural = "Test Case Sheets"
        unique_together = [('version', 'sheet_name')]
        indexes = [
            models.Index(fields=['version', 'sheet_name']),
        ]
    
    def __str__(self):
        return f"{self.version} → {self.sheet_name}"


class TestExecution(models.Model):
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="executions",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this execution belongs to"
    )
    test_case = models.ForeignKey(
        TestCase,
        on_delete=models.CASCADE,
        related_name="executions"
    )
    
    # CRITICAL: Explicit FK to TestCaseVersion and TestCaseSheet
    # Version must NEVER be inferred dynamically after save
    version = models.ForeignKey(
        'TestCaseVersion',
        on_delete=models.CASCADE,
        related_name="executions",
        db_index=True,
        null=True,
        blank=True,
        help_text="The version this execution belongs to (explicit FK)"
    )
    sheet = models.ForeignKey(
        'TestCaseSheet',
        on_delete=models.CASCADE,
        related_name="executions",
        db_index=True,
        null=True,
        blank=True,
        help_text="The sheet this execution belongs to (explicit FK)"
    )
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    # Keep these for backward compatibility and filtering
    sw_part_number = models.CharField(
        max_length=200,
        db_index=True
    )

    app_sw_version = models.CharField(
        max_length=100,
        db_index=True
    )

    status = models.CharField(
        max_length=100,
        blank=True,
        db_index=True
    )

    reports = models.TextField(blank=True)
    comments = models.TextField(blank=True)

    executed_at = models.DateTimeField(auto_now=True)
    
    # Manager approval flag - only managers can check/uncheck this
    manager_approved = models.BooleanField(default=False, db_index=True, help_text="Manager approval that test is completed") # type: ignore
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_executions", help_text="Manager who approved this test")
    approved_at = models.DateTimeField(null=True, blank=True, help_text="When manager approved this test")
    
    # CRITICAL: is_locked flag - when version is locked, execution is locked
    is_locked = models.BooleanField(default=False, db_index=True, help_text="Locked executions are read-only for everyone") # type: ignore

    def save(self, *args, **kwargs):
        """
        CRITICAL: Sync version execution_status after saving execution.
        This ensures version status reflects the actual execution state.
        """
        # Prevent saving if version is APPROVED (immutable)
        if self.version and self.version.execution_status == 'APPROVED':
            raise ValueError("Cannot modify execution for APPROVED version. Version is immutable.")
        
        super().save(*args, **kwargs)
        
        # CRITICAL: Recalculate version status after saving execution
        if self.version:
            from testmanager.services import recalculate_version_status
            recalculate_version_status(self.version)

    class Meta:
        unique_together = (
            "instance",
            "test_case",
            "version",
            "sheet",
        )
        ordering = ["test_case__id"]
        indexes = [
            models.Index(fields=["instance", "test_case", "version", "sheet"]),
            models.Index(fields=["-executed_at"]),
            models.Index(fields=["manager_approved"]),
            models.Index(fields=["is_locked"]),
            models.Index(fields=["instance"]),
        ]

    def __str__(self):
        return (
            f"{self.test_case.test_case_id} | "
            f"{self.sw_part_number} | "
            f"{self.app_sw_version}"
        )


class TestExecutionSnapshot(models.Model):
    """Stores snapshots of test executions when exported, allowing repeatable testing cycles"""
    instance = models.ForeignKey(
        'TestInstance',
        on_delete=models.CASCADE,
        related_name="snapshots",
        db_index=True,
        null=True,
        blank=True,
        help_text="The test instance this snapshot belongs to (null for old snapshots)"
    )
    export_id = models.CharField(max_length=50, unique=True, db_index=True, null=True, blank=True, help_text="Unique export identifier (e.g., export_1, export_2)")
    
    def save(self, *args, **kwargs):
        # Auto-generate export_id if not provided
        if not self.export_id:
            last_snapshot = TestExecutionSnapshot.objects.exclude(pk=self.pk).order_by('-exported_at').first()
            if last_snapshot and last_snapshot.export_id:
                try:
                    last_num = int(last_snapshot.export_id.split('_')[-1])
                    self.export_id = f"export_{last_num + 1}"
                except (ValueError, IndexError):
                    self.export_id = f"export_{TestExecutionSnapshot.objects.exclude(pk=self.pk).count() + 1}"
            else:
                self.export_id = f"export_{TestExecutionSnapshot.objects.exclude(pk=self.pk).count() + 1}"
        super().save(*args, **kwargs)
    snapshot_name = models.CharField(max_length=200, db_index=True, help_text="Name/version of this snapshot")
    sheet_name = models.CharField(max_length=100, db_index=True, blank=True)
    sw_part_number = models.CharField(max_length=200, db_index=True, blank=True)
    app_sw_version = models.CharField(max_length=100, db_index=True, blank=True)
    
    # Store execution data as JSON
    execution_data = models.JSONField(default=list, help_text="List of test execution records at time of export")
    
    # Metadata
    exported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='exported_snapshots')
    exported_at = models.DateTimeField(auto_now_add=True, db_index=True)
    total_test_cases = models.IntegerField(default=0) # type: ignore
    total_executed = models.IntegerField(default=0) # type: ignore
    total_passed = models.IntegerField(default=0) # type: ignore
    total_failed = models.IntegerField(default=0) # type: ignore
    total_not_executed = models.IntegerField(default=0) # type: ignore
    
    notes = models.TextField(blank=True, help_text="Optional notes about this export")
    
    class Meta:
        ordering = ['-exported_at']
        verbose_name = "Test Execution Snapshot"
        verbose_name_plural = "Test Execution Snapshots"
        indexes = [
            models.Index(fields=['sheet_name', 'sw_part_number', 'app_sw_version']),
            models.Index(fields=['-exported_at']),
            models.Index(fields=['export_id']),
        ]
    
    def __str__(self):
        return f"{self.export_id} - {self.snapshot_name} - {self.exported_at.strftime('%Y-%m-%d %H:%M')}"


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('Tester', 'Tester'),
        ('Test Engineer', 'Test Engineer'),
        ('Developer', 'Developer'),
        ('Manager', 'Manager'),
    ]
    

    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    employee_id = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Employee ID")
    full_name = models.CharField(max_length=200, verbose_name="Full Name")
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, verbose_name="Role")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['full_name']
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"
    
    def __str__(self):
        return f"{self.full_name} ({self.employee_id})"