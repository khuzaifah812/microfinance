from django.urls import path

from . import views

app_name = "integrations"
urlpatterns = [
    path("", views.home, name="home"),
    path("sms/dispatch/", views.sms_dispatch, name="sms_dispatch"),
    path("match/<int:pk>/", views.match, name="match"),
    path("webhook/<str:provider>/", views.webhook, name="webhook"),
]
