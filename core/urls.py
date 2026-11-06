from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("healthz", views.healthz, name="healthz"),
    path("branding/logo/", views.branding_logo, name="logo"),
    path("search/", views.search, name="search"),
    path("settings/", views.settings_view, name="settings"),
]
