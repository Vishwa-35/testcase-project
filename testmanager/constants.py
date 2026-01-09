"""
Constants for Test Case Management System

RISK REMOVAL: Centralizes all hardcoded values to prevent fragile configurations.
Future changes to Excel format, status values, or paths can be made in one place.
"""

# =====================================================
# EXCEL IMPORT/EXPORT CONFIGURATION
# =====================================================
# RISK REMOVAL: Excel row numbers were hardcoded in multiple places (views.py, excel_export.py, utils.py)
# Making them configurable allows Excel format changes without code modifications
EXCEL_HEADER_ROW = 7  # Row where column headers are located
EXCEL_DATA_START_ROW = 8  # First row containing data (after headers)
EXCEL_SHEETS_TO_SKIP = 0  # Number of sheets to skip from the beginning (usually SUMMARY, LEGEND)

# Excel column positions for project overview (if reading from Excel file)
PROJECT_OVERVIEW_START_ROW = 8
PROJECT_OVERVIEW_LABEL_COL = "E"
PROJECT_OVERVIEW_VALUE_COL = "F"
PROJECT_OVERVIEW_MAX_EMPTY_ROWS = 5  # Stop reading after this many empty rows

# =====================================================
# TEST EXECUTION STATUS VALUES
# =====================================================
# RISK REMOVAL: Status strings were hardcoded throughout codebase (views, templates, exports)
# Centralizing ensures consistency and makes status changes easier
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_EXECUTED = "NOT EXECUTED"
STATUS_NOT_RELEVANT = "NOT RELEVANT"
STATUS_NA = "NA"
STATUS_OTHER = "OTHER"

# Status aliases (for flexible matching)
STATUS_NOT_EXECUTED_ALIASES = (STATUS_NOT_EXECUTED, "NOT_EXECUTED", STATUS_NA, "")

# All valid status values
VALID_STATUSES = (
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_NOT_EXECUTED,
    STATUS_NOT_RELEVANT,
    STATUS_OTHER,
)

# Status display order for charts/reports
STATUS_DISPLAY_ORDER = (STATUS_PASS, STATUS_FAIL, STATUS_NOT_EXECUTED, STATUS_OTHER)

# =====================================================
# FILE PATHS & CONFIGURATION
# =====================================================
# RISK REMOVAL: File paths were hardcoded (debug log, project overview Excel)
# Using settings.BASE_DIR ensures portability across environments
import os
from django.conf import settings

# Project overview Excel file path (if using file-based approach)
PROJECT_OVERVIEW_EXCEL_PATH = os.path.join(
    settings.BASE_DIR, "data", "project_overview.xlsx"
)

# =====================================================
# BULK OPERATION CONFIGURATION
# =====================================================
# RISK REMOVAL: Batch size was hardcoded in bulk_create_in_batches
# Making it configurable allows tuning for different database backends
BULK_CREATE_BATCH_SIZE = 500  # Number of records to create per batch

# =====================================================
# PAGINATION CONFIGURATION
# =====================================================
# RISK REMOVAL: Pagination size was hardcoded in multiple views
ITEMS_PER_PAGE = 50  # Default items per page in list views
ADMIN_ITEMS_PER_PAGE = 50  # Items per page in admin interface

# =====================================================
# VERSION HANDLING
# =====================================================
# RISK REMOVAL: Version parsing logic was duplicated across multiple functions
# These constants define version format expectations
VERSION_PREFIX = "V"  # Optional prefix in version strings (e.g., "V2.1")
VERSION_SEPARATOR = "."  # Separator in version numbers (e.g., "2.1.0")
VERSION_MAX_PARTS = 3  # Maximum version parts (major.minor.patch)

# Default sort order for versions (newest first = descending major, ascending minor/patch)
VERSION_SORT_DESCENDING_MAJOR = True

# =====================================================
# TEST CASE ID GENERATION
# =====================================================
# RISK REMOVAL: NOID prefix was hardcoded in import logic
NOID_PREFIX = "NOID_"  # Prefix for auto-generated test case IDs when missing

# =====================================================
# PROJECT OVERVIEW KEYS
# =====================================================
# RISK REMOVAL: Project overview keys were hardcoded as strings throughout code
# Centralizing prevents typos and makes key changes easier
PROJECT_OVERVIEW_KEY_LAST_EXPORT_TIMESTAMP = "last_export_timestamp"

# =====================================================
# USER ROLES
# =====================================================
# RISK REMOVAL: Role names were hardcoded in multiple permission checks
# Centralizing ensures consistency across role-based access control
ROLE_MANAGER = "Manager"
ROLE_DEVELOPER = "Developer"
ROLE_TEST_ENGINEER = "Test Engineer"
ROLE_TESTER = "Tester"

# All valid roles
VALID_ROLES = (ROLE_MANAGER, ROLE_DEVELOPER, ROLE_TEST_ENGINEER, ROLE_TESTER)

# =====================================================
# ACTIVITY LOG ACTIONS
# =====================================================
# RISK REMOVAL: Action strings were hardcoded in ActivityLog model and views
# Centralizing prevents inconsistencies
ACTIVITY_ACTION_ADD = "ADD"
ACTIVITY_ACTION_EDIT = "EDIT"
ACTIVITY_ACTION_IMPORT = "IMPORT"
ACTIVITY_ACTION_LOGOUT = "LOGOUT"
# Note: DELETE action is intentionally not included (commented out in model)

# =====================================================
# EXPORT CONFIGURATION
# =====================================================
# Excel export column width
EXCEL_COLUMN_WIDTH = 38

# Excel sheet names
EXCEL_SHEET_SUMMARY = "SUMMARY"
EXCEL_SHEET_LEGEND = "LEGEND"

# Maximum file upload size (15 MB)
MAX_UPLOAD_SIZE_MB = 15
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

