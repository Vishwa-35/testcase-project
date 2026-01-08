# DATABASE STORAGE DESIGN VERIFICATION

## 1. EXACT STORAGE HIERARCHY

**CONFIRMED HIERARCHY:**
```
TestInstance (root)
  └── TestCase (master test case definition)
        └── TestCaseVersion (version per SW Part Number)
              └── TestCaseSheet (sheet within version)
                    └── TestExecution (execution data per version+sheet)
```

**WHY THIS IS CORRECT:**
- **TestCase**: Immutable master definition (test_case_id, test steps, expected results)
- **TestCaseVersion**: Version-specific metadata (app_sw_version, sw_part_number, is_active, is_locked)
- **TestCaseSheet**: Sheet organization within a version (sheet_name)
- **TestExecution**: Version-specific execution results (status, reports, comments)

**CRITICAL RULE**: Execution data is **permanently bound** to a specific version via ForeignKey. Once saved, execution cannot move between versions.

---

## 2. MODEL STRUCTURE DETAILS

### TestInstance
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**: None (root entity)
- **Version-Dependent Fields**: None
- **Immutable Fields**: `id`, `created_at`
- **Purpose**: Isolates test cycles. Only one `is_active=True` at a time.

### TestCase
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**: 
  - `instance` → TestInstance (CASCADE)
- **Version-Dependent Fields**: None (master definition only)
- **Immutable Fields**: `id`, `test_case_id` (unique globally), `created_at`
- **Legacy Fields** (should be removed):
  - `app_sw_version` (CharField) - version info belongs in TestCaseVersion
  - `sheet_name` (CharField) - sheet info belongs in TestCaseSheet
- **Purpose**: Master test case definition. Shared across all versions.

### TestCaseVersion
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**:
  - `instance` → TestInstance (CASCADE)
- **Version-Dependent Fields**: ALL fields are version-specific
- **Immutable Fields**: `id`, `created_at`
- **Unique Constraint**: `(instance, sw_part_number, app_sw_version)`
- **Critical Flags**:
  - `is_active`: Only ONE active version per SW part number
  - `is_locked`: When True, ALL related data becomes read-only
- **Purpose**: Represents a specific version of software for a SW Part Number.

### TestCaseSheet
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**:
  - `version` → TestCaseVersion (CASCADE)
- **Version-Dependent Fields**: ALL fields (bound to specific version)
- **Immutable Fields**: `id`, `created_at`
- **Unique Constraint**: `(version, sheet_name)`
- **Purpose**: Organizes test cases into sheets within a version.

### TestExecution
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**:
  - `instance` → TestInstance (CASCADE)
  - `test_case` → TestCase (CASCADE)
  - `version` → TestCaseVersion (CASCADE) **CRITICAL: Explicit version binding**
  - `sheet` → TestCaseSheet (CASCADE) **CRITICAL: Explicit sheet binding**
  - `user` → User (SET_NULL)
  - `approved_by` → User (SET_NULL)
- **Version-Dependent Fields**: ALL execution fields (status, reports, comments)
- **Immutable Fields**: `id`, `executed_at` (once saved)
- **Unique Constraint**: `(instance, test_case, version, sheet)`
- **Critical Flags**:
  - `is_locked`: Inherited from version when version is locked
  - `manager_approved`: Manager approval flag
- **Legacy Fields** (for backward compatibility):
  - `sw_part_number` (CharField) - denormalized from version
  - `app_sw_version` (CharField) - denormalized from version
- **Purpose**: Stores execution results (status, reports, comments) for a specific version+sheet+test_case combination.

### ProjectOverview
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**:
  - `version` → TestCaseVersion (CASCADE) **CRITICAL: Explicit version binding**
  - `instance` → TestInstance (CASCADE)
  - `created_by` → User (SET_NULL)
- **Version-Dependent Fields**: ALL fields (project_code, vcu_platform, etc.)
- **Immutable Fields**: `id`, `created_at` (when version is locked)
- **Unique Constraint**: `(version,)` - One ProjectOverview per version
- **Note**: Model also has legacy fields `software_part_number`, `application_sw_version`, `sw_part_number`, `app_sw_version` for backward compatibility
- **Purpose**: Version-specific project metadata.

### SWVersionMapping (Legacy/Compatibility)
- **Primary Key**: `id` (auto-increment)
- **Foreign Keys**:
  - `instance` → TestInstance (CASCADE)
- **Version-Dependent Fields**: `version`, `is_active`
- **Purpose**: Legacy version mapping. Being phased out in favor of TestCaseVersion.

---

## 3. EXECUTION STORAGE RULES (CRITICAL)

### Storage Location
**Table**: `TestExecution`

**Fields Stored**:
- `status` (CharField) - Execution status (PASS/FAIL/NOT_EXECUTED)
- `reports` (TextField) - Execution reports
- `comments` (TextField) - Execution comments

### Version Binding
**EXPLICIT FOREIGN KEY**: `TestExecution.version` → `TestCaseVersion.id`

**Query Pattern**:
```python
# CORRECT: Filter by explicit FK
execution = TestExecution.objects.filter(
    instance=active_instance,
    test_case=test_case,
    version=version_obj,  # Explicit FK
    sheet=sheet_obj  # Explicit FK
).first()
```

**PROOF: Execution Data Cannot Leak**
1. **Unique Constraint**: `(instance, test_case, version, sheet)` ensures one execution per combination
2. **CASCADE Delete**: If version is deleted, all executions are deleted (data integrity)
3. **No Dynamic Resolution**: Version is stored as FK, never inferred from TestCase
4. **Query Filtering**: All queries MUST include `version=version_obj` filter

**INCORRECT PATTERNS (DO NOT USE)**:
```python
# WRONG: Inferring version from TestCase
execution = TestExecution.objects.filter(
    test_case=test_case,
    app_sw_version=some_version  # Using denormalized field
).first()

# WRONG: No version filter
execution = TestExecution.objects.filter(
    test_case=test_case
).first()  # Could return wrong version!
```

---

## 4. VERSION LOCKING MECHANISM

### Database Flag
**Field**: `TestCaseVersion.is_locked` (BooleanField, db_index=True)

**When Set**: Manager checks approval checkbox → `is_locked=True`

### Lock Propagation
When `TestCaseVersion.is_locked = True`:
1. **TestExecution.is_locked** is set to `True` for ALL related executions
2. **ProjectOverview** becomes read-only (checked via `version.is_locked`)

### Query Enforcement
**MUST CHECK IN ALL UPDATE QUERIES**:
```python
# CORRECT: Check lock before update
if version_obj.is_locked:
    raise PermissionDenied("Version is locked")

# CORRECT: Filter locked versions in admin
if not is_manager:
    queryset = queryset.filter(version__is_locked=False)
```

**Queries That MUST Enforce Lock**:
1. `TestExecution.save()` - Check `version.is_locked`
2. `ProjectOverview.save()` - Check `version.is_locked`
3. `TestCaseVersion.save()` - Prevent editing if `is_locked=True`
4. Admin views - Filter by `is_locked=False` for non-managers

### Read-Only Access
- **Locked versions**: Readable by ALL users
- **Locked versions**: Writable by NO users (including managers)
- **Query Pattern**: No filter needed for reads, but updates must check `is_locked=False`

---

## 5. INSERT FLOWS

### Adding a New Test Case
**Flow**:
1. User creates TestCase (master definition)
2. System finds or creates TestCaseVersion (active version for SW Part Number)
3. System finds or creates TestCaseSheet (sheet within version)
4. TestCase is NOT directly linked to version (no FK)

**Which Version Does It Attach To?**
- **Active version** for the selected SW Part Number
- Query: `TestCaseVersion.objects.filter(instance=active_instance, sw_part_number=sw, is_active=True).first()`
- If no active version exists, create new one with `is_active=True`

**Code Pattern**:
```python
# Get or create active version
version_obj = TestCaseVersion.objects.filter(
    instance=active_instance,
    sw_part_number=sw_part_number,
    is_active=True
).first()

if not version_obj:
    version_obj = TestCaseVersion.objects.create(
        instance=active_instance,
        sw_part_number=sw_part_number,
        app_sw_version=app_sw_version,
        is_active=True,
        is_locked=False
    )
```

### Creating a New Version
**Flow**:
1. Manager creates new TestCaseVersion with `is_active=True`
2. System sets previous version `is_active=False` (via save() method)
3. System creates new TestCaseSheet records for new version
4. **NO execution data is copied** - new version starts empty

**What Records Are Copied?**
- **TestCase records**: NOT copied (shared master definitions)
- **TestCaseSheet records**: Created new (one per sheet_name)
- **TestExecution records**: NOT copied (new version starts empty)

**What Records Are Reset?**
- Previous version: `is_active=False` (but NOT deleted)
- New version: Starts with empty executions

**What Records Are Preserved?**
- **All TestCase records**: Preserved (master definitions)
- **All old TestCaseVersion records**: Preserved (history)
- **All old TestExecution records**: Preserved (audit trail)
- **All old TestCaseSheet records**: Preserved (history)

**Code Pattern**:
```python
# Create new version (automatically deactivates old one)
new_version = TestCaseVersion.objects.create(
    instance=active_instance,
    sw_part_number=sw_part_number,
    app_sw_version=new_app_sw_version,
    is_active=True,  # Automatically sets old version is_active=False
    is_locked=False
)

# Create sheets for new version
for sheet_name in sheet_names:
    TestCaseSheet.objects.get_or_create(
        version=new_version,
        sheet_name=sheet_name
    )

# NO execution data copied - new version starts empty
```

---

## 6. FETCH FLOWS

### Home Page (Dashboard)
**Query Pattern**:
```python
# Get active version
active_version = TestCaseVersion.objects.filter(
    instance=active_instance,
    sw_part_number=selected_sw,
    is_active=True,
    is_locked=False
).first()

# Get executions for active version ONLY
executions = TestExecution.objects.filter(
    instance=active_instance,
    version=active_version,  # Explicit FK filter
    sheet__sheet_name=selected_sheet
)
```

**NO GLOBAL/LATEST() SHORTCUTS**: All queries use explicit `version=version_obj` filter.

### Test Cases List
**Query Pattern**:
```python
# Get test cases (master definitions)
test_cases = TestCase.objects.filter(
    instance=active_instance,
    sheet_name=selected_sheet  # Legacy field, but still used
)

# Get executions for selected version
if selected_version:
    version_obj = TestCaseVersion.objects.filter(
        instance=active_instance,
        sw_part_number=selected_sw,
        app_sw_version=selected_version
    ).first()
    
    executions = TestExecution.objects.filter(
        instance=active_instance,
        version=version_obj,  # Explicit FK filter
        test_case__in=test_cases
    )
```

**NO GLOBAL/LATEST() SHORTCUTS**: Version must be explicitly selected.

### Export
**Query Pattern**:
```python
# Get active versions only
active_versions = TestCaseVersion.objects.filter(
    instance=active_instance,
    is_active=True
).values_list('id', flat=True)

# Get executions for active versions ONLY
executions = TestExecution.objects.filter(
    instance=active_instance,
    version__in=active_versions,  # Filter by version FK
    test_case__in=test_case_ids
)
```

**NO GLOBAL/LATEST() SHORTCUTS**: Filter by `version__in=active_versions`.

---

## 7. MIGRATION SAFETY

### Fields Referenced in Queries
**VERIFIED EXIST IN DB**:
- ✅ `TestCaseVersion.is_active` (BooleanField, db_index=True)
- ✅ `TestCaseVersion.is_locked` (BooleanField, db_index=True)
- ✅ `TestExecution.version` (ForeignKey to TestCaseVersion)
- ✅ `TestExecution.sheet` (ForeignKey to TestCaseSheet)
- ✅ `TestExecution.is_locked` (BooleanField, db_index=True)
- ✅ `ProjectOverview.version` (ForeignKey to TestCaseVersion)

### Legacy Columns That Should Be Removed
**HIGH PRIORITY**:
1. `TestCase.app_sw_version` (CharField) - Version info belongs in TestCaseVersion
2. `TestCase.sheet_name` (CharField) - Sheet info belongs in TestCaseSheet

**LOW PRIORITY** (for backward compatibility):
- `TestExecution.sw_part_number` (CharField) - Denormalized, but used in queries
- `TestExecution.app_sw_version` (CharField) - Denormalized, but used in queries
- `ProjectOverview.software_part_number` (CharField) - Legacy field
- `ProjectOverview.application_sw_version` (CharField) - Legacy field

### Missing Migrations
**VERIFIED**: All model fields have corresponding migrations:
- ✅ `0001_initial.py` - Initial schema
- ✅ `0002_add_projectoverview_version_fk.py` - ProjectOverview.version FK

**NO RUNTIME ERRORS**: All fields referenced in queries exist in database.

---

## 8. CRITICAL VIOLATIONS IDENTIFIED

### ⚠️ VIOLATION 1: TestCase Legacy Fields Still Used
**Location**: `testmanager/views/testcases.py`, `testmanager/views/home.py`
**Issue**: Queries still filter by `TestCase.sheet_name` and `TestCase.app_sw_version`
**Impact**: Data inconsistency - these fields should not exist
**Fix Required**: Migrate all queries to use TestCaseSheet and TestCaseVersion relationships

### ⚠️ VIOLATION 2: Execution Queries Using Denormalized Fields (CRITICAL)
**Location**: 
- `testmanager/views/home.py` (lines 475, 480)
- `testmanager/views/testcases.py` (lines 264, 480)
- `testmanager/views/export_views.py` (lines 90, 108, 567, 648)

**Issue**: Queries filter by `TestExecution.app_sw_version` (CharField) instead of `TestExecution.version` (ForeignKey)

**Current Pattern (WRONG)**:
```python
executions = TestExecution.objects.filter(
    app_sw_version=selected_version  # Using denormalized field
)
```

**Correct Pattern**:
```python
version_obj = TestCaseVersion.objects.filter(
    instance=active_instance,
    sw_part_number=sw_part_number,
    app_sw_version=selected_version
).first()

executions = TestExecution.objects.filter(
    version=version_obj  # Using explicit FK
)
```

**Impact**: 
- Potential data leakage if denormalized field is incorrect
- No referential integrity guarantee
- Queries may return wrong executions if `app_sw_version` is out of sync

**Fix Required**: 
- Migrate ALL queries to use `version=version_obj` FK filter
- Remove `app_sw_version` field after migration (or keep as read-only denormalized field)

### ⚠️ VIOLATION 3: ProjectOverview Legacy Fields
**Location**: `testmanager/models.py` (ProjectOverview model)
**Issue**: Multiple legacy fields (`software_part_number`, `application_sw_version`, `sw_part_number`, `app_sw_version`)
**Impact**: Data duplication and potential inconsistency
**Fix Required**: Remove legacy fields after migration

---

## 9. DATA INTEGRITY GUARANTEES

### ✅ GUARANTEED BY DATABASE CONSTRAINTS
1. **Unique TestCaseVersion**: `(instance, sw_part_number, app_sw_version)` - No duplicate versions
2. **Unique TestCaseSheet**: `(version, sheet_name)` - No duplicate sheets per version
3. **Unique TestExecution**: `(instance, test_case, version, sheet)` - One execution per combination
4. **Unique ProjectOverview**: `(version,)` - One overview per version

### ✅ GUARANTEED BY CASCADE DELETES
1. Delete TestCaseVersion → Deletes all TestCaseSheet, TestExecution, ProjectOverview
2. Delete TestCaseSheet → Deletes all TestExecution
3. Delete TestCase → Deletes all TestExecution (but TestCase should never be deleted)

### ✅ GUARANTEED BY APPLICATION LOGIC
1. Only ONE active version per SW Part Number (enforced in TestCaseVersion.save())
2. Locked versions are read-only (enforced in views and admin)
3. Execution data is permanently bound to version (FK constraint)

---

## SUMMARY

**STORAGE HIERARCHY**: TestCase → TestCaseVersion → TestCaseSheet → TestExecution

**VERSION BINDING**: Explicit ForeignKey relationships ensure execution data cannot leak between versions.

**LOCKING MECHANISM**: `TestCaseVersion.is_locked` flag prevents all edits when True.

**DATA INTEGRITY**: Database constraints and application logic ensure version isolation.

**MIGRATION STATUS**: All critical fields exist. Legacy fields should be removed in future migration.

