from django.urls import path

from . import views

app_name = "members"
urlpatterns = [
    path("", views.member_list, name="list"),
    path("new/", views.member_create, name="create"),
    path("<int:pk>/", views.member_detail, name="detail"),
    path("<int:pk>/edit/", views.member_edit, name="edit"),
    path("<int:pk>/status/", views.member_status, name="status"),
    path("<int:pk>/documents/new/", views.document_upload, name="doc_upload"),
    path("documents/<int:pk>/", views.document_download, name="doc_download"),
    path("<int:pk>/photo/", views.member_photo, name="photo"),
    path("<int:pk>/statement/", views.member_statement, name="statement"),
    path("<int:pk>/export/", views.member_export, name="export"),
]
