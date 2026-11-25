from django.contrib import admin

from .models import WithdrawalRequest


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    """Approve/reject from the BinaryPro admin panel so wallet entries stay consistent."""

    list_display = ("id", "member", "amount", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("member__member_id",)
    readonly_fields = [f.name for f in WithdrawalRequest._meta.fields if f.name != "payout_snapshot"]
    exclude = ("payout_snapshot",)

    def has_add_permission(self, request):
        return False
