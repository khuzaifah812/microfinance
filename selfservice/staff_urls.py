from django.urls import path

from . import views

app_name = "channels"
urlpatterns = [
    path("portal/", views.portal_admin, name="portal_admin"),
    path("portal/issue/<int:pk>/", views.issue_pin, name="issue_pin"),
    path("portal/toggle/<int:pk>/", views.toggle_access, name="toggle_access"),
    path("ussd/", views.ussd_preview, name="ussd"),
]
