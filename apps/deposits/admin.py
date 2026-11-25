from django.contrib import admin

from .models import DepositRequest, TopupTransaction


@admin.register(DepositRequest)
class DepositRequestAdmin(admin.ModelAdmin):
    """Read-only here: approve/reject from the Deposits admin page so the wallet is credited."""

    list_display = ("id", "created_at", "member", "amount", "utr", "status", "reviewed_by")
    list_filter = ("status",)
    search_fields = ("member__member_id", "utr")
    readonly_fields = [f.name for f in DepositRequest._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TopupTransaction)
class TopupTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "member", "transaction_type", "amount", "reference")
    list_filter = ("transaction_type",)
    search_fields = ("member__member_id", "reference")
    readonly_fields = [f.name for f in TopupTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
