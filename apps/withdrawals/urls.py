from django.urls import path

from . import views

app_name = "withdrawals"

urlpatterns = [
    path("", views.withdrawals, name="list"),
    path("confirm/", views.confirm, name="confirm"),
    path("confirm/resend/", views.resend, name="resend"),
]
