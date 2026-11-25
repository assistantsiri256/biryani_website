from django.contrib import admin

from .models import Order, OrderItem, PaymentTransaction


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "unit_price", "unit_volume", "quantity")
    can_delete = False


class PaymentInline(admin.TabularInline):
    model = PaymentTransaction
    extra = 0
    readonly_fields = ("gateway", "gateway_reference", "amount", "status", "created_at")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "total_amount", "total_volume", "status", "commission_processed", "created_at")
    list_filter = ("status", "commission_processed")
    search_fields = ("order_number", "customer__member_id")
    readonly_fields = ("order_number", "customer", "total_amount", "total_volume", "status", "paid_at", "commission_processed")
    inlines = [OrderItemInline, PaymentInline]
