from datetime import date

from rest_framework import serializers

from .models import BloodCamp, BloodInventory, BloodRequest, DonorResponse, Notification, UserProfile


class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "id",
            "role",
            "full_name",
            "email",
            "phone",
            "password_hash",
            "blood_group",
            "age",
            "gender",
            "city_area",
            "latitude",
            "longitude",
            "last_donation_date",
            "health_eligible",
            "fit_confirmed",
            "active_donor",
            "approved",
            "created_at",
        ]
        read_only_fields = ["active_donor", "approved", "created_at"]

    def validate(self, attrs):
        role = attrs.get("role")
        if role == "donor":
            age = attrs.get("age")
            health_ok = attrs.get("health_eligible")
            fit_ok = attrs.get("fit_confirmed")
            last_donation_date = attrs.get("last_donation_date")

            if age is None or age < 18 or age > 60:
                raise serializers.ValidationError("Donor age must be between 18 and 60.")
            if not health_ok or not fit_ok:
                raise serializers.ValidationError("Donor must pass health screening and fitness confirmation.")
            if last_donation_date and (date.today() - last_donation_date).days < 90:
                raise serializers.ValidationError("Minimum 90-day gap required since last donation.")

        return attrs

    def create(self, validated_data):
        role = validated_data.get("role")
        if role == "hospital" or role == "camp":
            validated_data["approved"] = False
        else:
            validated_data["approved"] = True

        if role == "donor":
            validated_data["active_donor"] = True

        return super().create(validated_data)


class BloodRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BloodRequest
        fields = "__all__"
        read_only_fields = ["status", "admin_approved", "ai_priority_score", "fraud_risk_score", "fraud_flags", "created_at"]


class DonorResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonorResponse
        fields = "__all__"


class BloodCampSerializer(serializers.ModelSerializer):
    class Meta:
        model = BloodCamp
        fields = "__all__"
        read_only_fields = ["approved", "status", "created_at"]


class BloodInventorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BloodInventory
        fields = "__all__"


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = "__all__"
