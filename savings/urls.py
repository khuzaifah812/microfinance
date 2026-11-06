from django.urls import path

from . import views

app_name = "savings"
urlpatterns = [
    path("", views.account_list, name="list"),
    path("open/", views.account_open, name="open"),
    path("interest/", views.interest_run, name="interest"),
    path("receipt/<str:ref>/", views.receipt, name="receipt"),
    path("<int:pk>/", views.account_detail, name="detail"),
    path("<int:pk>/statement/", views.statement, name="statement"),
    path("<int:pk>/deposit/", views.deposit, name="deposit"),
    path("<int:pk>/withdraw/", views.withdraw, name="withdraw"),
    path("<int:pk>/transfer/", views.transfer, name="transfer"),
    path("<int:pk>/status/", views.set_status, name="status"),
]
