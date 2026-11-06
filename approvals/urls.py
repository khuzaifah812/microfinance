from django.urls import path

from . import views

app_name = "approvals"
urlpatterns = [
    path("", views.queue, name="queue"),
    path("<int:pk>/decide/", views.decide, name="decide"),
    path("rules/", views.rules, name="rules"),
]
