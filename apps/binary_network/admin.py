from django.contrib import admin

from .models import BinaryNode, BinaryVolume, BinaryVolumeEntry


@admin.register(BinaryNode)
class BinaryNodeAdmin(admin.ModelAdmin):
    list_display = ("member", "parent", "position", "depth", "created_at")
    search_fields = ("member__member_id",)
    readonly_fields = ("member", "parent", "position", "path", "depth", "created_at")

    def has_add_permission(self, request):
        return False  # placement must go through the placement service


@admin.register(BinaryVolume)
class BinaryVolumeAdmin(admin.ModelAdmin):
    list_display = ("member", "left_count", "right_count", "left_carry", "right_carry", "total_matched")
    search_fields = ("member__member_id",)
    readonly_fields = [f.name for f in BinaryVolume._meta.fields]


@admin.register(BinaryVolumeEntry)
class BinaryVolumeEntryAdmin(admin.ModelAdmin):
    list_display = ("member", "order", "leg", "volume", "reversed", "created_at")
    search_fields = ("member__member_id", "order__order_number")
    readonly_fields = [f.name for f in BinaryVolumeEntry._meta.fields]
