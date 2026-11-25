from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.members.models import MemberProfile, Referral

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("member_id", "username", "first_name", "mobile", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "is_verified")
    search_fields = ("member_id", "username", "email", "mobile", "first_name", "last_name")
    readonly_fields = ("member_id", "created_at")
    fieldsets = BaseUserAdmin.fieldsets + (("BinaryPro", {"fields": ("member_id", "mobile", "role", "is_verified", "created_at")}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("BinaryPro", {"fields": ("mobile", "role", "email")}),)


@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "city", "kyc_status")
    list_filter = ("kyc_status",)
    search_fields = ("user__member_id", "user__username")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("member", "sponsor", "created_at")
    search_fields = ("member__member_id", "sponsor__member_id")
    raw_id_fields = ("member", "sponsor")
