from math import sqrt

from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import BloodCamp, BloodInventory, BloodRequest, DonorResponse, Notification, UserProfile
from .serializers import (
    BloodCampSerializer,
    BloodInventorySerializer,
    BloodRequestSerializer,
    DonorResponseSerializer,
    NotificationSerializer,
    UserRegistrationSerializer,
)


def _priority_score(request_data):
    score = 10
    if (request_data.get("emergency_level") or "").lower() == "urgent":
        score += 30
    score += min(int(request_data.get("units_required") or 0) * 5, 30)
    if request_data.get("blood_group") in {"AB-", "O-", "B-", "A-"}:
        score += 15
    notes = (request_data.get("additional_notes") or "").lower()
    if any(token in notes for token in ["icu", "accident", "critical", "surgery"]):
        score += 20
    return min(score, 100)


def _fraud_score(request_data):
    score = 0
    flags = []
    contact = request_data.get("contact_number")
    patient = request_data.get("patient_name")
    hospital_name = request_data.get("hospital_name")

    repeated_contact = BloodRequest.objects.filter(contact_number=contact).count()
    if repeated_contact >= 3:
        score += 35
        flags.append("repeated_contact")

    repeated_identity = BloodRequest.objects.filter(
        patient_name=patient,
        hospital_name=hospital_name,
    ).count()
    if repeated_identity >= 2:
        score += 25
        flags.append("repeated_patient_hospital")

    if not request_data.get("medical_proof_url"):
        score += 40
        flags.append("missing_medical_proof")

    return min(score, 100), ",".join(flags)


def _distance_score(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return 0
    return max(0.0, 1 - (sqrt((float(lat1) - float(lat2)) ** 2 + (float(lng1) - float(lng2)) ** 2) * 12))


@api_view(["GET"])
def health(_request):
    return Response({"status": "ok", "service": "blood-network-upgrade-api"})


@api_view(["POST"])
def register_user(request):
    serializer = UserRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def create_blood_request(request):
    payload = request.data.copy()
    payload["ai_priority_score"] = _priority_score(payload)
    fraud_score, fraud_flags = _fraud_score(payload)
    payload["fraud_risk_score"] = fraud_score
    payload["fraud_flags"] = fraud_flags

    serializer = BloodRequestSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    blood_request = serializer.save(status="Pending", admin_approved=False)
    return Response(BloodRequestSerializer(blood_request).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def approve_request(request, request_id):
    try:
        blood_request = BloodRequest.objects.get(id=request_id)
    except BloodRequest.DoesNotExist:
        return Response({"detail": "Request not found"}, status=status.HTTP_404_NOT_FOUND)

    blood_request.admin_approved = True
    blood_request.status = "Approved"
    blood_request.save(update_fields=["admin_approved", "status"])

    matched_donors = UserProfile.objects.filter(
        role="donor",
        active_donor=True,
        blood_group=blood_request.blood_group,
    )

    for donor in matched_donors[: max(1, blood_request.units_required)]:
        Notification.objects.create(
            user=donor,
            type="blood_request_alert",
            message=(
                f"Urgent blood request #{blood_request.id}: {blood_request.blood_group}, "
                f"{blood_request.hospital_location}, {blood_request.emergency_level}"
            ),
            sent_web=True,
        )

    return Response({
        "approved": True,
        "request_id": blood_request.id,
        "matched_donors": matched_donors.count(),
    })


@api_view(["POST"])
def donor_respond(request, request_id):
    payload = request.data.copy()
    payload["request"] = request_id
    serializer = DonorResponseSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    response_row = serializer.save()
    return Response(DonorResponseSerializer(response_row).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def pending_requests(_request):
    rows = BloodRequest.objects.filter(status="Pending", admin_approved=False).order_by("-ai_priority_score", "-created_at")
    return Response(BloodRequestSerializer(rows, many=True).data)


@api_view(["GET"])
def donor_match_map(request):
    blood_group = request.query_params.get("blood_group")
    latitude = request.query_params.get("latitude")
    longitude = request.query_params.get("longitude")

    donors = UserProfile.objects.filter(role="donor", active_donor=True)
    if blood_group:
        donors = donors.filter(blood_group=blood_group)

    scored_rows = []
    for donor in donors[:200]:
        score = _distance_score(latitude, longitude, donor.latitude, donor.longitude)
        scored_rows.append(
            {
                "id": donor.id,
                "name": donor.full_name,
                "blood_group": donor.blood_group,
                "city_area": donor.city_area,
                "latitude": donor.latitude,
                "longitude": donor.longitude,
                "response_rate": donor.response_rate,
                "distance_score": round(score, 4),
            }
        )

    scored_rows.sort(key=lambda row: (row["distance_score"], row["response_rate"]), reverse=True)
    return Response({"matched_donors": scored_rows[:50]})


@api_view(["POST"])
def create_camp(request):
    serializer = BloodCampSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    camp = serializer.save(status="Pending", approved=False)
    return Response(BloodCampSerializer(camp).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def approve_camp(_request, camp_id):
    try:
        camp = BloodCamp.objects.get(id=camp_id)
    except BloodCamp.DoesNotExist:
        return Response({"detail": "Camp not found"}, status=status.HTTP_404_NOT_FOUND)

    camp.approved = True
    camp.status = "Published"
    camp.save(update_fields=["approved", "status"])
    return Response({"approved": True, "camp_id": camp.id})


@api_view(["GET"])
def inventory(_request):
    rows = BloodInventory.objects.all().order_by("blood_group")
    return Response(BloodInventorySerializer(rows, many=True).data)


@api_view(["GET"])
def notifications(request, user_id):
    rows = Notification.objects.filter(user_id=user_id).order_by("-created_at")[:50]
    return Response(NotificationSerializer(rows, many=True).data)


@api_view(["GET"])
def impact_counters(_request):
    total_donors = UserProfile.objects.filter(role="donor").count()
    total_requests = BloodRequest.objects.count()
    total_lives_saved = (
        UserProfile.objects.filter(role="donor").aggregate(total=Sum("units_donated")).get("total") or 0
    ) * 3
    upcoming_camps = BloodCamp.objects.filter(Q(status="Pending") | Q(status="Published")).count()

    return Response(
        {
            "total_donors": total_donors,
            "total_blood_requests": total_requests,
            "lives_saved": total_lives_saved,
            "upcoming_camps": upcoming_camps,
        }
    )
