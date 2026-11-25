from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="reports:dashboard"), name="home"),
    path("django-admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("dashboard/", include("apps.reports.urls")),
    path("network/", include("apps.binary_network.urls")),
    path("shop/", include("apps.products.urls")),
    path("orders/", include("apps.orders.urls")),
    path("wallet/", include("apps.wallet.urls")),
    path("withdrawals/", include("apps.withdrawals.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("support/", include("apps.support.urls")),
    path("deposit/", include("apps.deposits.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
