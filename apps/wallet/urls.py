from django.urls import path

from . import views

app_name = "wallet"

urlpatterns = [
    path("", views.wallet, name="home"),
    path("income/", views.income_history, name="income"),
    path("transactions/", views.transactions, name="transactions"),
]
