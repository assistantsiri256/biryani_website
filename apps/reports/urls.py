from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("admin/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/members/", views.admin_members, name="admin_members"),
    path("admin/members/<str:member_id>/toggle/", views.admin_member_toggle, name="admin_member_toggle"),
    path("admin/orders/", views.admin_orders, name="admin_orders"),
    path("admin/orders/<str:order_number>/action/", views.admin_order_action, name="admin_order_action"),
    path("admin/commissions/", views.admin_commissions, name="admin_commissions"),
    path("admin/commissions/release/", views.admin_release_commissions, name="admin_release_commissions"),
    path("admin/withdrawals/", views.admin_withdrawals, name="admin_withdrawals"),
    path("admin/withdrawals/<int:pk>/action/", views.admin_withdrawal_action, name="admin_withdrawal_action"),
    path("admin/reports/", views.admin_reports, name="admin_reports"),
    path("admin/placements/", views.admin_placements, name="admin_placements"),
]
