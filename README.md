# Test Case Management System

A comprehensive Django-based test case management system designed for managing test cases, test executions, versions, and exports with role-based access control.

## Overview

This system provides a complete solution for:
- Managing test cases with version control
- Tracking test executions and results
- Managing multiple test instances/cycles
- Exporting test data to Excel and HTML formats
- Role-based user management (Tester, Test Engineer, Developer, Manager)
- Project overview and metadata management
- Historical snapshots and audit trails

## Features

### Core Functionality
- **Test Case Management**: Create, edit, and manage test cases with versioning support
- **Test Execution Tracking**: Record test execution results with status, reports, and comments
- **Version Control**: Manage multiple software versions per SW Part Number
- **Test Instances**: Support for multiple test cycles/instances with only one active at a time
- **Excel Import/Export**: Import test cases from Excel files and export execution results
- **HTML Export**: Generate HTML reports with snapshot functionality for historical records
- **Project Overview**: Manage project metadata including VCU platform, hardware part numbers, developers, etc.

### User Management
- **Role-Based Access Control**: 
  - Tester
  - Test Engineer
  - Developer
  - Manager
- **User Profiles**: Employee ID, full name, and role management
- **Manager Approval**: Managers can approve test executions and lock versions

### Advanced Features
- **Version Locking**: Lock approved versions to prevent modifications
- **Execution Status Tracking**: Track execution status (Not Started, In Progress, Completed, Approved)
- **Activity Logging**: Comprehensive audit trail of all user actions
- **Snapshot System**: Create historical snapshots of test executions for repeatable testing cycles
- **Feature Completion Tracking**: Monitor completion status of features
- **Inline Editing**: Edit test cases directly from the list view

## Technology Stack

- **Backend**: Django 5.2.x
- **Database**: SQLite (default, can be configured for PostgreSQL/MySQL)
- **Excel Handling**: openpyxl, pandas
- **Frontend**: HTML, CSS, JavaScript
- **Python**: 3.x

## Installation

### Prerequisites
- Python 3.8 or higher
- pip (Python package manager)

### Setup Steps

1. **Clone or navigate to the project directory**
   ```bash
   cd testcase_project
   ```

2. **Create a virtual environment (recommended)**
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run migrations**
   ```bash
   python manage.py migrate
   ```

5. **Create a superuser (for admin access)**
   ```bash
   python manage.py createsuperuser
   ```

6. **Collect static files**
   ```bash
   python manage.py collectstatic
   ```

7. **Run the development server**
   ```bash
   python manage.py runserver
   ```

8. **Access the application**
   - Main application: http://127.0.0.1:8000/
   - Admin panel: http://127.0.0.1:8000/admin/

## Project Structure

```
testcase_project/
├── testcase_project/          # Main project settings
│   ├── settings.py            # Django settings
│   ├── urls.py                # Root URL configuration
│   ├── wsgi.py                # WSGI configuration
│   └── asgi.py                # ASGI configuration
├── testmanager/               # Main application
│   ├── models.py              # Database models
│   ├── views/                 # View modules
│   │   ├── admin_views.py     # Admin-related views
│   │   ├── export_views.py    # Export functionality
│   │   ├── home.py            # Home page views
│   │   ├── import_views.py    # Import functionality
│   │   └── testcases.py       # Test case views
│   ├── templates/             # HTML templates
│   ├── static/                # Static files (CSS, JS, images)
│   ├── services.py            # Business logic services
│   ├── excel_export.py        # Excel export utilities
│   ├── forms.py               # Django forms
│   ├── admin.py               # Django admin configuration
│   └── urls.py                # Application URL routing
├── manage.py                  # Django management script
├── requirements.txt           # Python dependencies
└── db.sqlite3                 # SQLite database (created after migrations)
```

## Key Models

### TestInstance
Represents a test instance cycle. Only one instance can be active at a time.

### TestCase
Stores test case definitions with versioning support. Includes fields for:
- Test case ID, summary, steps, expected results
- Requirements and feature information
- Software part numbers and versions

### TestCaseVersion
Manages versions for SW Part Numbers with execution status tracking:
- NOT_STARTED
- IN_PROGRESS
- COMPLETED
- APPROVED (locked and immutable)

### TestExecution
Tracks test execution results with:
- Execution status (Pass/Fail/Not Executed)
- Reports and comments
- Manager approval
- User and timestamp information

### ProjectOverview
Stores project metadata including:
- Project code, VCU platform
- Hardware/Software part numbers
- Developer and test engineer information
- Bootloader versions, checksums
- DBC test_it references

### UserProfile
Extends Django User model with:
- Employee ID
- Full name
- Role (Tester, Test Engineer, Developer, Manager)

## Usage Guide

### Creating a New Test Instance
1. Navigate to "Create New Test Instance"
2. A new test instance cycle will be created and set as active
3. Previous instances will be automatically archived

### Importing Test Cases
1. Go to "Upload" page
2. Select an Excel file with test cases
3. Choose the sheet to import
4. Enter software versions for each SW Part Number
5. Import the test cases

### Executing Tests
1. Navigate to the test case list
2. Click on a test case to execute
3. Fill in execution details:
   - Status (Pass/Fail/Not Executed)
   - Reports
   - Comments
4. Save the execution

### Exporting Results
1. Go to "Export" page
2. Select versions to export
3. Choose export format (Excel or HTML)
4. Download the generated file

### Manager Approval
1. Managers can approve individual test executions
2. Once all tests are completed, managers can approve the entire version
3. Approved versions are locked and cannot be modified

## Configuration

### Database Configuration
By default, the project uses SQLite. To use PostgreSQL or MySQL, update `settings.py`:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'your_database_name',
        'USER': 'your_username',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### Allowed Hosts
Update `ALLOWED_HOSTS` in `settings.py` for production deployment:

```python
ALLOWED_HOSTS = ['your-domain.com', 'your-ip-address']
```

### Static Files
For production, configure static file serving using a web server (Nginx, Apache) or use Django's `whitenoise` package.

## Development

### Running Tests
```bash
python manage.py test
```

### Creating Migrations
After modifying models:
```bash
python manage.py makemigrations
python manage.py migrate
```

### Accessing Django Admin
1. Create a superuser: `python manage.py createsuperuser`
2. Access admin at: http://127.0.0.1:8000/admin/
3. The system uses a custom admin site with enhanced functionality

## Security Considerations

- **SECRET_KEY**: Change the `SECRET_KEY` in `settings.py` for production
- **DEBUG**: Set `DEBUG = False` in production
- **CSRF Protection**: Enabled by default
- **Authentication**: Django's built-in authentication system
- **Role-Based Permissions**: Custom decorators for role-based access control

## API Endpoints

### Main Routes
- `/` - Home page
- `/testcases/` - Test case list
- `/upload/` - Excel upload
- `/export/` - Export functionality
- `/admin-page/` - Admin user management
- `/history/` - Execution history
- `/instruction/` - User instructions

### API Endpoints
- `/api/feature-completion-status/` - Get feature completion status
- `/export/versions/` - Get exportable versions
- `/project-overview/get/` - Get project overview
- `/project-overview/save/` - Save project overview

## Troubleshooting

### Common Issues

1. **Migration Errors**
   - Ensure all dependencies are installed
   - Run `python manage.py migrate` again

2. **Static Files Not Loading**
   - Run `python manage.py collectstatic`
   - Check `STATIC_URL` and `STATIC_ROOT` in settings

3. **Permission Errors**
   - Ensure user has appropriate role assigned
   - Check user profile exists in database

4. **Excel Import Errors**
   - Verify Excel file format matches expected structure
   - Check sheet names and column headers

## Contributing

1. Follow Django coding standards
2. Write clear commit messages
3. Test changes thoroughly
4. Update documentation as needed

## License

This project is proprietary software for TVS Motor Company Ltd.

## Support

For issues or questions, contact the development team or refer to the instruction page within the application.

## Version History

- **Current Version**: Django 5.2.x
- **Database Schema**: Supports versioning, test instances, and execution tracking
- **Export Formats**: Excel (.xlsx) and HTML

---

**Note**: This is an internal project for TVS Motor Company Ltd. Ensure proper access controls and security measures are in place before deployment.

