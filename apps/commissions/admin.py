from django.contrib import admin

from .engine import release_commissions
from .models import Commission, CommissionRule


@admin.register(CommissionRule)
class CommissionRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "placement_mode", "binary_match_rate", "direct_sale_rate", "daily_binary_cap", "min_withdrawal", "is_active")

    def save_model(self, request, obj, form, change):
        from apps.core.utils import audit

        super().save_model(request, obj, form, change)
        audit(request.user, "plan.update", obj, **{f: form.cleaned_data.get(f) for f in form.changed_data})


@admin.action(description="Release selected pending commissions to wallets")
def release_selected(modeladmin, request, queryset):
    n = release_commissions(queryset, actor=request.user)
    modeladmin.message_user(request, f"{n} commission(s) released.")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "member", "commission_type", "volume", "rate", "amount", "capped", "status")
    list_filter = ("commission_type", "status", "capped")
    search_fields = ("member__member_id", "order__order_number")
    readonly_fields = [f.name for f in Commission._meta.fields]
    actions = [release_selected]

    def has_add_permission(self, request):
        return False
