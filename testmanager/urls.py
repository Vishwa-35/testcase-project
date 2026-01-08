# testmanager/urls.py
from django.urls import path
from . import views
from .views import custom_logout

urlpatterns = [
    path('', views.home, name='home'),
    path("login/redirect/", views.post_login_redirect, name="post_login_redirect"),
    path("project-overview/get/", views.get_project_overview, name="get_project_overview"),
    path("project-overview/save/", views.save_project_overview, name="save_project_overview"),
    path('testcases/', views.testcase_list, name='testcase_list'),
    path("create-testcases/", views.create_testcases, name="create_testcases"),
    path("create-new-version/", views.create_new_version, name="create_new_version"),
    path('testcases/add/', views.testcase_add, name='testcase_add'),
    path('testcases/<int:id>/', views.testcase_test_it, name='testcase_test_it'),
    path('view/<int:id>/', views.view_test_execution, name='view_test_execution'),
    path('upload/', views.upload_excel, name='upload_excel'),
    path('upload/input-versions/', views.input_versions, name='input_versions'),
    path('upload/import/', views.import_excel, name='import_excel'),
    path("export/versions/", views.get_exportable_versions_api, name="get_exportable_versions_api"),
    path("export/", views.export_excel, name="export_excel"),
    path("export/html/", views.export_html, name="export_html"),
    path("export/html/<str:export_id>/", views.export_html_snapshot, name="export_html_snapshot"),
    path("reset-execution-data/", views.reset_execution_data, name="reset_execution_data"),
    path("create-new-test-instance/", views.create_new_test_instance, name="create_new_test_instance"),
    path("api/feature-completion-status/", views.get_feature_completion_status_api, name="get_feature_completion_status_api"),
    path("toggle-tested-status/", views.toggle_tested_status, name="toggle_tested_status"),
    path("logout/", custom_logout, name="custom_logout"),
    path("project-overview/update/", views.update_project_overview, name="update_project_overview"),
    path("history/", views.history, name="history"),
    path("admin-page/", views.admin_page, name="admin_page"),
    path("admin-page/create-user/", views.create_user, name="create_user"),
    path("instruction/", views.instruction_page, name="instruction"),
]
