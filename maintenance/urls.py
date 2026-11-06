from django.urls import path

from . import views

app_name = "maintenance"
urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("modules/", views.module_list, name="modules"),
    path("modules/new/", views.module_edit, name="module_new"),
    path("modules/export/", views.module_export, name="module_export"),
    path("modules/import/", views.module_import, name="module_import"),
    path("modules/<int:pk>/", views.module_detail, name="module"),
    path("modules/<int:pk>/edit/", views.module_edit, name="module_edit"),
    path("modules/<int:pk>/<str:action>/", views.module_action, name="module_action"),
    path("m/<slug:key>/", views.module_page, name="module_page"),
    path("notices/", views.notices, name="notices"),
    path("activity/", views.activity, name="activity"),
    path("backups/", views.backups, name="backups"),
    path("backups/create/", views.backup_create, name="backup_create"),
    path("backups/<int:pk>/download/", views.backup_download, name="backup_download"),
    path("backups/<int:pk>/verify/", views.backup_verify, name="backup_verify"),
    path("failures/", views.failures, name="failures"),
    path("docs/", views.docs, name="docs"),
    path("docs/new/", views.doc_edit, name="doc_new"),
    path("docs/<int:pk>/", views.doc_view, name="doc"),
    path("docs/<int:pk>/edit/", views.doc_edit, name="doc_edit"),
    path("demo-reset/", views.demo_reset, name="demo_reset"),
]
