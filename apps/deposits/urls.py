from django.urls import path

from . import views

app_name = "deposits"

urlpatterns = [
    path("", views.deposit, name="home"),
    path("pay/<str:order_number>/", views.pay_order, name="pay_order"),
    path("razorpay/start/", views.razorpay_start, name="razorpay_start"),
    path("razorpay/callback/", views.razorpay_callback, name="razorpay_callback"),
    path("admin/", views.admin_deposits, name="admin"),
    path("admin/<int:pk>/action/", views.admin_action, name="admin_action"),
]
