from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "title", "is_read")
    list_filter = ("is_read",)
    search_fields = ("user__member_id", "title")
