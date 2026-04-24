import os
import MySQLdb.cursors
from datetime import date, datetime
from uuid import uuid4

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from extensions import mysql
from models.models import (
    DONOR_STATUS_MEDICALLY_CLEARED,
    DONOR_STATUS_PERM_DEFERRED,
    DONOR_STATUS_PRE_ELIGIBLE,
    DONOR_STATUS_REGISTERED,
    DONOR_STATUS_TEMP_DEFERRED,
    calculate_alcohol_deferral_until,
    calculate_required_ml,
    is_supported_blood_group,
    current_user_payload,
    jwt_required,
    log_donor_deferral_event,
    log_activity,
    resolve_donor_access_state,
    validate_donor_rules,
    check_donation_eligibility,
    get_donor_next_eligible_date,
)
from services.ai_service import calculate_fraud_risk, calculate_priority_score

donor = Blueprint("donor", __name__, url_prefix="/donor")

TIME_SLOT_OPTIONS = ["Morning", "Afternoon", "Evening"]
ALLOWED_PROOF_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}


def _reward_tier(units_donated):
    units = int(units_donated or 0)
    if units >= 10:
        return "Life Saver"
    if units >= 5:
        return "Gold Donor"
    if units >= 3:
        return "Silver Donor"
    if units >= 1:
        return "Bronze Donor"
    return "New Donor"


def _is_allowed_proof(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_PROOF_EXTENSIONS


@donor.route("/dashboard", methods=["GET", "POST"])
@jwt_required(roles=["donor"])
def dashboard():
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    if request.method == "POST":
        action = (request.form.get("action") or "update_profile").strip()
        if action == "request_blood":
            patient_name = request.form.get("patient_name", "").strip()
            blood_group = request.form.get("blood_group", "").strip().upper()
            hospital_name = request.form.get("hospital_name", "").strip()
            hospital_location = request.form.get("hospital_location", "").strip()
            units_required = int(request.form.get("units_required", "0") or 0)
            emergency_level = request.form.get("emergency_level", "Normal").strip().title()
            emergency_level = "Urgent" if emergency_level == "Urgent" else "Normal"
            contact_number = request.form.get("contact_number", "").strip()
            relationship_with_patient = request.form.get("relationship_with_patient", "").strip()
            additional_notes = request.form.get("additional_notes", "").strip()
            medical_proof = request.files.get("medical_proof")

            if not patient_name or not blood_group or not hospital_name or not hospital_location or not contact_number or not relationship_with_patient:
                flash("Please fill all mandatory blood request fields.", "error")
                return redirect(url_for("donor.dashboard"))

            if not is_supported_blood_group(blood_group):
                flash("Select a valid blood group (A+, A-, B+, B-, AB+, AB-, O+, O-).", "error")
                return redirect(url_for("donor.dashboard"))

            if units_required < 1:
                flash("Units required must be at least 1.", "error")
                return redirect(url_for("donor.dashboard"))

            if not medical_proof or not medical_proof.filename:
                flash("Medical proof document is required.", "error")
                return redirect(url_for("donor.dashboard"))

            if not _is_allowed_proof(medical_proof.filename):
                flash("Medical proof must be PDF/JPG/JPEG/PNG.", "error")
                return redirect(url_for("donor.dashboard"))

            safe_name = secure_filename(medical_proof.filename)
            unique_filename = f"user_req_{donor_id}_{uuid4().hex}_{safe_name}"
            upload_dir = os.path.join(current_app.root_path, "uploads", "medical_proofs")
            os.makedirs(upload_dir, exist_ok=True)
            medical_proof.save(os.path.join(upload_dir, unique_filename))
            relative_proof_path = os.path.join("medical_proofs", unique_filename).replace("\\", "/")

            ai_priority_score = calculate_priority_score(
                blood_group,
                emergency_level,
                units_required,
                additional_notes,
            )
            fraud_risk_score, fraud_flags = calculate_fraud_risk(
                mysql,
                contact_number,
                patient_name,
                hospital_name,
                hospital_location,
            )

            cursor = mysql.connection.cursor()
            cursor.execute(
                """
                INSERT INTO blood_requests (
                    hospital_id,
                    requester_donor_id,
                    requester_role,
                    patient_name,
                    blood_group,
                    units_required,
                    required_ml,
                    emergency,
                    emergency_level,
                    hospital_address,
                    hospital_name_snapshot,
                    hospital_location_snapshot,
                    contact_number,
                    relationship_with_patient,
                    medical_proof_path,
                    additional_notes,
                    ai_priority_score,
                    fraud_risk_score,
                    fraud_flags,
                    status,
                    admin_approved
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending', FALSE)
                """,
                (
                    None,
                    donor_id,
                    "user",
                    patient_name,
                    blood_group,
                    units_required,
                    calculate_required_ml(units_required),
                    emergency_level == "Urgent",
                    emergency_level,
                    hospital_location,
                    hospital_name,
                    hospital_location,
                    contact_number,
                    relationship_with_patient,
                    relative_proof_path,
                    additional_notes,
                    ai_priority_score,
                    fraud_risk_score,
                    fraud_flags,
                ),
            )
            request_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO request_status_history (request_id, status, note, changed_by_role, changed_by_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (request_id, "Submitted", "User blood request submitted for admin verification.", "donor", donor_id),
            )
            mysql.connection.commit()
            cursor.close()

            log_activity(
                mysql,
                "donor",
                donor_id,
                "blood_request_submitted",
                "blood_request",
                request_id,
                f"User request: {units_required} unit(s) of {blood_group} for {patient_name}",
            )

            flash("Blood request submitted successfully. Awaiting admin verification.", "success")
            return redirect(url_for("donor.dashboard"))

        if action != "update_profile":
            flash("Invalid action.", "error")
            return redirect(url_for("donor.dashboard"))

        full_name = request.form.get("name", "").strip()
        age = int(request.form.get("age", "0") or 0)
        gender = request.form.get("gender", "").strip()
        blood_group_raw = request.form.get("blood_group", "").strip().upper()
        
        # Handle blood group verification
        if not blood_group_raw or blood_group_raw == "UNKNOWN":
            blood_group = "UNKNOWN"
            blood_group_verified = False
        else:
            if not is_supported_blood_group(blood_group_raw):
                flash("Select a valid blood group (A+, A-, B+, B-, AB+, AB-, O+, O-).", "error")
                return redirect(url_for("donor.dashboard"))
            blood_group = blood_group_raw
            # If user is selecting a known blood group for the first time, mark as self-reported (unverified)
            # It will only be verified after a camp donation
            blood_group_verified = False
        
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        first_time_donor = request.form.get("first_time_donor") == "on"
        last_donation_raw = request.form.get("last_donation", "").strip()
        last_donation = None if first_time_donor or not last_donation_raw else last_donation_raw
        alcohol_consumed_recently = (request.form.get("alcohol_consumed_recently") or "no").strip().lower() == "yes"
        last_alcohol_consumption_raw = request.form.get("last_alcohol_consumption_datetime", "").strip()
        health_status = request.form.get("health_status") == "on"
        fit_confirmation = request.form.get("fit_confirmation") == "on"

        last_alcohol_consumption_datetime = None
        if alcohol_consumed_recently:
            if not last_alcohol_consumption_raw:
                flash("Please provide last alcohol consumption date and time.", "error")
                return redirect(url_for("donor.dashboard"))
            try:
                last_alcohol_consumption_datetime = datetime.strptime(last_alcohol_consumption_raw, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Alcohol consumption date/time format is invalid.", "error")
                return redirect(url_for("donor.dashboard"))

        temporary_deferral_until = calculate_alcohol_deferral_until(
            alcohol_consumed_recently,
            last_alcohol_consumption_datetime,
        )

        validation_error = validate_donor_rules(age, health_status, fit_confirmation)
        if validation_error:
            flash(validation_error, "error")
            return redirect(url_for("donor.dashboard"))

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute(
            """
            SELECT blood_group, blood_group_verified, donor_status, account_status, is_permanently_deferred, deferral_reason
            FROM donors
            WHERE id = %s
            """,
            (donor_id,),
        )
        existing_donor = cursor.fetchone() or {}

        existing_group = (existing_donor.get("blood_group") or "").strip().upper()
        existing_verified = bool(existing_donor.get("blood_group_verified"))
        if blood_group and blood_group == existing_group and existing_verified:
            blood_group_verified = True

        now_utc = datetime.utcnow()
        is_permanently_deferred = bool(existing_donor.get("is_permanently_deferred"))
        current_donor_status = (existing_donor.get("donor_status") or DONOR_STATUS_REGISTERED).strip()

        if is_permanently_deferred:
            donor_status = DONOR_STATUS_PERM_DEFERRED
            deferral_reason = (existing_donor.get("deferral_reason") or "Permanent Medical Deferral").strip()
        elif temporary_deferral_until and temporary_deferral_until > now_utc:
            donor_status = DONOR_STATUS_TEMP_DEFERRED
            deferral_reason = "Alcohol (24h Rule)"
        elif current_donor_status == DONOR_STATUS_MEDICALLY_CLEARED and blood_group_verified:
            donor_status = DONOR_STATUS_MEDICALLY_CLEARED
            deferral_reason = None
        else:
            donor_status = DONOR_STATUS_PRE_ELIGIBLE
            deferral_reason = None

        cursor.close()
        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            UPDATE donors
            SET name = %s,
                age = %s,
                gender = %s,
                blood_group = %s,
                blood_group_verified = %s,
                address = %s,
                phone = %s,
                last_donation = %s,
                account_status = %s,
                donor_status = %s,
                deferral_reason = %s,
                alcohol_consumed_recently = %s,
                last_alcohol_consumption_datetime = %s,
                temporary_deferral_until = %s,
                health_status = %s,
                fit_confirmation = %s
            WHERE id = %s
            """,
            (
                full_name,
                age,
                gender,
                blood_group,
                blood_group_verified,
                address,
                phone,
                last_donation,
                "ProfileCompleted",
                donor_status,
                deferral_reason,
                alcohol_consumed_recently,
                last_alcohol_consumption_datetime,
                temporary_deferral_until,
                health_status,
                fit_confirmation,
                donor_id,
            ),
        )
        mysql.connection.commit()
        cursor.close()

        if temporary_deferral_until:
            log_donor_deferral_event(
                mysql,
                donor_id,
                "Alcohol (24h Rule)",
                last_alcohol_consumption_datetime,
                temporary_deferral_until,
                source="profile_update",
            )

        if donor_status == DONOR_STATUS_PRE_ELIGIBLE:
            log_activity(
                mysql,
                "donor",
                donor_id,
                "donor_status_changed",
                "donor_profile",
                donor_id,
                "Donor moved to Pre-Eligible after profile completion.",
            )
            log_activity(
                mysql,
                "donor",
                donor_id,
                "alcohol_deferral_applied",
                "donor_profile",
                donor_id,
                (
                    "Temporary deferral applied: Alcohol (24h Rule), "
                    f"eligible after {temporary_deferral_until}"
                ),
            )

        log_activity(
            mysql,
            "donor",
            donor_id,
            "profile_updated",
            "donor_profile",
            donor_id,
            f"Donor profile updated for blood group {blood_group}",
        )

        flash("Profile updated successfully.", "success")
        return redirect(url_for("donor.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM donors WHERE id = %s", (donor_id,))
    donor_profile = cursor.fetchone()

    donor_access_state = resolve_donor_access_state(donor_profile)
    current_status = donor_access_state["status"]
    stored_status = ((donor_profile or {}).get("donor_status") or "").strip()
    if donor_profile and current_status and current_status != stored_status:
        cursor.execute(
            """
            UPDATE donors
            SET donor_status = %s,
                deferral_reason = %s
            WHERE id = %s
            """,
            (
                current_status,
                donor_access_state.get("deferral_reason") or None,
                donor_id,
            ),
        )
        mysql.connection.commit()
        cursor.execute("SELECT * FROM donors WHERE id = %s", (donor_id,))
        donor_profile = cursor.fetchone()
        donor_access_state = resolve_donor_access_state(donor_profile)

    next_eligible_date = get_donor_next_eligible_date(mysql, donor_id)
    temporary_deferral_until = donor_access_state.get("temporary_deferral_until")
    is_temporarily_ineligible = donor_access_state.get("status") == DONOR_STATUS_TEMP_DEFERRED

    donor_units = int((donor_profile or {}).get("units_donated") or 0)
    donor_reward_tier = _reward_tier(donor_units)

    if donor_profile and donor_profile.get("last_donation") and not next_eligible_date:
        cursor.execute(
            """
            SELECT id
            FROM notifications
            WHERE user_role = 'donor'
              AND user_id = %s
              AND type = 'donation_eligibility_reminder'
              AND DATE(created_at) = CURDATE()
            LIMIT 1
            """,
            (donor_id,),
        )
        already_notified = cursor.fetchone()
        if not already_notified:
            cursor.execute(
                """
                INSERT INTO notifications (user_id, user_role, message, type)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    donor_id,
                    "donor",
                    f"Hi {(donor_profile.get('name') or 'Donor')}, you are now eligible to donate blood again. Help save lives.",
                    "donation_eligibility_reminder",
                ),
            )
            mysql.connection.commit()

    cursor.execute(
        """
        SELECT id, name, blood_group, units_donated
        FROM donors
        WHERE COALESCE(units_donated, 0) > 0
        ORDER BY units_donated DESC, id ASC
        LIMIT 10
        """
    )
    donor_leaderboard = cursor.fetchall()

    cursor.execute(
        """
        SELECT COUNT(*) + 1 AS rank_position
        FROM donors
        WHERE COALESCE(units_donated, 0) > %s
        """,
        (donor_units,),
    )
    donor_rank = int((cursor.fetchone() or {}).get("rank_position") or 1)

    donor_address = (donor_profile or {}).get("address") or ""

    cursor.execute(
        """
        SELECT
            ce.id,
            ce.event_name,
            ce.location,
            ce.event_date,
            ce.event_end_date,
            ce.target_units,
            ce.contact_info,
            bc.camp_name,
            bc.phone AS camp_phone,
            GROUP_CONCAT(DISTINCT cebg.blood_group ORDER BY cebg.blood_group SEPARATOR ', ') AS required_groups,
            CASE
                WHEN %s <> '' AND (ce.location LIKE %s OR %s LIKE CONCAT('%%', ce.location, '%%')) THEN 1
                ELSE 0
            END AS nearby_score,
            EXISTS(
                SELECT 1
                FROM camp_event_registrations cerx
                WHERE cerx.event_id = ce.id AND cerx.donor_id = %s
            ) AS already_registered
        FROM camp_events ce
        JOIN blood_camps bc ON bc.id = ce.camp_id
        LEFT JOIN camp_event_blood_groups cebg ON cebg.event_id = ce.id
        WHERE ce.event_date >= CURDATE()
          AND ce.status = 'Upcoming'
          AND bc.approved = TRUE
        GROUP BY ce.id
        ORDER BY nearby_score DESC, ce.event_date ASC, ce.created_at DESC
        """,
        (donor_address, f"%{donor_address}%", donor_address, donor_id),
    )
    upcoming_camps = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            cer.id,
            ce.event_name,
            ce.location,
            ce.event_date,
            cer.preferred_slot,
            cer.registration_status,
            cer.units_collected,
            cer.created_at,
            cer.donated_at
        FROM camp_event_registrations cer
        JOIN camp_events ce ON ce.id = cer.event_id
        WHERE cer.donor_id = %s
        ORDER BY ce.event_date DESC, cer.created_at DESC
        """,
        (donor_id,),
    )
    camp_registrations = cursor.fetchall()

    cursor.execute(
        """
        SELECT action, entity_type, details, created_at
        FROM activity_logs
        WHERE actor_role = 'donor' AND actor_id = %s
          AND action NOT IN ('login', 'login_confirmed', 'google_login', 'profile_updated')
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (donor_id,),
    )
    donor_logs = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            br.id,
            br.patient_name,
            br.blood_group,
            br.units_required,
            br.emergency_level,
            br.hospital_name_snapshot,
            br.hospital_location_snapshot,
            br.contact_number,
            br.created_at,
            EXISTS(
                SELECT 1
                FROM donor_responses dr
                WHERE dr.request_id = br.id
                  AND dr.donor_id = %s
            ) AS already_responded,
            (
                SELECT COUNT(*)
                FROM donor_responses dra
                WHERE dra.request_id = br.id
                  AND dra.response_status = 'Accepted'
            ) AS accepted_count
        FROM blood_requests br
        WHERE COALESCE(br.admin_approved, FALSE) = TRUE
          AND br.status IN ('Approved', 'Pending', 'Pending Transfer')
          AND br.blood_group = %s
          AND (br.requester_donor_id IS NULL OR br.requester_donor_id <> %s)
        ORDER BY br.emergency DESC, br.created_at DESC
        LIMIT 20
        """,
        (donor_id, (donor_profile or {}).get("blood_group") or "", donor_id),
    )
    donation_requests = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            br.id,
            br.patient_name,
            br.blood_group,
            br.units_required,
            br.status,
            br.emergency_level,
            br.created_at,
            (
                SELECT GROUP_CONCAT(
                    CONCAT(d.name, ' (', d.phone, ')')
                    ORDER BY dr.response_time ASC
                    SEPARATOR ', '
                )
                FROM donor_responses dr
                JOIN donors d ON d.id = dr.donor_id
                WHERE dr.request_id = br.id
                  AND dr.response_status = 'Accepted'
            ) AS accepted_donor_contacts
        FROM blood_requests br
        WHERE br.requester_donor_id = %s
        ORDER BY br.created_at DESC
        LIMIT 20
        """,
        (donor_id,),
    )
    my_blood_requests = cursor.fetchall()

    cursor.execute(
        """
        SELECT message, type, created_at
        FROM notifications
        WHERE user_role = 'donor' AND user_id = %s
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (donor_id,),
    )
    donor_notifications = cursor.fetchall()

    # Fetch admin camp donations (donor selected dates)
    cursor.execute(
        """
        SELECT id, selected_donation_date, status, donated_at, created_at, updated_at
        FROM admin_camp_donations
        WHERE donor_id = %s
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (donor_id,),
    )
    admin_camp_donations = cursor.fetchall()
    cursor.close()

    return render_template(
        "donor_dashboard.html",
        donor=donor_profile,
        donor_logs=donor_logs,
        upcoming_camps=upcoming_camps,
        camp_registrations=camp_registrations,
        time_slot_options=TIME_SLOT_OPTIONS,
        today_date=date.today(),
        next_eligible_date=next_eligible_date,
        donor_reward_tier=donor_reward_tier,
        donor_rank=donor_rank,
        donor_units=donor_units,
        donor_leaderboard=donor_leaderboard,
        admin_camp_donations=admin_camp_donations,
        donation_requests=donation_requests,
        my_blood_requests=my_blood_requests,
        donor_notifications=donor_notifications,
        donor_access_state=donor_access_state,
        is_temporarily_ineligible=is_temporarily_ineligible,
        temporary_deferral_until=temporary_deferral_until,
    )


@donor.route("/blood-request/<int:request_id>/respond", methods=["POST"])
@jwt_required(roles=["donor"])
def respond_to_blood_request(request_id):
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)
    response_status = (request.form.get("response_status") or "").strip().title()

    if response_status not in {"Accepted", "Declined"}:
        flash("Invalid response action.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT *
        FROM donors
        WHERE id = %s
        """,
        (donor_id,),
    )
    donor_profile = cursor.fetchone() or {}
    donor_access_state = resolve_donor_access_state(donor_profile)

    if response_status == "Accepted":
        if not donor_access_state.get("can_complete_donation"):
            cursor.close()
            if donor_access_state.get("status") == DONOR_STATUS_PRE_ELIGIBLE:
                flash("Your account is Pre-Eligible. Medical clearance is required before completing donation.", "error")
            elif donor_access_state.get("status") == DONOR_STATUS_TEMP_DEFERRED:
                flash(
                    f"Temporarily ineligible due to {donor_access_state.get('deferral_reason') or 'deferral'}.",
                    "error",
                )
            else:
                flash("Your current donor status does not allow donation completion.", "error")
            return redirect(url_for("donor.dashboard"))

        is_eligible, _next_eligible_date, eligibility_message = check_donation_eligibility(mysql, donor_id)
        if not is_eligible:
            cursor.close()
            flash(eligibility_message or "You are temporarily ineligible to accept this request.", "error")
            return redirect(url_for("donor.dashboard"))

    cursor.execute(
        """
        SELECT id, blood_group, units_required, requester_role, requester_donor_id, hospital_id, contact_number
        FROM blood_requests
        WHERE id = %s
          AND COALESCE(admin_approved, FALSE) = TRUE
          AND status IN ('Approved', 'Pending', 'Pending Transfer')
        """,
        (request_id,),
    )
    request_row = cursor.fetchone()

    if not request_row:
        cursor.close()
        flash("Request is not available for donor response.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor.execute("SELECT blood_group FROM donors WHERE id = %s", (donor_id,))
    donor_row = cursor.fetchone()
    if not donor_row:
        cursor.close()
        flash("Donor profile not found.", "error")
        return redirect(url_for("donor.dashboard"))

    if (donor_row.get("blood_group") or "") != (request_row.get("blood_group") or ""):
        cursor.close()
        flash("Your blood group does not match this request.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor.execute(
        """
        SELECT id
        FROM donor_responses
        WHERE request_id = %s AND donor_id = %s
        """,
        (request_id, donor_id),
    )
    existing = cursor.fetchone()

    if response_status == "Accepted":
        cursor.execute(
            """
            SELECT COUNT(*) AS accepted_total
            FROM donor_responses
            WHERE request_id = %s
              AND response_status = 'Accepted'
            """,
            (request_id,),
        )
        accepted_total = int((cursor.fetchone() or {}).get("accepted_total") or 0)
        if accepted_total >= int(request_row.get("units_required") or 0):
            cursor.close()
            flash("Required donor count is already fulfilled for this request.", "error")
            return redirect(url_for("donor.dashboard"))

    if existing:
        cursor.execute(
            """
            UPDATE donor_responses
            SET response_status = %s,
                response_time = NOW()
            WHERE request_id = %s AND donor_id = %s
            """,
            (response_status, request_id, donor_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO donor_responses (request_id, donor_id, response_status)
            VALUES (%s, %s, %s)
            """,
            (request_id, donor_id, response_status),
        )

    if response_status == "Accepted":
        cursor.execute("SELECT name, phone, blood_group, address FROM donors WHERE id = %s", (donor_id,))
        donor_contact = cursor.fetchone() or {}

        if request_row.get("requester_role") == "user" and request_row.get("requester_donor_id"):
            message = (
                f"Donor accepted request #{request_id}: {donor_contact.get('name')} | "
                f"{donor_contact.get('blood_group')} | {donor_contact.get('address') or 'N/A'} | "
                f"{donor_contact.get('phone') or 'N/A'}"
            )
            cursor.execute(
                """
                INSERT INTO notifications (user_id, user_role, message, type, related_request_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (request_row["requester_donor_id"], "donor", message, "donor_acceptance", request_id),
            )

    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "donor",
        donor_id,
        f"blood_request_{response_status.lower()}",
        "blood_request",
        request_id,
        f"Donor {response_status.lower()} request #{request_id}",
    )

    flash(f"You have {response_status.lower()} this blood request.", "success")
    return redirect(url_for("donor.dashboard"))


@donor.route("/camp-event/<int:event_id>/register", methods=["POST"])
@jwt_required(roles=["donor"])
def register_for_camp_event(event_id):
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)
    preferred_slot = (request.form.get("preferred_slot") or "").strip()

    if preferred_slot not in TIME_SLOT_OPTIONS:
        flash("Select a valid preferred time slot.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT id, blood_group, donor_status, account_status, is_permanently_deferred, temporary_deferral_until, deferral_reason
        FROM donors
        WHERE id = %s
        """,
        (donor_id,),
    )
    donor_row = cursor.fetchone()

    if not donor_row:
        cursor.close()
        flash("Donor profile not found.", "error")
        return redirect(url_for("donor.dashboard"))

    # Check donation eligibility
    access_state = resolve_donor_access_state(donor_row)
    if access_state.get("status") == DONOR_STATUS_PERM_DEFERRED:
        cursor.close()
        flash("You are permanently deferred and cannot register for camp events.", "error")
        return redirect(url_for("donor.dashboard"))

    is_eligible, next_eligible_date, eligibility_message = check_donation_eligibility(mysql, donor_id)
    if not is_eligible:
        cursor.close()
        flash(eligibility_message, "error")
        return redirect(url_for("donor.dashboard"))

    cursor.execute(
        """
        SELECT
            ce.id,
            ce.event_name,
            ce.event_date,
            GROUP_CONCAT(DISTINCT cebg.blood_group ORDER BY cebg.blood_group SEPARATOR ',') AS required_groups
        FROM camp_events ce
        JOIN blood_camps bc ON bc.id = ce.camp_id
        LEFT JOIN camp_event_blood_groups cebg ON cebg.event_id = ce.id
        WHERE ce.id = %s
          AND ce.status = 'Upcoming'
          AND ce.event_date >= CURDATE()
          AND bc.approved = TRUE
        GROUP BY ce.id
        """,
        (event_id,),
    )
    event_row = cursor.fetchone()

    if not event_row:
        cursor.close()
        flash("Camp event not available for registration.", "error")
        return redirect(url_for("donor.dashboard"))

    required_groups = [group.strip() for group in (event_row.get("required_groups") or "").split(",") if group.strip()]
    donor_group = (donor_row.get("blood_group") or "").strip()
    if required_groups and donor_group and donor_group not in required_groups:
        cursor.close()
        flash("This event is currently accepting different blood groups.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor.execute(
        """
        SELECT id
        FROM camp_event_registrations
        WHERE event_id = %s
          AND donor_id = %s
        """,
        (event_id, donor_id),
    )
    existing_row = cursor.fetchone()
    if existing_row:
        cursor.close()
        flash("You are already registered for this camp event.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor.execute(
        """
        INSERT INTO camp_event_registrations (event_id, donor_id, preferred_slot, registration_status)
        VALUES (%s, %s, %s, 'Registered')
        """,
        (event_id, donor_id, preferred_slot),
    )
    registration_id = cursor.lastrowid
    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "donor",
        donor_id,
        "camp_event_registered",
        "camp_event_registration",
        registration_id,
        f"Registered for event '{event_row['event_name']}' ({event_row['event_date']}) at {preferred_slot} slot",
    )

    flash("Camp registration successful.", "success")
    return redirect(url_for("donor.dashboard"))


@donor.route("/admin_camp_donation", methods=["POST"])
@jwt_required(roles=["donor"])
def submit_admin_camp_donation():
    """
    Donor submits a preferred donation date for admin camp donations.
    Eligibility check enforced (90/120 day gap).
    No admin approval needed - donor can reselect if unable to donate on selected date.
    """
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    donation_date_str = request.form.get("donation_date", "").strip()
    action = request.form.get("action", "submit").strip()

    if not donation_date_str:
        flash("Please select a donation date.", "error")
        return redirect(url_for("donor.dashboard"))

    try:
        from datetime import datetime
        donation_date = datetime.strptime(donation_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format. Please use YYYY-MM-DD.", "error")
        return redirect(url_for("donor.dashboard"))

    # Validate date is in future
    if donation_date <= date.today():
        flash("Donation date must be in the future.", "error")
        return redirect(url_for("donor.dashboard"))

    # Check eligibility (90/120 day gap)
    is_eligible, next_eligible_date, eligibility_message = check_donation_eligibility(mysql, donor_id)
    if not is_eligible:
        flash(f"You are not eligible to donate yet. {eligibility_message}", "error")
        return redirect(url_for("donor.dashboard"))

    # Check if selected date is at least on next_eligible_date
    if next_eligible_date and donation_date < next_eligible_date:
        flash(f"Your selected date must be on or after {next_eligible_date.strftime('%B %d, %Y')}.", "error")
        return redirect(url_for("donor.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        if action == "reselect":
            # Update existing admin camp donation record
            cursor.execute(
                """
                UPDATE admin_camp_donations 
                SET selected_donation_date = %s, status = 'Pending', updated_at = NOW()
                WHERE donor_id = %s AND status IN ('Pending', 'Missed')
                """,
                (donation_date, donor_id),
            )
            mysql.connection.commit()
            log_activity(
                mysql,
                "donor",
                donor_id,
                "admin_camp_donation_reselected",
                "admin_camp_donation",
                donor_id,
                f"Admin camp donation date reselected to {donation_date.strftime('%B %d, %Y')}",
            )
            flash("Admin camp donation date updated successfully.", "success")
        else:
            # Create new admin camp donation request
            cursor.execute(
                """
                INSERT INTO admin_camp_donations (donor_id, selected_donation_date, status)
                VALUES (%s, %s, 'Pending')
                """,
                (donor_id, donation_date),
            )
            mysql.connection.commit()
            log_activity(
                mysql,
                "donor",
                donor_id,
                "admin_camp_donation_submitted",
                "admin_camp_donation",
                cursor.lastrowid,
                f"Admin camp donation date selected: {donation_date.strftime('%B %d, %Y')}",
            )
            flash("Admin camp donation date submitted successfully. You can reselect anytime if needed.", "success")

    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error submitting donation date: {str(e)}", "error")
    finally:
        cursor.close()

    return redirect(url_for("donor.dashboard"))


@donor.route("/api/live-requests")
@jwt_required(roles=["donor"])
def live_requests_api():
    """Return live incoming requests + my submitted request progress for donor dashboard polling."""
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT blood_group FROM donors WHERE id = %s", (donor_id,))
    donor_row = cursor.fetchone() or {}
    donor_group = (donor_row.get("blood_group") or "").strip()

    cursor.execute(
        """
        SELECT
            br.id,
            br.patient_name,
            br.blood_group,
            br.units_required,
            br.emergency_level,
            br.hospital_location_snapshot,
            br.status,
            br.ai_priority_score,
            (
                SELECT COUNT(*)
                FROM donor_responses drx
                WHERE drx.request_id = br.id
                  AND drx.response_status = 'Accepted'
            ) AS accepted_count,
            EXISTS(
                SELECT 1
                FROM donor_responses dr
                WHERE dr.request_id = br.id
                  AND dr.donor_id = %s
            ) AS already_responded
        FROM blood_requests br
        WHERE COALESCE(br.admin_approved, FALSE) = TRUE
          AND br.status IN ('Approved', 'Pending', 'Pending Transfer')
          AND br.blood_group = %s
          AND (br.requester_donor_id IS NULL OR br.requester_donor_id <> %s)
        ORDER BY br.emergency DESC, br.ai_priority_score DESC, br.created_at DESC
        LIMIT 20
        """,
        (donor_id, donor_group, donor_id),
    )
    incoming = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            br.id,
            br.patient_name,
            br.blood_group,
            br.units_required,
            br.status,
            br.emergency_level,
            br.ai_priority_score,
            (
                SELECT COUNT(*)
                FROM donor_responses dra
                WHERE dra.request_id = br.id
                  AND dra.response_status = 'Accepted'
            ) AS accepted_count,
            (
                SELECT GROUP_CONCAT(
                    CONCAT(d.name, ' (', d.phone, ')')
                    ORDER BY dr.response_time ASC
                    SEPARATOR ', '
                )
                FROM donor_responses dr
                JOIN donors d ON d.id = dr.donor_id
                WHERE dr.request_id = br.id
                  AND dr.response_status = 'Accepted'
            ) AS accepted_donor_contacts
        FROM blood_requests br
        WHERE br.requester_donor_id = %s
        ORDER BY br.created_at DESC
        LIMIT 20
        """,
        (donor_id,),
    )
    my_requests = cursor.fetchall()
    cursor.close()

    return jsonify({"incoming": incoming, "my_requests": my_requests})
