import MySQLdb.cursors
from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import mysql
from models.models import calculate_required_ml, current_user_payload, find_matching_donors, jwt_required, log_activity

hospital = Blueprint("hospital", __name__, url_prefix="/hospital")

LOW_STOCK_THRESHOLD = 5
EMERGENCY_STOCK_THRESHOLD = 2


@hospital.route("/dashboard", methods=["GET", "POST"])
@jwt_required(roles=["hospital"])
def dashboard():
    user_data = current_user_payload()
    hospital_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "profile":
            hospital_name = request.form.get("name", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()

            cursor = mysql.connection.cursor()
            cursor.execute(
                """
                UPDATE hospitals
                SET name = %s, address = %s, phone = %s, approved = FALSE
                WHERE id = %s
                """,
                (hospital_name, address, phone, hospital_id),
            )
            mysql.connection.commit()
            cursor.close()

            log_activity(
                mysql,
                "hospital",
                hospital_id,
                "profile_submitted",
                "hospital_profile",
                hospital_id,
                "Hospital profile submitted for approval",
            )

            flash("Hospital profile submitted for admin approval.", "success")
            return redirect(url_for("hospital.dashboard"))

        if action == "request":
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute("SELECT * FROM hospitals WHERE id = %s", (hospital_id,))
            hospital_data = cursor.fetchone()
            cursor.close()

            if not hospital_data or not hospital_data.get("approved"):
                flash("Hospital must be approved by admin before placing blood requests.", "error")
                return redirect(url_for("hospital.dashboard"))

            patient_name = request.form.get("patient_name", "").strip()
            blood_group = request.form.get("blood_group", "").strip()
            units_required = int(request.form.get("units_required", "0") or 0)
            emergency = request.form.get("emergency") == "on"
            hospital_address = request.form.get("hospital_address", "").strip()
            contact_number = request.form.get("contact_number", "").strip()

            if units_required < 1:
                flash("Minimum donation requirement is 1 unit (450ml).", "error")
                return redirect(url_for("hospital.dashboard"))

            required_ml = calculate_required_ml(units_required)

            cursor = mysql.connection.cursor()
            cursor.execute(
                """
                INSERT INTO blood_requests (
                    hospital_id,
                    patient_name,
                    blood_group,
                    units_required,
                    required_ml,
                    emergency,
                    hospital_address,
                    contact_number,
                    status,
                    admin_approved
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending', FALSE)
                """,
                (
                    hospital_id,
                    patient_name,
                    blood_group,
                    units_required,
                    required_ml,
                    emergency,
                    hospital_address,
                    contact_number,
                ),
            )
            request_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO request_status_history (request_id, status, note, changed_by_role, changed_by_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (request_id, "Submitted", "Hospital request submitted.", "hospital", hospital_id),
            )
            mysql.connection.commit()
            cursor.close()

            log_activity(
                mysql,
                "hospital",
                hospital_id,
                "blood_request_submitted",
                "blood_request",
                None,
                f"Requested {units_required} unit(s) of {blood_group} for patient {patient_name}",
            )

            flash("Blood request submitted. Admin review required.", "success")
            return redirect(url_for("hospital.dashboard"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

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
    mysql.connection.commit()

    cursor.execute("SELECT * FROM hospitals WHERE id = %s", (hospital_id,))
    hospital_data = cursor.fetchone()

    cursor.execute(
        """
        SELECT *
        FROM blood_requests
        WHERE hospital_id = %s
        ORDER BY emergency DESC, created_at DESC
        """,
        (hospital_id,),
    )
    requests = cursor.fetchall()

    for blood_request in requests:
        raw_status = (blood_request.get("status") or "").strip()
        is_admin_approved = bool(blood_request.get("admin_approved"))
        has_transfer_record = bool(blood_request.get("transferred_at")) or int(blood_request.get("transferred_units") or 0) > 0

        if raw_status == "Transferred" or has_transfer_record:
            blood_request["display_status"] = "Transferred"
        elif raw_status == "Rejected":
            blood_request["display_status"] = "Rejected"
        elif raw_status in ("Approved", "Pending Transfer"):
            blood_request["display_status"] = "Pending Transfer"
        elif raw_status == "Pending" and is_admin_approved:
            blood_request["display_status"] = "Pending Transfer"
        else:
            blood_request["display_status"] = "Pending"

    cursor.execute(
        """
        SELECT
            id,
            patient_name,
            blood_group,
            COALESCE(transferred_units, units_required) AS transferred_units,
            transferred_at
        FROM blood_requests
        WHERE hospital_id = %s
          AND (status = 'Transferred' OR transferred_at IS NOT NULL OR COALESCE(transferred_units, 0) > 0)
        ORDER BY transferred_at DESC, id DESC
        """,
        (hospital_id,),
    )
    transfer_history = cursor.fetchall()

    cursor.execute(
        """
        SELECT action, entity_type, details, created_at
        FROM activity_logs
        WHERE actor_role = 'hospital' AND actor_id = %s
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (hospital_id,),
    )
    hospital_logs = cursor.fetchall()

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
        SELECT
            rsh.request_id,
            rsh.status,
            rsh.note,
            rsh.changed_by_role,
            rsh.created_at
        FROM request_status_history rsh
        JOIN blood_requests br ON br.id = rsh.request_id
        WHERE br.hospital_id = %s
        ORDER BY rsh.created_at DESC
        LIMIT 100
        """,
        (hospital_id,),
    )
    request_timeline = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            biu.unit_tracking_id,
            biu.blood_group,
            biu.collection_source,
            biu.collection_date,
            biu.expiry_date,
            biu.used_at,
            br.id AS request_id,
            br.patient_name
        FROM blood_inventory_units biu
        JOIN blood_requests br ON br.id = biu.request_id
        WHERE br.hospital_id = %s
          AND biu.status = 'Used'
        ORDER BY biu.used_at DESC, biu.id DESC
        LIMIT 100
        """,
        (hospital_id,),
    )
    allocation_tracking = cursor.fetchall()
    cursor.close()

    donor_matches = []
    if requests:
        latest_request = requests[0]
        donor_matches = find_matching_donors(
            mysql,
            latest_request["blood_group"],
            latest_request.get("hospital_address") or "",
            latest_request["units_required"],
        )

    return render_template(
        "hospital_dashboard.html",
        hospital=hospital_data,
        requests=requests,
        donor_matches=donor_matches,
        blood_stock=blood_stock,
        low_stock_alerts=low_stock_alerts,
        emergency_stock_alerts=emergency_stock_alerts,
        transfer_history=transfer_history,
        hospital_logs=hospital_logs,
        request_timeline=request_timeline,
        allocation_tracking=allocation_tracking,
    )
