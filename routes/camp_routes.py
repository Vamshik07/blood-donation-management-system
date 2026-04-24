import MySQLdb.cursors
import csv
from io import StringIO
from datetime import datetime
from uuid import uuid4

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for

from extensions import mysql
from models.models import current_user_payload, jwt_required, log_activity, check_donation_eligibility
from services.email_service import send_email
from services.notifications import send_sms_update

camp = Blueprint("camp", __name__, url_prefix="/camp")

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


def _is_camp_profile_complete(camp_row):
    if not camp_row:
        return False

    return bool(
        (camp_row.get("camp_name") or "").strip()
        and (camp_row.get("phone") or "").strip()
        and (camp_row.get("location") or "").strip()
        and int(camp_row.get("days") or 0) > 0
        and int(camp_row.get("slots_per_day") or 0) > 0
        and int(camp_row.get("expected_donors") or 0) > 0
    )


@camp.route("/dashboard", methods=["GET", "POST"])
@jwt_required(roles=["camp"])
def dashboard():
    user_data = current_user_payload()
    camp_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    if request.method == "POST":
        action = (request.form.get("action") or "update_profile").strip()

        if action == "update_profile":
            camp_name = request.form.get("camp_name", "").strip()
            phone = request.form.get("phone", "").strip()
            location = request.form.get("location", "").strip()
            days = int(request.form.get("days", "0") or 0)
            slots_per_day = int(request.form.get("slots_per_day", "0") or 0)
            expected_donors = int(request.form.get("expected_donors", "0") or 0)

            if days < 1 or slots_per_day < 1 or expected_donors < 1:
                flash("Days, slots per day and expected donors must be greater than 0.", "error")
                return redirect(url_for("camp.dashboard"))

            if not phone.replace("+", "", 1).isdigit() or len(phone.replace("+", "", 1)) < 10:
                flash("Enter a valid contact number with at least 10 digits.", "error")
                return redirect(url_for("camp.dashboard"))

            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute(
                """
                UPDATE blood_camps
                SET camp_name = %s,
                    phone = %s,
                    location = %s,
                    days = %s,
                    slots_per_day = %s,
                    expected_donors = %s
                WHERE id = %s
                """,
                (camp_name, phone, location, days, slots_per_day, expected_donors, camp_id),
            )

            cursor.execute("SELECT * FROM blood_camps WHERE id = %s", (camp_id,))
            camp_row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) AS total FROM camp_events WHERE camp_id = %s", (camp_id,))
            event_count_row = cursor.fetchone() or {"total": 0}

            pending_approval_requested = False
            if camp_row and bool(camp_row.get("approved")) and _is_camp_profile_complete(camp_row) and int(event_count_row.get("total") or 0) > 0:
                cursor.execute("UPDATE blood_camps SET approved = FALSE WHERE id = %s", (camp_id,))
                pending_approval_requested = True

            mysql.connection.commit()
            cursor.close()

            log_activity(
                mysql,
                "camp",
                camp_id,
                "camp_profile_updated",
                "blood_camp",
                camp_id,
                f"Camp profile updated: {camp_name}",
            )

            if pending_approval_requested:
                flash("Camp profile updated. Approval request has been sent to admin after completing profile and event details.", "success")
            else:
                flash("Camp profile details saved.", "success")
            return redirect(url_for("camp.dashboard"))

        if action == "create_event":
            event_name = request.form.get("event_name", "").strip()
            event_location = request.form.get("event_location", "").strip()
            event_start_date = request.form.get("event_start_date", "").strip()
            event_end_date = request.form.get("event_end_date", "").strip()
            target_units = int(request.form.get("target_units", "0") or 0)
            required_groups = request.form.getlist("required_blood_groups")
            contact_info = request.form.get("event_contact", "").strip()
            organizer_name = request.form.get("organizer_name", "").strip()
            camp_phone = request.form.get("camp_phone", "").strip()

            if not event_name or not event_location or not event_start_date or not event_end_date:
                flash("Event name, location, start date and end date are required.", "error")
                return redirect(url_for("camp.dashboard"))

            if not organizer_name:
                flash("Organizer name is required.", "error")
                return redirect(url_for("camp.dashboard"))

            if not camp_phone:
                flash("Camp phone is required.", "error")
                return redirect(url_for("camp.dashboard"))

            if not camp_phone.replace("+", "", 1).isdigit() or len(camp_phone.replace("+", "", 1)) < 10:
                flash("Enter a valid camp phone number with at least 10 digits.", "error")
                return redirect(url_for("camp.dashboard"))

            if target_units < 1:
                flash("Target units must be greater than 0.", "error")
                return redirect(url_for("camp.dashboard"))

            try:
                start_date_obj = datetime.strptime(event_start_date, "%Y-%m-%d").date()
                end_date_obj = datetime.strptime(event_end_date, "%Y-%m-%d").date()
            except ValueError:
                flash("Enter valid start and end dates.", "error")
                return redirect(url_for("camp.dashboard"))

            if end_date_obj < start_date_obj:
                flash("End date must be on or after start date.", "error")
                return redirect(url_for("camp.dashboard"))

            normalized_groups = sorted({bg for bg in required_groups if bg in BLOOD_GROUPS})
            if not normalized_groups:
                flash("Select at least one required blood group.", "error")
                return redirect(url_for("camp.dashboard"))

            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute("SELECT * FROM blood_camps WHERE id = %s", (camp_id,))
            camp_row = cursor.fetchone()
            if not camp_row or not camp_row.get("approved"):
                cursor.close()
                flash("Your camp is pending admin approval. You can create events again after approval.", "error")
                return redirect(url_for("camp.dashboard"))

            if not _is_camp_profile_complete(camp_row):
                cursor.close()
                flash("Complete the Blood Camp Dashboard details before creating an event.", "error")
                return redirect(url_for("camp.dashboard"))

            cursor.execute(
                """
                INSERT INTO camp_events (
                    camp_id,
                    event_name,
                    location,
                    event_date,
                    event_end_date,
                    target_units,
                    contact_info,
                    organizer_name,
                    camp_phone,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Upcoming')
                """,
                (
                    camp_id,
                    event_name,
                    event_location,
                    event_start_date,
                    event_end_date,
                    target_units,
                    contact_info or None,
                    organizer_name,
                    camp_phone,
                ),
            )
            event_id = cursor.lastrowid

            for blood_group in normalized_groups:
                cursor.execute(
                    """
                    INSERT INTO camp_event_blood_groups (event_id, blood_group)
                    VALUES (%s, %s)
                    """,
                    (event_id, blood_group),
                )

            placeholders = ", ".join(["%s"] * len(normalized_groups))
            cursor.execute(
                f"""
                SELECT id, name, email, phone
                FROM donors
                WHERE approved = TRUE
                                    AND COALESCE(account_suspended, FALSE) = FALSE
                                    AND COALESCE(is_permanently_deferred, FALSE) = FALSE
                                    AND donor_status IN ('Pre-Eligible', 'Medically Cleared')
                                    AND (temporary_deferral_until IS NULL OR temporary_deferral_until <= NOW())
                  AND blood_group IN ({placeholders})
                  AND (address LIKE %s OR %s = '')
                """,
                tuple(normalized_groups + [f"%{event_location}%", event_location]),
            )
            notification_targets = cursor.fetchall()

            cursor.execute("UPDATE blood_camps SET approved = FALSE WHERE id = %s", (camp_id,))

            mysql.connection.commit()
            cursor.close()

            for donor in notification_targets:
                donor_phone = donor.get("phone")
                donor_email = donor.get("email")
                notify_message = (
                    f"New blood camp event '{event_name}' from {event_start_date} to {event_end_date} at {event_location}. "
                    f"Organizer: {organizer_name}. Camp phone: {camp_phone}. Required groups: {', '.join(normalized_groups)}."
                )
                if donor_phone:
                    send_sms_update(donor_phone, notify_message, current_app.config)
                if donor_email:
                    send_email(
                        current_app.config,
                        donor_email,
                        "New Blood Camp Event Available",
                        notify_message,
                    )

            log_activity(
                mysql,
                "camp",
                camp_id,
                "camp_event_created",
                "camp_event",
                event_id,
                f"Event '{event_name}' from {event_start_date} to {event_end_date} at {event_location}; organizer={organizer_name}; camp_phone={camp_phone}",
            )

            flash("Camp event created. Your profile is now pending admin approval.", "success")
            return redirect(url_for("camp.dashboard"))

        flash("Invalid action.", "error")
        return redirect(url_for("camp.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM blood_camps WHERE id = %s", (camp_id,))
    camp_data = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS total FROM camp_events WHERE camp_id = %s", (camp_id,))
    existing_event_count_row = cursor.fetchone() or {"total": 0}
    existing_event_count = int(existing_event_count_row.get("total") or 0)

    # Backward compatibility: older flow set camps to pending right after profile submit.
    # Re-enable camps with no events so they can create their first event before approval.
    if camp_data and not bool(camp_data.get("approved")) and existing_event_count == 0:
        cursor.execute("UPDATE blood_camps SET approved = TRUE WHERE id = %s", (camp_id,))
        mysql.connection.commit()
        camp_data["approved"] = True

    cursor.execute(
        """
        UPDATE camp_events
        SET status = 'Completed'
        WHERE camp_id = %s
          AND event_date < CURDATE()
          AND status = 'Upcoming'
        """,
        (camp_id,),
    )
    mysql.connection.commit()

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
            ce.organizer_name,
            ce.camp_phone,
            ce.status,
            COALESCE(SUM(cer.units_collected), 0) AS units_collected,
            COUNT(cer.id) AS total_registered,
            SUM(CASE WHEN cer.registration_status = 'Donated' THEN 1 ELSE 0 END) AS donated_count,
            ROUND((COALESCE(SUM(cer.units_collected), 0) / NULLIF(ce.target_units, 0)) * 100, 2) AS progress_percent,
            GROUP_CONCAT(DISTINCT cebg.blood_group ORDER BY cebg.blood_group SEPARATOR ', ') AS required_groups
        FROM camp_events ce
        LEFT JOIN camp_event_registrations cer ON cer.event_id = ce.id
        LEFT JOIN camp_event_blood_groups cebg ON cebg.event_id = ce.id
        WHERE ce.camp_id = %s
          AND ce.event_date >= CURDATE()
        GROUP BY ce.id
        ORDER BY ce.event_date ASC, ce.created_at DESC
        """,
        (camp_id,),
    )
    upcoming_events = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            cer.id AS registration_id,
            ce.event_name,
            ce.event_date,
            d.name AS donor_name,
            d.blood_group,
            d.blood_group_verified,
            d.phone,
            cer.preferred_slot,
            cer.registration_status,
            cer.units_collected,
            cer.created_at AS registered_at,
            cer.donated_at
        FROM camp_event_registrations cer
        JOIN camp_events ce ON ce.id = cer.event_id
        JOIN donors d ON d.id = cer.donor_id
        WHERE ce.camp_id = %s
        ORDER BY ce.event_date DESC, cer.created_at DESC
        """,
        (camp_id,),
    )
    registered_donors = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(cer.units_collected), 0) AS total_units,
            COUNT(cer.id) AS total_registered,
            SUM(CASE WHEN cer.registration_status = 'Donated' THEN 1 ELSE 0 END) AS total_donated
        FROM camp_event_registrations cer
        JOIN camp_events ce ON ce.id = cer.event_id
        WHERE ce.camp_id = %s
        """,
        (camp_id,),
    )
    stats_row = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT d.blood_group, COUNT(*) AS total
        FROM camp_event_registrations cer
        JOIN camp_events ce ON ce.id = cer.event_id
        JOIN donors d ON d.id = cer.donor_id
        WHERE ce.camp_id = %s
          AND cer.registration_status = 'Donated'
          AND d.blood_group IS NOT NULL
        GROUP BY d.blood_group
        ORDER BY total DESC, d.blood_group ASC
        LIMIT 1
        """,
        (camp_id,),
    )
    top_group_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            ce.id,
            ce.event_name,
            ce.location,
            ce.event_date,
            ce.event_end_date,
            ce.target_units,
            ce.organizer_name,
            ce.camp_phone,
            ce.status,
            COALESCE(SUM(cer.units_collected), 0) AS units_collected,
            COUNT(cer.id) AS total_registered
        FROM camp_events ce
        LEFT JOIN camp_event_registrations cer ON cer.event_id = ce.id
        WHERE ce.camp_id = %s
          AND ce.event_date < CURDATE()
        GROUP BY ce.id
        ORDER BY ce.event_date DESC
        """,
        (camp_id,),
    )
    past_events = cursor.fetchall()

    cursor.close()

    total_registered = int(stats_row.get("total_registered") or 0)
    total_donated = int(stats_row.get("total_donated") or 0)
    success_rate = round((total_donated / total_registered) * 100, 2) if total_registered else 0

    camp_stats = {
        "total_units": int(stats_row.get("total_units") or 0),
        "total_registered": total_registered,
        "total_donated": total_donated,
        "success_rate": success_rate,
        "most_common_blood_group": (top_group_row or {}).get("blood_group") or "N/A",
    }

    camp_profile_complete = _is_camp_profile_complete(camp_data)
    camp_has_event = (existing_event_count > 0) or bool(upcoming_events or past_events)

    return render_template(
        "camp_dashboard.html",
        camp=camp_data,
        upcoming_events=upcoming_events,
        registered_donors=registered_donors,
        past_events=past_events,
        camp_stats=camp_stats,
        blood_groups=BLOOD_GROUPS,
        camp_profile_complete=camp_profile_complete,
        camp_has_event=camp_has_event,
    )


@camp.route("/registration/<int:registration_id>/complete", methods=["POST"])
@jwt_required(roles=["camp"])
def complete_registration(registration_id):
    user_data = current_user_payload()
    camp_id = int(user_data.get("uid") or user_data.get("sub") or 0)
    units_collected = int(request.form.get("units_collected", "0") or 0)
    blood_group_tested = request.form.get("blood_group_tested", "").strip()

    if units_collected < 1:
        flash("Collected units must be greater than 0.", "error")
        return redirect(url_for("camp.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT
            cer.id,
            cer.donor_id,
            cer.registration_status,
            ce.id AS event_id,
            ce.camp_id,
            ce.event_name,
            d.blood_group,
            d.blood_group_verified,
            d.donor_status,
            d.phone
        FROM camp_event_registrations cer
        JOIN camp_events ce ON ce.id = cer.event_id
        JOIN donors d ON d.id = cer.donor_id
        WHERE cer.id = %s
          AND ce.camp_id = %s
        """,
        (registration_id, camp_id),
    )
    registration_row = cursor.fetchone()

    if not registration_row:
        cursor.close()
        flash("Registration not found.", "error")
        return redirect(url_for("camp.dashboard"))

    if registration_row["registration_status"] == "Donated":
        cursor.close()
        flash("Donation is already marked complete for this registration.", "error")
        return redirect(url_for("camp.dashboard"))

    if (registration_row.get("donor_status") or "").strip() != "Medically Cleared":
        cursor.close()
        flash("Only Medically Cleared donors can complete donation.", "error")
        return redirect(url_for("camp.dashboard"))

    cursor.execute(
        """
        UPDATE camp_event_registrations
        SET registration_status = 'Donated',
            units_collected = %s,
            donated_at = NOW()
        WHERE id = %s
        """,
        (units_collected, registration_id),
    )

    # Determine blood group and verification status
    final_blood_group = registration_row.get("blood_group")
    blood_group_verified = registration_row.get("blood_group_verified") or False
    
    # If camp tested blood group, update donor's blood group and mark as verified
    if blood_group_tested and blood_group_tested in ['A+','A-','B+','B-','AB+','AB-','O+','O-']:
        final_blood_group = blood_group_tested
        blood_group_verified = True
        
        cursor.execute(
            """
            UPDATE donors
            SET donation_status = 'Completed',
                donation_completed_at = NOW(),
                last_donation = CURDATE(),
                units_donated = %s,
                blood_group = %s,
                blood_group_verified = TRUE
            WHERE id = %s
            """,
            (units_collected, final_blood_group, registration_row["donor_id"]),
        )
    else:
        # If blood group was already known and not tested, just mark donation complete
        cursor.execute(
            """
            UPDATE donors
            SET donation_status = 'Completed',
                donation_completed_at = NOW(),
                last_donation = CURDATE(),
                units_donated = %s
            WHERE id = %s
            """,
            (units_collected, registration_row["donor_id"]),
        )

    # Add units to blood inventory if blood group is known and verified
    if final_blood_group and final_blood_group != 'UNKNOWN':
        for _ in range(units_collected):
            cursor.execute(
                """
                INSERT INTO blood_inventory_units (
                    unit_tracking_id,
                    blood_group,
                    collection_source,
                    source_ref_id,
                    collection_date,
                    expiry_date,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    'Camp',
                    %s,
                    CURDATE(),
                    DATE_ADD(CURDATE(), INTERVAL 35 DAY),
                    'Available'
                )
                """,
                (f"CU-{uuid4().hex[:12].upper()}", final_blood_group, registration_row["event_id"]),
            )

        cursor.execute(
            """
            UPDATE blood_inventory_units
            SET status = 'Expired'
            WHERE status IN ('Available', 'Reserved')
              AND expiry_date < CURDATE()
            """
        )
        cursor.execute("UPDATE blood_stock SET units_available = 0")
        cursor.execute(
            """
            UPDATE blood_stock bs
            JOIN (
                SELECT blood_group, COUNT(*) AS available_units
                FROM blood_inventory_units
                WHERE status = 'Available'
                  AND expiry_date >= CURDATE()
                GROUP BY blood_group
            ) inv ON inv.blood_group = bs.blood_group
            SET bs.units_available = inv.available_units
            """
        )

    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "camp",
        camp_id,
        "camp_donation_completed",
        "camp_event_registration",
        registration_id,
        f"Donation completed for registration #{registration_id}: {units_collected} unit(s)",
    )

    flash("Donation marked complete and blood stock updated.", "success")
    return redirect(url_for("camp.dashboard"))


@camp.route("/event/<int:event_id>/report.csv")
@jwt_required(roles=["camp"])
def event_report_csv(event_id):
    user_data = current_user_payload()
    camp_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
                SELECT id, event_name, event_date, event_end_date, location, target_units, organizer_name, camp_phone
        FROM camp_events
        WHERE id = %s
          AND camp_id = %s
        """,
        (event_id, camp_id),
    )
    event_row = cursor.fetchone()
    if not event_row:
        cursor.close()
        flash("Camp event not found.", "error")
        return redirect(url_for("camp.dashboard"))

    cursor.execute(
        """
        SELECT
            d.name AS donor_name,
            d.blood_group,
            cer.preferred_slot,
            cer.registration_status,
            cer.units_collected,
            cer.donated_at
        FROM camp_event_registrations cer
        JOIN donors d ON d.id = cer.donor_id
        WHERE cer.event_id = %s
        ORDER BY cer.created_at ASC
        """,
        (event_id,),
    )
    registration_rows = cursor.fetchall()
    cursor.close()

    buffer = StringIO()
    csv_writer = csv.writer(buffer)
    csv_writer.writerow(["Event", event_row["event_name"]])
    csv_writer.writerow(["Start Date", str(event_row["event_date"])])
    csv_writer.writerow(["End Date", str(event_row.get("event_end_date") or event_row["event_date"])])
    csv_writer.writerow(["Location", event_row["location"]])
    csv_writer.writerow(["Organizer", event_row.get("organizer_name") or "N/A"])
    csv_writer.writerow(["Camp Phone", event_row.get("camp_phone") or "N/A"])
    csv_writer.writerow(["Target Units", event_row["target_units"]])
    csv_writer.writerow([])
    csv_writer.writerow(["Donor Name", "Blood Group", "Preferred Slot", "Status", "Units Collected", "Donated At"])

    for row in registration_rows:
        csv_writer.writerow(
            [
                row.get("donor_name") or "",
                row.get("blood_group") or "",
                row.get("preferred_slot") or "",
                row.get("registration_status") or "",
                row.get("units_collected") or 0,
                row.get("donated_at") or "",
            ]
        )

    output = buffer.getvalue()
    buffer.close()

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=camp_event_{event_id}_summary.csv"},
    )
