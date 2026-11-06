from django.urls import path

from . import views

app_name = "accounting"
urlpatterns = [
    path("coa/", views.coa, name="coa"),
    path("coa/new/", views.coa_edit, name="coa_new"),
    path("coa/<int:pk>/edit/", views.coa_edit, name="coa_edit"),
    path("journals/", views.journals, name="journals"),
    path("journals/new/", views.journal_new, name="journal_new"),
    path("journals/<int:pk>/", views.journal_detail, name="journal"),
    path("journals/<int:pk>/reverse/", views.journal_reverse, name="journal_reverse"),
    path("ledger/", views.ledger, name="ledger"),
    path("vouchers/", views.voucher_list, name="vouchers"),
    path("vouchers/new/", views.voucher_new, name="voucher_new"),
    path("vouchers/<int:pk>/settle/", views.voucher_settle, name="voucher_settle"),
    path("banks/", views.banks, name="banks"),
    path("banks/new/", views.bank_new, name="bank_new"),
    path("banks/<int:pk>/reconcile/", views.reconcile, name="reconcile"),
    path("provision/", views.provision, name="provision"),
]
