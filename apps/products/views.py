from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Product, ProductCategory


@login_required
def product_list(request):
    products = Product.objects.filter(is_active=True).select_related("category")
    cat = request.GET.get("category")
    q = request.GET.get("q", "").strip()
    if cat:
        products = products.filter(category__slug=cat)
    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))
    return render(
        request,
        "products/list.html",
        {"products": products, "categories": ProductCategory.objects.all(), "active_cat": cat, "q": q},
    )


@login_required
def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    return render(request, "products/detail.html", {"product": product})
