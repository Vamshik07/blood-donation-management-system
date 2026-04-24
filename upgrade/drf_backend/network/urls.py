from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("users/register", views.register_user, name="register-user"),
    path("requests", views.create_blood_request, name="create-request"),
    path("requests/pending", views.pending_requests, name="pending-requests"),
    path("requests/<int:request_id>/approve", views.approve_request, name="approve-request"),
    path("requests/<int:request_id>/respond", views.donor_respond, name="respond-request"),
    path("map/donors", views.donor_match_map, name="donor-match-map"),
    path("camps", views.create_camp, name="create-camp"),
    path("camps/<int:camp_id>/approve", views.approve_camp, name="approve-camp"),
    path("inventory", views.inventory, name="inventory"),
    path("notifications/<int:user_id>", views.notifications, name="notifications"),
    path("counters", views.impact_counters, name="impact-counters"),
]
