from django.urls import path

from . import views

app_name = "network"

urlpatterns = [
    path("tree/", views.tree, name="tree"),
    path("direct/", views.direct_referrals, name="direct"),
    path("team/", views.my_team, name="team"),
    path("api/tree/", views.tree_api, name="tree_api"),
]
