from django.contrib import admin

from .models import WalletTransaction


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    """Read-only: ledger entries are only created by services."""

    list_display = ("created_at", "member", "transaction_type", "amount", "reference")
    list_filter = ("transaction_type",)
    search_fields = ("member__member_id", "reference")
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
