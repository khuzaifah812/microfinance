from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("session/keepalive/", views.keepalive, name="keepalive"),
    path("account/password/", views.password_change, name="password_change"),
    path("account/verify-email/", views.verify_email, name="verify_email"),
    path("2fa/setup/", views.twofa_setup, name="2fa_setup"),
    path("2fa/verify/", views.twofa_verify, name="2fa_verify"),
    path("users/", views.user_list, name="users"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/<str:action>/", views.user_action, name="user_action"),
    path("security/logins/", views.login_history, name="logins"),
    path("audit/", views.audit_list, name="audit"),
    path("audit/verify/", views.audit_verify, name="audit_verify"),
]
