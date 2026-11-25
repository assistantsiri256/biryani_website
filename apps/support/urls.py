from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.support, name="home"),
    path("admin/", views.admin_tickets, name="admin"),
    path("admin/<int:pk>/reply/", views.admin_reply, name="admin_reply"),
]
