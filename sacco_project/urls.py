from django.urls import include, path

handler403 = "core.views.error_403"
handler404 = "core.views.error_404"
handler500 = "core.views.error_500"

urlpatterns = [
    path("", include("core.urls")),
    path("", include("accounts_core.urls")),
    path("members/", include("members.urls")),
    path("savings/", include("savings.urls")),
    path("loans/", include("loans.urls")),
    path("cashier/", include("cashier.urls")),
    path("accounting/", include("accounting.urls")),
    path("approvals/", include("approvals.urls")),
    path("reports/", include("reports.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("integrations/", include("integrations.urls")),
    path("portal/", include("selfservice.urls")),
    path("channels/", include("selfservice.staff_urls")),
    path("notifications/", include("notifications.urls")),
]
