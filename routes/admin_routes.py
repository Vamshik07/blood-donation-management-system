import os
import MySQLdb.cursors
from datetime import date, datetime
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for

from extensions import mysql
from models.models import current_user_payload, find_matching_donors_tiered, jwt_required, log_activity
from services.email_service import send_email
from services.notifications import send_sms_update

admin = Blueprint("admin", __name__, url_prefix="/admin")

LOW_STOCK_THRESHOLD = 5
EMERGENCY_STOCK_THRESHOLD = 2


def _log_request_status(cursor, request_id, status, note, changed_by_role, changed_by_id):
    cursor.execute(
        """
        INSERT INTO request_status_history (request_id, status, note, changed_by_role, changed_by_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (request_id, status, note, changed_by_role, changed_by_id),
    )


def _sync_inventory_and_stock(cursor):
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
        ) i ON i.blood_group = bs.blood_group
        SET bs.units_available = i.available_units
        """
    )


@admin.route("/dashboard")
@jwt_required(roles=["admin"])
def dashboard():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    _sync_inventory_and_stock(cursor)
    mysql.connection.commit()

    cursor.execute(
        """
        SELECT
            id,
            name,
            email,
            blood_group,
            blood_group_verified,
            phone,
            created_at,
            donation_status,
            units_donated,
            account_status,
            donor_status,
            deferral_reason,
            is_permanently_deferred,
            temporary_deferral_until,
            alcohol_consumed_recently
        FROM donors
        ORDER BY created_at DESC, id DESC
        LIMIT 50
        """
    )
    donors = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            d.id,
            d.name,
            d.email,
            d.blood_group,
            d.blood_group_verified,
            d.phone,
            d.created_at AS registered_at,
            d.last_donation,
            d.scheduled_donation_at,
            d.donation_status,
            d.donation_completed_at,
            d.units_donated,
            d.account_status,
            d.donor_status,
            d.deferral_reason,
            d.is_permanently_deferred,
            d.temporary_deferral_until,
            d.alcohol_consumed_recently
        FROM donors d
        ORDER BY COALESCE(d.donation_completed_at, d.created_at) DESC
        LIMIT 20
        """
    )
    approved_donors = cursor.fetchall()

    cursor.execute("SELECT * FROM hospitals WHERE approved = FALSE ORDER BY id DESC")
    hospitals = cursor.fetchall()

    cursor.execute("SELECT * FROM blood_camps WHERE approved = FALSE ORDER BY id DESC")
    camps = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            br.*,
            h.name AS hospital_name,
            d.name AS requester_name,
            COALESCE(h.name, d.name, br.hospital_name_snapshot, 'Requester') AS request_source_name,
            (
                SELECT COUNT(*)
                FROM donor_responses dr
                WHERE dr.request_id = br.id
                  AND dr.response_status = 'Accepted'
            ) AS accepted_donor_count
        FROM blood_requests br
        LEFT JOIN hospitals h ON br.hospital_id = h.id
        LEFT JOIN donors d ON d.id = br.requester_donor_id
        WHERE br.status = 'Pending'
          AND COALESCE(br.admin_approved, FALSE) = FALSE
                ORDER BY br.ai_priority_score DESC, br.emergency DESC, br.created_at DESC
        """
    )
    blood_requests = cursor.fetchall()

    cursor.execute(
        """
          SELECT br.*, h.name AS hospital_name
        FROM blood_requests br
        JOIN hospitals h ON br.hospital_id = h.id
          WHERE br.requester_role = 'hospital'
             AND (
                    (br.status = 'Pending' AND COALESCE(br.admin_approved, FALSE) = TRUE)
                OR br.status = 'Pending Transfer'
                OR br.status = 'Approved'
             )
        ORDER BY br.emergency DESC, br.created_at DESC
        """
    )
    transfer_requests = cursor.fetchall()

    cursor.execute(
        """
        SELECT br.*, h.name AS hospital_name
        FROM blood_requests br
        JOIN hospitals h ON br.hospital_id = h.id
                WHERE br.requester_role = 'hospital'
                    AND br.status = 'Transferred'
        ORDER BY COALESCE(br.transferred_at, br.created_at) DESC
        """
    )
    transferred_requests = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS total FROM donors")
    total_donors = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM blood_requests br
        JOIN hospitals h ON br.hospital_id = h.id
                WHERE br.status = 'Pending'
                    AND COALESCE(br.admin_approved, FALSE) = FALSE
        """
    )
    total_requests = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM blood_camps WHERE approved = TRUE")
    approved_camps = cursor.fetchone()["total"]

    cursor.execute("SELECT blood_group, COUNT(*) AS total FROM donors WHERE blood_group IS NOT NULL GROUP BY blood_group")
    blood_distribution = cursor.fetchall()
    
    # Blood group verification metrics
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM donors
        WHERE blood_group_verified = TRUE
          AND blood_group IS NOT NULL
          AND blood_group != 'UNKNOWN'
        """
    )
    verified_donors = cursor.fetchone()["total"]
    
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM donors
        WHERE (blood_group_verified = FALSE OR blood_group_verified IS NULL)
          AND blood_group IS NOT NULL
          AND blood_group != 'UNKNOWN'
        """
    )
    unverified_donors = cursor.fetchone()["total"]
    
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM donors
        WHERE blood_group = 'UNKNOWN' OR blood_group IS NULL
        """
    )
    unknown_blood_group_donors = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT blood_group, units_available
        FROM blood_stock
        ORDER BY blood_group ASC
        """
    )
    blood_stock = cursor.fetchall()

    cursor.execute(
        """
        SELECT blood_group, units_available
        FROM blood_stock
        WHERE units_available < %s
        ORDER BY units_available ASC, blood_group ASC
        """,
        (LOW_STOCK_THRESHOLD,),
    )
    low_stock_alerts = cursor.fetchall()

    cursor.execute(
        """
        SELECT blood_group, units_available
        FROM blood_stock
        WHERE units_available < %s
        ORDER BY units_available ASC, blood_group ASC
        """,
        (EMERGENCY_STOCK_THRESHOLD,),
    )
    emergency_stock_alerts = cursor.fetchall()

    cursor.execute(
        """
        SELECT DATE_FORMAT(collection_date, '%%Y-%%m') AS period, COUNT(*) AS total_units
        FROM blood_inventory_units
        GROUP BY DATE_FORMAT(collection_date, '%%Y-%%m')
        ORDER BY period ASC
        """
    )
    monthly_units = cursor.fetchall()

    cursor.execute(
        """
        SELECT blood_group, COUNT(*) AS total
        FROM blood_inventory_units
        GROUP BY blood_group
        ORDER BY blood_group ASC
        """
    )
    inventory_distribution = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN emergency = TRUE THEN 1 ELSE 0 END) AS emergency_total,
            SUM(CASE WHEN emergency = FALSE THEN 1 ELSE 0 END) AS normal_total
        FROM blood_requests
        """
    )
    emergency_split = cursor.fetchone() or {"emergency_total": 0, "normal_total": 0}

    cursor.execute(
        """
        SELECT
            bc.camp_name,
            COALESCE(SUM(cer.units_collected), 0) AS units_collected
        FROM blood_camps bc
        JOIN camp_events ce ON ce.camp_id = bc.id
        LEFT JOIN camp_event_registrations cer ON cer.event_id = ce.id AND cer.registration_status = 'Donated'
        GROUP BY bc.id, bc.camp_name
        ORDER BY units_collected DESC, bc.camp_name ASC
        LIMIT 1
        """
    )
    most_active_camp = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN action = 'Approved' THEN 1 ELSE 0 END) AS approved_count,
            SUM(CASE WHEN action = 'Rejected' THEN 1 ELSE 0 END) AS rejected_count
        FROM approvals
        WHERE entity_type IN ('hospital', 'camp')
        """
    )
    approval_row = cursor.fetchone() or {"approved_count": 0, "rejected_count": 0}
    approved_count = int(approval_row.get("approved_count") or 0)
    rejected_count = int(approval_row.get("rejected_count") or 0)
    approval_rate = round((approved_count / (approved_count + rejected_count)) * 100, 2) if (approved_count + rejected_count) else 0

    cursor.execute(
        """
        SELECT actor_role, action, entity_type, details, created_at
        FROM activity_logs
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )
    admin_logs = cursor.fetchall()

    # Fetch pending admin camp donations (donors with selected dates)
    cursor.execute(
        """
        SELECT 
            acd.id,
            acd.donor_id,
            d.name AS donor_name,
            d.email AS donor_email,
            d.phone AS donor_phone,
            d.blood_group,
            acd.selected_donation_date,
            acd.status,
            acd.donated_at,
            acd.created_at
        FROM admin_camp_donations acd
        JOIN donors d ON acd.donor_id = d.id
        WHERE acd.status IN ('Pending', 'Missed')
        ORDER BY acd.selected_donation_date ASC, acd.created_at ASC
        LIMIT 50
        """
    )
    admin_camp_donations = cursor.fetchall()

    cursor.close()

    return render_template(
        "admin_dashboard.html",
        donors=donors,
        approved_donors=approved_donors,
        hospitals=hospitals,
        camps=camps,
        blood_requests=blood_requests,
        transfer_requests=transfer_requests,
        transferred_requests=transferred_requests,
        admin_logs=admin_logs,
        admin_camp_donations=admin_camp_donations,
        today_date=date.today(),
        current_time=datetime.utcnow(),
        total_donors=total_donors,
        total_requests=total_requests,
        approved_camps=approved_camps,
        blood_distribution=blood_distribution,
        blood_stock=blood_stock,
        low_stock_alerts=low_stock_alerts,
        emergency_stock_alerts=emergency_stock_alerts,
        monthly_units=monthly_units,
        inventory_distribution=inventory_distribution,
        emergency_split=emergency_split,
        most_active_camp=most_active_camp,
        approval_rate=approval_rate,
        verified_donors=verified_donors,
        unverified_donors=unverified_donors,
        unknown_blood_group_donors=unknown_blood_group_donors,
    )


@admin.route("/api/command-center")
@jwt_required(roles=["admin"])
def command_center_api():
    """Live admin command center feed for emergency queue, fraud checks, and quick actions."""
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        _sync_inventory_and_stock(cursor)
        mysql.connection.commit()

        cursor.execute(
            """
            SELECT
                br.id,
                br.patient_name,
                br.blood_group,
                br.units_required,
                br.required_ml,
                br.status,
                br.admin_approved,
                br.emergency,
                br.emergency_level,
                br.ai_priority_score,
                br.fraud_risk_score,
                br.fraud_flags,
                br.created_at,
                br.requester_role,
                COALESCE(h.name, d.name, br.hospital_name_snapshot, 'Requester') AS request_source_name,
                (
                    SELECT COUNT(*)
                    FROM donor_responses dr
                    WHERE dr.request_id = br.id
                      AND dr.response_status = 'Accepted'
                ) AS accepted_donor_count
            FROM blood_requests br
            LEFT JOIN hospitals h ON br.hospital_id = h.id
            LEFT JOIN donors d ON d.id = br.requester_donor_id
            WHERE br.status IN ('Pending', 'Approved', 'Pending Transfer')
            ORDER BY br.emergency DESC, br.ai_priority_score DESC, br.created_at DESC
            LIMIT 30
            """
        )
        queue_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT blood_group, units_available
            FROM blood_stock
            WHERE units_available < %s
            ORDER BY units_available ASC, blood_group ASC
            """,
            (EMERGENCY_STOCK_THRESHOLD,),
        )
        emergency_stock_alerts = cursor.fetchall()

        queue = []
        for row in queue_rows:
            request_id = int(row.get("id") or 0)
            status = (row.get("status") or "").strip()
            can_transfer = status in ("Pending Transfer", "Approved") or (status == "Pending" and bool(row.get("admin_approved")))

            queue.append(
                {
                    "id": request_id,
                    "request_source_name": row.get("request_source_name") or "Requester",
                    "requester_role": row.get("requester_role") or "hospital",
                    "patient_name": row.get("patient_name") or "N/A",
                    "blood_group": row.get("blood_group") or "N/A",
                    "units_required": int(row.get("units_required") or 0),
                    "required_ml": int(row.get("required_ml") or 0),
                    "status": status or "Pending",
                    "emergency": bool(row.get("emergency")),
                    "emergency_level": row.get("emergency_level") or ("Urgent" if row.get("emergency") else "Normal"),
                    "ai_priority_score": int(row.get("ai_priority_score") or 0),
                    "fraud_risk_score": int(row.get("fraud_risk_score") or 0),
                    "fraud_flags": row.get("fraud_flags") or "",
                    "accepted_donor_count": int(row.get("accepted_donor_count") or 0),
                    "created_at": str(row.get("created_at") or ""),
                    "approve_url": url_for("admin.approve_blood_request", request_id=request_id),
                    "reject_url": url_for("admin.reject_blood_request", request_id=request_id),
                    "transfer_url": url_for("admin.transfer_blood_request", request_id=request_id),
                    "can_transfer": can_transfer,
                }
            )

        return jsonify(
            {
                "queue": queue,
                "emergency_stock_alerts": emergency_stock_alerts,
                "refreshed_at": str(date.today()),
            }
        )
    except Exception:
        # Prevent dashboard polling from crashing the page when schema drift exists.
        current_app.logger.exception("Failed to build admin command center payload")
        return jsonify(
            {
                "queue": [],
                "emergency_stock_alerts": [],
                "refreshed_at": str(date.today()),
                "error": "Command center temporarily unavailable",
            }
        )
    finally:
        cursor.close()


@admin.route("/blood-request/<int:request_id>/approve")
@jwt_required(roles=["admin"])
def approve_blood_request(request_id):
    user_data = current_user_payload()
    admin_id = int(user_data.get("uid") or user_data.get("sub") or 1)
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM blood_requests WHERE id = %s", (request_id,))
    blood_request = cursor.fetchone()

    if not blood_request:
        cursor.close()
        flash("Blood request not found.", "error")
        return redirect(url_for("admin.dashboard"))

    requester_role = (blood_request.get("requester_role") or "hospital").strip().lower()
    next_status = "Pending Transfer" if requester_role == "hospital" else "Approved"

    cursor.execute(
        "UPDATE blood_requests SET status = %s, admin_approved = TRUE WHERE id = %s",
        (next_status, request_id),
    )
    _log_request_status(
        cursor,
        request_id,
        "Approved",
        f"Request approved and moved to {next_status}.",
        "admin",
        admin_id,
    )

    requester_phone = None
    requester_email = None
    if requester_role == "hospital" and blood_request.get("hospital_id"):
        cursor.execute("SELECT phone, email FROM hospitals WHERE id = %s", (blood_request["hospital_id"],))
        requester_row = cursor.fetchone() or {}
        requester_phone = requester_row.get("phone")
        requester_email = requester_row.get("email")
    elif requester_role == "user" and blood_request.get("requester_donor_id"):
        cursor.execute("SELECT phone, email FROM donors WHERE id = %s", (blood_request["requester_donor_id"],))
        requester_row = cursor.fetchone() or {}
        requester_phone = requester_row.get("phone")
        requester_email = requester_row.get("email")

    target_location = (blood_request.get("hospital_location_snapshot") or blood_request.get("hospital_address") or "").strip()
    emergency_level = blood_request.get("emergency_level") or ("Urgent" if blood_request.get("emergency") else "Normal")
    ai_priority_score = int(blood_request.get("ai_priority_score") or 0)

    cursor.execute(
        """
        SELECT COUNT(*) AS accepted_total
        FROM donor_responses
        WHERE request_id = %s
          AND response_status = 'Accepted'
        """,
        (request_id,),
    )
    accepted_count_row = cursor.fetchone() or {}
    accepted_count = int(accepted_count_row.get("accepted_total") or 0)

    tiered_routing = find_matching_donors_tiered(
        mysql,
        requested_group=blood_request["blood_group"],
        location=target_location,
        units_required=int(blood_request.get("units_required") or 0),
        emergency_level=emergency_level,
        ai_priority_score=ai_priority_score,
        accepted_units=accepted_count,
    )
    routed_donors = tiered_routing.get("notified_donors", [])
    activated_tiers = tiered_routing.get("activated_tiers", [])
    remaining_units = int(tiered_routing.get("remaining_units") or 0)

    donor_alert = (
        f"Blood needed: {blood_request['blood_group']} for {blood_request.get('patient_name') or 'patient'} | "
        f"Location: {blood_request.get('hospital_location_snapshot') or blood_request.get('hospital_address') or 'N/A'} | "
        f"Emergency: {emergency_level} | Contact: {blood_request.get('contact_number') or 'N/A'}"
    )

    for routed_row in routed_donors:
        donor_row = routed_row["donor"]
        tier_name = routed_row["tier"]
        donor_id = donor_row[0]
        donor_phone = donor_row[4]
        tiered_alert = f"[{tier_name}] {donor_alert}"
        cursor.execute(
            """
            INSERT INTO notifications (user_id, user_role, message, type, related_request_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (donor_id, "donor", tiered_alert, "blood_request_alert", request_id),
        )
        if donor_phone:
            send_sms_update(donor_phone, tiered_alert, current_app.config)

        cursor.execute("SELECT email FROM donors WHERE id = %s", (donor_id,))
        donor_email_row = cursor.fetchone() or {}
        donor_email = donor_email_row.get("email")
        if donor_email:
            send_email(
                current_app.config,
                donor_email,
                f"{tier_name} Blood Request #{request_id}",
                tiered_alert,
            )

    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "admin",
        admin_id,
        "blood_request_approved",
        "blood_request",
        request_id,
        (
            f"Approved blood request #{request_id}; status={next_status}; "
            f"notified_donors={len(routed_donors)}; activated_tiers={','.join(activated_tiers) or 'None'}; "
            f"remaining_units={remaining_units}"
        ),
    )

    for tier_name in activated_tiers:
        log_activity(
            mysql,
            "admin",
            admin_id,
            "donor_routing_tier_activated",
            "blood_request",
            request_id,
            f"Activated {tier_name} for request #{request_id}",
        )

    if requester_phone:
        sms_message = (
            f"Blood request #{request_id} approved and marked {next_status} for "
            f"{blood_request['units_required']} unit(s) of {blood_request['blood_group']}."
        )
        send_sms_update(requester_phone, sms_message, current_app.config)

    if requester_email and requester_role == "user":
        send_email(
            current_app.config,
            requester_email,
            f"Blood Request #{request_id} Approved",
            (
                f"Your request for {blood_request['units_required']} unit(s) of {blood_request['blood_group']} "
                f"has been approved. Current status: {next_status}."
            ),
        )

    tier_text = ", ".join(activated_tiers) if activated_tiers else "No tiers"
    flash(
        (
            f"Blood request approved. Status is now {next_status}. "
            f"Donors notified via: {tier_text}. Remaining units to fulfill: {remaining_units}."
        ),
        "success",
    )
    return redirect(url_for("admin.dashboard"))


@admin.route("/blood-request/<int:request_id>/transfer", methods=["POST"])
@jwt_required(roles=["admin"])
def transfer_blood_request(request_id):
    user_data = current_user_payload()
    admin_id = int(user_data.get("uid") or user_data.get("sub") or 1)
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    _sync_inventory_and_stock(cursor)

    cursor.execute("SELECT * FROM blood_requests WHERE id = %s", (request_id,))
    blood_request = cursor.fetchone()

    if not blood_request:
        cursor.close()
        flash("Blood request not found.", "error")
        return redirect(url_for("admin.dashboard"))

    if blood_request["status"] == "Transferred":
        cursor.close()
        flash("Blood request is already marked as transferred.", "error")
        return redirect(url_for("admin.dashboard"))

    is_approved_for_transfer = bool(blood_request.get("admin_approved")) or blood_request["status"] in (
        "Pending Transfer",
        "Approved",
    )

    if blood_request["status"] != "Pending" and not is_approved_for_transfer:
        cursor.close()
        flash("Only approved blood requests can be transferred.", "error")
        return redirect(url_for("admin.dashboard"))

    required_units = int(blood_request["units_required"] or 0)

    cursor.execute(
        """
        SELECT id, unit_tracking_id
        FROM blood_inventory_units
        WHERE blood_group = %s
          AND status = 'Available'
          AND expiry_date >= CURDATE()
        ORDER BY expiry_date ASC, collection_date ASC, id ASC
        LIMIT %s
        """,
        (blood_request["blood_group"], required_units),
    )
    available_units_rows = cursor.fetchall()
    available_units = len(available_units_rows)

    if available_units < required_units:
        cursor.close()
        flash(
            f"Insufficient stock for {blood_request['blood_group']}. Available: {available_units}, required: {required_units}.",
            "error",
        )
        return redirect(url_for("admin.dashboard"))

    allocated_tracking_ids = [row["unit_tracking_id"] for row in available_units_rows]
    allocated_unit_ids = [row["id"] for row in available_units_rows]
    id_placeholders = ",".join(["%s"] * len(allocated_unit_ids))

    cursor.execute(
        f"""
        UPDATE blood_inventory_units
        SET status = 'Used',
            request_id = %s,
            used_at = NOW()
        WHERE id IN ({id_placeholders})
        """,
        tuple([request_id] + allocated_unit_ids),
    )

    cursor.execute(
        """
        UPDATE blood_stock
        SET units_available = units_available - %s
        WHERE blood_group = %s
        """,
        (required_units, blood_request["blood_group"]),
    )
    cursor.execute(
        """
        UPDATE blood_requests
        SET status = 'Transferred',
            admin_approved = TRUE,
            transferred_units = %s,
            transferred_at = NOW(),
            allocation_details = %s
        WHERE id = %s
        """,
        (required_units, ", ".join(allocated_tracking_ids), request_id),
    )
    _log_request_status(
        cursor,
        request_id,
        "Transferred",
        f"Transferred {required_units} unit(s). Tracking IDs: {', '.join(allocated_tracking_ids)}",
        "admin",
        admin_id,
    )
    cursor.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, action, approved_by, note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            "blood_request",
            request_id,
            "Transferred",
            admin_id,
            f"blood transferred: {required_units} unit(s) of {blood_request['blood_group']}",
        ),
    )

    cursor.execute("SELECT phone FROM hospitals WHERE id = %s", (blood_request["hospital_id"],))
    hospital_row = cursor.fetchone()
    hospital_phone = hospital_row["phone"] if hospital_row else None

    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "admin",
        admin_id,
        "blood_transferred",
        "blood_request",
        request_id,
        f"Transferred {required_units} unit(s) of {blood_request['blood_group']} for request #{request_id}. Units: {', '.join(allocated_tracking_ids)}",
    )

    if hospital_phone:
        sms_message = (
            f"Blood request #{request_id} transferred successfully. "
            f"{required_units} unit(s) of {blood_request['blood_group']} delivered."
        )
        send_sms_update(hospital_phone, sms_message, current_app.config)

    flash(
        f"Blood transferred successfully. Stock updated: {blood_request['blood_group']} -{required_units} unit(s).",
        "success",
    )
    return redirect(url_for("admin.dashboard"))


@admin.route("/blood-request/<int:request_id>/reject")
@jwt_required(roles=["admin"])
def reject_blood_request(request_id):
    user_data = current_user_payload()
    admin_id = int(user_data.get("uid") or user_data.get("sub") or 1)
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT hospital_id, requester_role, requester_donor_id, blood_group, units_required
        FROM blood_requests
        WHERE id = %s
        """,
        (request_id,),
    )
    request_row = cursor.fetchone()

    if not request_row:
        cursor.close()
        flash("Blood request not found.", "error")
        return redirect(url_for("admin.dashboard"))

    cursor.execute(
        "UPDATE blood_requests SET status = 'Rejected' WHERE id = %s",
        (request_id,),
    )
    cursor.execute(
        "UPDATE blood_requests SET admin_approved = FALSE WHERE id = %s",
        (request_id,),
    )
    _log_request_status(
        cursor,
        request_id,
        "Rejected",
        "Request rejected by admin.",
        "admin",
        admin_id,
    )

    requester_phone = None
    requester_email = None
    if request_row.get("requester_role") == "user" and request_row.get("requester_donor_id"):
        cursor.execute("SELECT phone, email FROM donors WHERE id = %s", (request_row["requester_donor_id"],))
        requester_row = cursor.fetchone() or {}
        requester_phone = requester_row.get("phone")
        requester_email = requester_row.get("email")
    elif request_row.get("hospital_id"):
        cursor.execute("SELECT phone, email FROM hospitals WHERE id = %s", (request_row["hospital_id"],))
        requester_row = cursor.fetchone() or {}
        requester_phone = requester_row.get("phone")
        requester_email = requester_row.get("email")

    mysql.connection.commit()
    cursor.close()

    log_activity(
        mysql,
        "admin",
        admin_id,
        "blood_request_rejected",
        "blood_request",
        request_id,
        f"Rejected blood request #{request_id}",
    )

    if requester_phone:
        sms_message = f"Blood request #{request_id} was rejected by admin. Please review and resubmit."
        send_sms_update(requester_phone, sms_message, current_app.config)

    if requester_email and request_row.get("requester_role") == "user":
        send_email(
            current_app.config,
            requester_email,
            f"Blood Request #{request_id} Rejected",
            "Your blood request was rejected by admin. Please review the details and submit again.",
        )

    flash("Blood request rejected.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.route("/blood-request/<int:request_id>/proof")
@jwt_required(roles=["admin"])
def view_request_proof(request_id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT medical_proof_path FROM blood_requests WHERE id = %s", (request_id,))
    row = cursor.fetchone() or {}
    cursor.close()

    proof_path = (row.get("medical_proof_path") or "").strip()
    if not proof_path:
        abort(404)

    normalized = proof_path.replace("\\", "/")
    if not normalized.startswith("medical_proofs/"):
        abort(404)

    filename = normalized.split("/", 1)[1]
    proof_dir = os.path.join(current_app.root_path, "uploads", "medical_proofs")
    return send_from_directory(proof_dir, filename)


@admin.route("/approve/<entity>/<int:entity_id>")
@jwt_required(roles=["admin"])
def approve_entity(entity, entity_id):
    if entity == "donor":
        flash("Donor accounts do not require admin approval.", "error")
        return redirect(url_for("admin.dashboard"))
    return _set_approval(entity, entity_id, True)


@admin.route("/reject/<entity>/<int:entity_id>")
@jwt_required(roles=["admin"])
def reject_entity(entity, entity_id):
    return _set_approval(entity, entity_id, False)


@admin.route("/approve/donor/<int:donor_id>", methods=["POST"])
@jwt_required(roles=["admin"])
def approve_donor_with_schedule(donor_id):
    flash("Donor accounts are auto-activated and do not need admin approval.", "error")
    return redirect(url_for("admin.dashboard"))


@admin.route("/donor/<int:donor_id>/reschedule", methods=["POST"])
@jwt_required(roles=["admin"])
def reschedule_donor(donor_id):
    schedule_raw = (request.form.get("scheduled_donation_at") or "").strip()
    if not schedule_raw:
        flash("Select a new schedule date.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        scheduled_at = date.fromisoformat(schedule_raw)
    except ValueError:
        flash("Invalid schedule date format.", "error")
        return redirect(url_for("admin.dashboard"))

    user_data = current_user_payload()
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT phone FROM donors WHERE id = %s", (donor_id,))
    donor_row = cursor.fetchone()
    if not donor_row:
        cursor.close()
        flash("Donor not found.", "error")
        return redirect(url_for("admin.dashboard"))

    cursor.execute(
        """
        UPDATE donors
        SET scheduled_donation_at = %s,
            donation_status = 'Pending',
            donation_completed_at = NULL
        WHERE id = %s
        """,
        (scheduled_at, donor_id),
    )
    cursor.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, action, approved_by, note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            "donor",
            donor_id,
            "Rescheduled",
            int(user_data.get("uid") or user_data.get("sub") or 1),
            f"donor rescheduled to {scheduled_at.strftime('%Y-%m-%d')}",
        ),
    )
    mysql.connection.commit()
    cursor.close()

    if donor_row.get("phone"):
        send_sms_update(
            donor_row["phone"],
            f"Your donation date was rescheduled to {scheduled_at.strftime('%d-%b-%Y')}",
            current_app.config,
        )

    flash("Donor donation date rescheduled.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.route("/donor/<int:donor_id>/complete", methods=["POST"])
@jwt_required(roles=["admin"])
def complete_donor_donation(donor_id):
    user_data = current_user_payload()
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    
    # Get units_donated from request
    units_donated = request.form.get("units_donated", 0, type=int)
    if units_donated <= 0:
        cursor.close()
        flash("Units donated must be greater than 0.", "error")
        return redirect(url_for("admin.dashboard"))
    
    cursor.execute("SELECT phone, blood_group, donor_status FROM donors WHERE id = %s", (donor_id,))
    donor_row = cursor.fetchone()
    if not donor_row:
        cursor.close()
        flash("Donor not found.", "error")
        return redirect(url_for("admin.dashboard"))

    donor_status = (donor_row.get("donor_status") or "Registered").strip()
    if donor_status != "Medically Cleared":
        cursor.close()
        flash("Only Medically Cleared donors can complete donation.", "error")
        return redirect(url_for("admin.dashboard"))

    cursor.execute(
        """
        UPDATE donors
        SET donation_status = 'Completed',
            donation_completed_at = NOW(),
            last_donation = CURDATE(),
            donor_status = 'Medically Cleared',
            units_donated = %s
        WHERE id = %s
        """,
        (units_donated, donor_id),
    )

    # Create unit-level inventory rows
    blood_group = donor_row.get('blood_group')
    if blood_group:
        for unit_index in range(units_donated):
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
                    'Direct Donor',
                    %s,
                    CURDATE(),
                    DATE_ADD(CURDATE(), INTERVAL 35 DAY),
                    'Available'
                )
                """,
                (f"DU-{uuid4().hex[:12].upper()}", blood_group, donor_id),
            )

        _sync_inventory_and_stock(cursor)
    
    cursor.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, action, approved_by, note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            "donor",
            donor_id,
            "Completed",
            int(user_data.get("uid") or user_data.get("sub") or 1),
            f"donation marked completed by admin - {units_donated} units collected",
        ),
    )
    mysql.connection.commit()
    cursor.close()

    if donor_row.get("phone"):
        send_sms_update(
            donor_row["phone"],
            f"Thank you for donating blood ({units_donated} units). Your donation is marked completed.",
            current_app.config,
        )

    flash(f"Donation marked as completed. Blood stock updated: {blood_group} +{units_donated} units.", "success")
    return redirect(url_for("admin.dashboard"))


def _set_approval(entity, entity_id, approved):
    table_map = {
        "hospital": "hospitals",
        "camp": "blood_camps",
    }

    if entity not in table_map:
        flash("Invalid approval type.", "error")
        return redirect(url_for("admin.dashboard"))

    table_name = table_map[entity]
    action_text = "Approved" if approved else "Rejected"
    user_data = current_user_payload()

    cursor = mysql.connection.cursor()

    contact_phone = None
    if entity == "hospital":
        cursor.execute("SELECT phone FROM hospitals WHERE id = %s", (entity_id,))
        hospital_row = cursor.fetchone()
        contact_phone = hospital_row[0] if hospital_row else None
    elif entity == "camp":
        cursor.execute("SELECT phone FROM blood_camps WHERE id = %s", (entity_id,))
        camp_row = cursor.fetchone()
        contact_phone = camp_row[0] if camp_row else None

    cursor.execute(
        f"UPDATE {table_name} SET approved = %s WHERE id = %s",
        (approved, entity_id),
    )
    cursor.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, action, approved_by, note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            entity,
            entity_id,
            action_text,
            int(user_data.get("uid") or user_data.get("sub") or 1),
            f"{entity} {action_text.lower()} by admin",
        ),
    )
    mysql.connection.commit()
    cursor.close()

    if contact_phone:
        sms_templates = {
            "hospital": (
                "Your hospital profile has been approved. You can now submit active blood requests.",
                "Your hospital profile was rejected. Please update details and resubmit.",
            ),
            "camp": (
                "Your blood camp profile has been approved and is now active.",
                "Your blood camp profile was rejected. Please update details and resubmit.",
            ),
        }

        approved_msg, rejected_msg = sms_templates.get(entity, ("Approved", "Rejected"))
        send_sms_update(contact_phone, approved_msg if approved else rejected_msg, current_app.config)

    flash(f"{entity.title()} request {action_text.lower()}.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.route("/api/metrics")
@jwt_required(roles=["admin"])
def metrics_api():
    """Simple API for dashboard counters."""
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT COUNT(*) AS total FROM donors")
    donors_total = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM blood_requests br
        JOIN hospitals h ON br.hospital_id = h.id
                WHERE br.status = 'Pending'
                    AND COALESCE(br.admin_approved, FALSE) = FALSE
        """
    )
    requests_total = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM blood_camps WHERE approved = TRUE")
    camps_total = cursor.fetchone()["total"]
    cursor.close()

    return jsonify(
        {
            "total_donors": donors_total,
            "total_requests": requests_total,
            "approved_camps": camps_total,
        }
    )


@admin.route("/api/blood-availability")
@jwt_required(roles=["admin"])
def blood_availability_api():
    """API endpoint for real-time approved donor blood-group availability."""
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT blood_group, COUNT(*) AS available_donors
        FROM donors
        WHERE blood_group IS NOT NULL
        GROUP BY blood_group
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    return jsonify(rows)


@admin.route("/admin_camp_donation/<int:donation_id>/mark_complete", methods=["POST"])
@jwt_required(roles=["admin"])
def mark_admin_camp_donation_complete(donation_id):
    """Mark an admin camp donation as complete (donor has donated)."""
    user_data = current_user_payload()
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    units_donated = request.form.get("units_donated", 1, type=int)

    if units_donated <= 0:
        cursor.close()
        flash("Units donated must be greater than 0.", "error")
        return redirect(url_for("admin.dashboard"))

    # Fetch the admin camp donation record
    cursor.execute(
        """
        SELECT acd.id, acd.donor_id, acd.selected_donation_date, d.phone, d.name, d.last_donation, d.blood_group
        FROM admin_camp_donations acd
        JOIN donors d ON acd.donor_id = d.id
        WHERE acd.id = %s AND acd.status = 'Pending'
        """,
        (donation_id,),
    )
    record = cursor.fetchone()

    if not record:
        cursor.close()
        flash("Donation record not found or already completed.", "error")
        return redirect(url_for("admin.dashboard"))

    donor_id = record["donor_id"]

    try:
        # Mark as donated
        cursor.execute(
            """
            UPDATE admin_camp_donations
            SET status = 'Donated', donated_at = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (donation_id,),
        )

        # Update donor's last_donation date
        cursor.execute(
            """
            UPDATE donors
            SET last_donation = CURDATE(),
                donation_status = 'Completed',
                donation_completed_at = NOW(),
                units_donated = COALESCE(units_donated, 0) + %s
            WHERE id = %s
            """,
            (units_donated, donor_id),
        )

        blood_group = (record.get("blood_group") or "").strip().upper()
        if blood_group and blood_group != "UNKNOWN":
            for _ in range(units_donated):
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
                        'Admin Camp',
                        %s,
                        CURDATE(),
                        DATE_ADD(CURDATE(), INTERVAL 35 DAY),
                        'Available'
                    )
                    """,
                    (f"AC-{uuid4().hex[:12].upper()}", blood_group, donation_id),
                )

            _sync_inventory_and_stock(cursor)

        mysql.connection.commit()

        # Send SMS notification
        try:
            send_sms_update(
                record.get("phone"),
                f"Your admin camp donation ({units_donated} unit(s)) has been recorded on {record['selected_donation_date'].strftime('%B %d, %Y')}. Thank you!",
                current_app.config,
            )
        except Exception:
            pass

        log_activity(
            mysql,
            "admin",
            user_data.get("uid"),
            "admin_camp_donation_marked_complete",
            "admin_camp_donation",
            donation_id,
            f"Donor {record['name']} marked as donated ({units_donated} unit(s)) on {record['selected_donation_date']}",
        )

        flash(f"Admin camp donation for {record['name']} marked as complete ({units_donated} unit(s)).", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error marking donation as complete: {str(e)}", "error")
    finally:
        cursor.close()

    return redirect(url_for("admin.dashboard"))


@admin.route("/admin_camp_donation/<int:donation_id>/mark_missed", methods=["POST"])
@jwt_required(roles=["admin"])
def mark_admin_camp_donation_missed(donation_id):
    """Mark admin camp donation as missed (donor didn't show up). Donor can reselect a new date."""
    user_data = current_user_payload()
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    cursor.execute(
        """
        SELECT acd.id, acd.donor_id, d.name
        FROM admin_camp_donations acd
        JOIN donors d ON acd.donor_id = d.id
        WHERE acd.id = %s AND acd.status = 'Pending'
        """,
        (donation_id,),
    )
    record = cursor.fetchone()

    if not record:
        cursor.close()
        flash("Donation record not found.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        # Mark as missed (donor can now reselect a new date)
        cursor.execute(
            """
            UPDATE admin_camp_donations
            SET status = 'Missed', updated_at = NOW()
            WHERE id = %s
            """,
            (donation_id,),
        )
        mysql.connection.commit()

        log_activity(
            mysql,
            "admin",
            user_data.get("uid"),
            "admin_camp_donation_marked_missed",
            "admin_camp_donation",
            donation_id,
            f"Donor {record['name']} marked as missed. Donor can reselect a new date.",
        )

        flash(f"Donation for {record['name']} marked as missed. Donor can reselect a new date.", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error marking donation as missed: {str(e)}", "error")
    finally:
        cursor.close()

    return redirect(url_for("admin.dashboard"))


@admin.route("/api/analytics")
@jwt_required(roles=["admin"])
def analytics_api():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    _sync_inventory_and_stock(cursor)
    mysql.connection.commit()

    cursor.execute(
        """
        SELECT DATE_FORMAT(collection_date, '%%Y-%%m') AS period, COUNT(*) AS total_units
        FROM blood_inventory_units
        GROUP BY DATE_FORMAT(collection_date, '%%Y-%%m')
        ORDER BY period ASC
        """
    )
    monthly_units = cursor.fetchall()

    cursor.execute(
        """
        SELECT blood_group, COUNT(*) AS total
        FROM blood_inventory_units
        GROUP BY blood_group
        ORDER BY blood_group ASC
        """
    )
    distribution = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN emergency = TRUE THEN 1 ELSE 0 END) AS emergency_total,
            SUM(CASE WHEN emergency = FALSE THEN 1 ELSE 0 END) AS normal_total
        FROM blood_requests
        """
    )
    request_mix = cursor.fetchone() or {"emergency_total": 0, "normal_total": 0}

    cursor.execute(
        """
        SELECT blood_group, units_available
        FROM blood_stock
        WHERE units_available < %s
        ORDER BY units_available ASC, blood_group ASC
        """,
        (LOW_STOCK_THRESHOLD,),
    )
    low_stock = cursor.fetchall()
    cursor.close()

    return jsonify(
        {
            "monthly_units": monthly_units,
            "inventory_distribution": distribution,
            "request_mix": request_mix,
            "low_stock_alerts": low_stock,
        }
    )
