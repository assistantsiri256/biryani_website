from django.contrib import admin

from .models import Product, ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "product_type", "category", "price", "sales_volume", "stock", "is_active")
    list_filter = ("product_type", "category", "is_active")
    list_editable = ("price", "sales_volume", "stock", "is_active")
    search_fields = ("name", "sku")
