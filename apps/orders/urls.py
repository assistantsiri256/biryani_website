from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("cart/", views.cart, name="cart"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("checkout/", views.checkout, name="checkout"),
    path("webhooks/razorpay/", views.razorpay_webhook, name="razorpay_webhook"),
    path("<str:order_number>/", views.order_detail, name="detail"),
    path("<str:order_number>/pay/", views.pay, name="pay"),
    path("<str:order_number>/pay/razorpay/", views.razorpay_start, name="razorpay_start"),
    path("<str:order_number>/pay/razorpay/callback/", views.razorpay_callback, name="razorpay_callback"),
    path("<str:order_number>/pay/upi/", views.upi_submit, name="upi_submit"),
    path("<str:order_number>/cancel/", views.cancel, name="cancel"),
]
