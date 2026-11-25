from decimal import Decimal

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class ProductCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        verbose_name_plural = "Product categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(models.Model):
    PHYSICAL, DIGITAL = "physical", "digital"
    TYPES = [(PHYSICAL, "Physical product"), (DIGITAL, "Digital product / service")]

    product_type = models.CharField(max_length=10, choices=TYPES, default=PHYSICAL)
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    sku = models.CharField(max_length=40, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # Business volume credited to the binary legs when this product is sold.
    sales_volume = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    stock = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to="products/", blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(f"{self.name}-{self.sku}")
        super().save(*args, **kwargs)

    @property
    def is_digital(self):
        return self.product_type == self.DIGITAL

    @property
    def in_stock(self):
        return self.is_digital or self.stock > 0

    @property
    def max_quantity(self):
        return 99 if self.is_digital else self.stock

    def get_absolute_url(self):
        return reverse("products:detail", args=[self.slug])

    def __str__(self):
        return self.name
