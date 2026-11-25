from django.contrib import admin

from .models import SupportTicket


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "member", "category", "subject", "status")
    list_filter = ("status", "category")
    search_fields = ("member__member_id", "subject", "message")
