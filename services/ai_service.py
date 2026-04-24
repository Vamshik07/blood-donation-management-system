RARE_GROUP_BONUS = {"O-": 20, "AB-": 18, "B-": 14, "A-": 12}


def calculate_priority_score(blood_group, emergency_level, units_required, additional_notes):
    """Heuristic priority score (0-100) for triage ordering."""
    score = 20
    if (emergency_level or "").strip().lower() == "urgent":
        score += 35

    score += min(max(int(units_required or 0), 0) * 4, 20)
    score += RARE_GROUP_BONUS.get((blood_group or "").strip().upper(), 0)

    note = (additional_notes or "").lower()
    critical_keywords = ["icu", "accident", "critical", "surgery", "trauma", "hemorrhage"]
    if any(keyword in note for keyword in critical_keywords):
        score += 15

    return max(0, min(score, 100))


def calculate_fraud_risk(mysql, contact_number, patient_name, hospital_name, hospital_location):
    """Simple fraud-risk scoring based on repeated request patterns in recent history."""
    score = 0
    flags = []

    cursor = mysql.connection.cursor()

    # Repeated requests by same contact in last 14 days.
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM blood_requests
        WHERE contact_number = %s
          AND created_at >= (NOW() - INTERVAL 14 DAY)
        """,
        (contact_number,),
    )
    recent_by_contact = int((cursor.fetchone() or [0])[0] or 0)
    if recent_by_contact >= 3:
        score += 35
        flags.append("high_repeat_contact")
    elif recent_by_contact == 2:
        score += 20
        flags.append("repeat_contact")

    # Repeated same patient + hospital pattern in last 30 days.
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM blood_requests
        WHERE patient_name = %s
          AND hospital_name_snapshot = %s
          AND hospital_location_snapshot = %s
          AND created_at >= (NOW() - INTERVAL 30 DAY)
        """,
        (patient_name, hospital_name, hospital_location),
    )
    repeated_profile = int((cursor.fetchone() or [0])[0] or 0)
    if repeated_profile >= 2:
        score += 25
        flags.append("repeated_patient_profile")

    if not contact_number or len(contact_number.replace("+", "", 1)) < 10:
        score += 20
        flags.append("invalid_contact")

    cursor.close()
    return max(0, min(score, 100)), ",".join(flags)