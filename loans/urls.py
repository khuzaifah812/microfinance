from django.urls import path

from . import views

app_name = "loans"
urlpatterns = [
    path("", views.loan_list, name="list"),
    path("products/", views.product_list, name="products"),
    path("products/new/", views.product_edit, name="product_new"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("apply/", views.loan_apply, name="apply"),
    path("preview/", views.preview, name="preview"),
    path("calculator/", views.calculator, name="calculator"),
    path("arrears/", views.arrears, name="arrears"),
    path("penalties/", views.run_penalties, name="penalties"),
    path("<int:pk>/", views.loan_detail, name="detail"),
    path("<int:pk>/guarantor/", views.add_guarantor, name="guarantor"),
    path("<int:pk>/decide/", views.decide, name="decide"),
    path("<int:pk>/disburse/", views.disburse, name="disburse"),
    path("<int:pk>/repay/", views.repay, name="repay"),
    path("<int:pk>/settle/", views.settle, name="settle"),
    path("<int:pk>/reschedule/", views.reschedule, name="reschedule"),
    path("<int:pk>/statement/", views.statement, name="statement"),
    path("<int:pk>/certificate/", views.certificate, name="certificate"),
]
