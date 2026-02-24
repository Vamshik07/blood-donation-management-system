import MySQLdb.cursors
from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import mysql
from models.models import current_user_payload, jwt_required, log_activity, validate_donor_rules

donor = Blueprint("donor", __name__, url_prefix="/donor")

TIME_SLOT_OPTIONS = ["Morning", "Afternoon", "Evening"]


@donor.route("/dashboard", methods=["GET", "POST"])
@jwt_required(roles=["donor"])
def dashboard():
    user_data = current_user_payload()
    donor_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    if request.method == "POST":
        action = (request.form.get("action") or "update_profile").strip()
        if action != "update_profile":
            flash("Invalid action.", "error")
            return redirect(url_for("donor.dashboard"))

        full_name = request.form.get("name", "").strip()
        age = int(request.form.get("age", "0") or 0)
        blood_group_raw = request.form.get("blood_group", "").strip()
        
        # Handle blood group verification
        if not blood_group_raw or blood_group_raw == "UNKNOWN":
            blood_group = "UNKNOWN"
            blood_group_verified = False
        else:
            blood_group = blood_group_raw
            # If user is selecting a known blood group for the first time, mark as self-reported (unverified)
            # It will only be verified after a camp donation
            blood_group_verified = False
        
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        first_time_donor = request.form.get("first_time_donor") == "on"
        last_donation_raw = request.form.get("last_donation", "").strip()
        last_donation = None if first_time_donor or not last_donation_raw else last_donation_raw
        health_status = True
        fit_confirmation = request.form.get("fit_confirmation") == "on"

        validation_error = validate_donor_rules(age, health_status, fit_confirmation)
        if validation_error:
            flash(validation_error, "error")
            return redirect(url_for("donor.dashboard"))

        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            UPDATE donors
            SET name = %s,
                age = %s,
                blood_group = %s,
                blood_group_verified = %s,
                address = %s,
                phone = %s,
                last_donation = %s,
                health_status = %s,
                fit_confirmation = %s,
                approved = FALSE
            WHERE id = %s
            """,
            (
                full_name,
                age,
                blood_group,
                blood_group_verified,
                address,
                phone,
                last_donation,
                health_status,
                fit_confirmation,
                donor_id,
            ),
        )
        mysql.connection.commit()
        cursor.close()

        log_activity(
            mysql,
            "donor",
            donor_id,
            "profile_updated",
            "donor_profile",
            donor_id,
            f"Donor profile updated for blood group {blood_group}",
        )

        flash("Profile submitted for admin approval.", "success")
        return redirect(url_for("donor.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM donors WHERE id = %s", (donor_id,))
    donor_profile = cursor.fetchone()

    donor_address = (donor_profile or {}).get("address") or ""

    cursor.execute(
        """
        SELECT
            ce.id,
            ce.event_name,
            ce.location,
            ce.event_date,
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
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (donor_id,),
    )
    donor_logs = cursor.fetchall()
    cursor.close()

    return render_template(
        "donor_dashboard.html",
        donor=donor_profile,
        donor_logs=donor_logs,
        upcoming_camps=upcoming_camps,
        camp_registrations=camp_registrations,
        time_slot_options=TIME_SLOT_OPTIONS,
    )


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
        SELECT id, approved, blood_group
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

    if not donor_row.get("approved"):
        cursor.close()
        flash("Your profile must be approved before registering for camp events.", "error")
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
