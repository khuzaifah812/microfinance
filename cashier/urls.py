from django.urls import path

from . import views

app_name = "cashier"
urlpatterns = [
    path("", views.home, name="home"),
    path("open/", views.open_till, name="open"),
    path("close/", views.close_till, name="close"),
    path("tills/", views.till_list, name="tills"),
    path("till/<int:pk>/", views.till_detail, name="till"),
    path("transfer/", views.transfer, name="transfer"),
    path("report/", views.daily_report, name="report"),
]
