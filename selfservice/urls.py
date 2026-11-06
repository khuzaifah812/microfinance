from django.urls import path

from . import views

app_name = "portal"
urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("pin/", views.pin, name="pin"),
    path("loan/<int:pk>/", views.loan_view, name="loan"),
    path("statement/<int:pk>/", views.statement, name="statement"),
]
