from django.db import models


ROLE_CHOICES = [
    ("admin", "Admin"),
    ("donor", "Donor"),
    ("hospital", "Hospital"),
    ("camp", "Camp"),
]

BLOOD_GROUP_CHOICES = [
    ("A+", "A+"),
    ("A-", "A-"),
    ("B+", "B+"),
    ("B-", "B-"),
    ("AB+", "AB+"),
    ("AB-", "AB-"),
    ("O+", "O+"),
    ("O-", "O-"),
]


class UserProfile(models.Model):
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)
    password_hash = models.CharField(max_length=255)
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES, blank=True, null=True)
    age = models.PositiveIntegerField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    city_area = models.CharField(max_length=180, blank=True, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    last_donation_date = models.DateField(blank=True, null=True)
    health_eligible = models.BooleanField(default=False)
    fit_confirmed = models.BooleanField(default=False)
    active_donor = models.BooleanField(default=False)
    approved = models.BooleanField(default=True)
    response_rate = models.FloatField(default=0)
    units_donated = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.role})"


class BloodRequest(models.Model):
    requester = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="created_requests")
    patient_name = models.CharField(max_length=120)
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES)
    hospital_name = models.CharField(max_length=180)
    hospital_location = models.CharField(max_length=200)
    hospital_latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    hospital_longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    units_required = models.PositiveIntegerField()
    emergency_level = models.CharField(max_length=20, default="Normal")
    contact_number = models.CharField(max_length=20)
    relationship_with_patient = models.CharField(max_length=120)
    medical_proof_url = models.CharField(max_length=255)
    additional_notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=30, default="Pending")
    admin_approved = models.BooleanField(default=False)
    ai_priority_score = models.IntegerField(default=0)
    fraud_risk_score = models.IntegerField(default=0)
    fraud_flags = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DonorResponse(models.Model):
    request = models.ForeignKey(BloodRequest, on_delete=models.CASCADE, related_name="responses")
    donor = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="responses")
    response_status = models.CharField(max_length=20)
    response_time = models.DateTimeField(auto_now_add=True)


class BloodInventory(models.Model):
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES, unique=True)
    units_available = models.PositiveIntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)


class BloodCamp(models.Model):
    organizer = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="organized_camps")
    camp_name = models.CharField(max_length=160)
    location = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    event_date = models.DateField()
    description = models.TextField(blank=True, null=True)
    expected_donors = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=30, default="Pending")
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="notifications")
    message = models.TextField()
    type = models.CharField(max_length=40)
    sent_email = models.BooleanField(default=False)
    sent_sms = models.BooleanField(default=False)
    sent_web = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
