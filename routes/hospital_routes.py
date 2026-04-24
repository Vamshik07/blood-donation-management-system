import os
from uuid import uuid4

import MySQLdb.cursors
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from extensions import mysql
from models.models import calculate_required_ml, current_user_payload, find_matching_donors, is_supported_blood_group, jwt_required, log_activity
from services.ai_service import calculate_fraud_risk, calculate_priority_score

hospital = Blueprint("hospital", __name__, url_prefix="/hospital")

LOW_STOCK_THRESHOLD = 5
EMERGENCY_STOCK_THRESHOLD = 2
ALLOWED_PROOF_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}


def _is_allowed_proof(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_PROOF_EXTENSIONS


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
                SET name = %s, address = %s, phone = %s
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
                "profile_updated",
                "hospital_profile",
                hospital_id,
                "Hospital profile updated",
            )

            flash("Hospital profile updated successfully.", "success")
            return redirect(url_for("hospital.dashboard"))

        if action == "request":
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute("SELECT * FROM hospitals WHERE id = %s", (hospital_id,))
            hospital_data = cursor.fetchone()
            cursor.close()

            if not hospital_data:
                flash("Hospital account not found.", "error")
                return redirect(url_for("hospital.dashboard"))

            patient_name = request.form.get("patient_name", "").strip()
            blood_group = request.form.get("blood_group", "").strip().upper()
            units_required = int(request.form.get("units_required", "0") or 0)
            emergency_level = request.form.get("emergency_level", "Normal").strip().title()
            emergency_level = "Urgent" if emergency_level == "Urgent" else "Normal"
            emergency = emergency_level == "Urgent"
            hospital_name = request.form.get("hospital_name", "").strip() or (hospital_data.get("name") or "")
            hospital_location = request.form.get("hospital_location", "").strip() or (hospital_data.get("address") or "")
            contact_number = request.form.get("contact_number", "").strip()
            relationship_with_patient = request.form.get("relationship_with_patient", "").strip()
            additional_notes = request.form.get("additional_notes", "").strip()
            medical_proof = request.files.get("medical_proof")

            if not patient_name or not blood_group or not hospital_name or not hospital_location or not contact_number or not relationship_with_patient:
                flash("Please fill all mandatory blood request fields.", "error")
                return redirect(url_for("hospital.dashboard"))

            if not is_supported_blood_group(blood_group):
                flash("Select a valid blood group (A+, A-, B+, B-, AB+, AB-, O+, O-).", "error")
                return redirect(url_for("hospital.dashboard"))

            if not medical_proof or not medical_proof.filename:
                flash("Medical proof document is required.", "error")
                return redirect(url_for("hospital.dashboard"))

            if not _is_allowed_proof(medical_proof.filename):
                flash("Medical proof must be PDF/JPG/JPEG/PNG.", "error")
                return redirect(url_for("hospital.dashboard"))

            if units_required < 1:
                flash("Minimum donation requirement is 1 unit (450ml).", "error")
                return redirect(url_for("hospital.dashboard"))

            required_ml = calculate_required_ml(units_required)

            safe_name = secure_filename(medical_proof.filename)
            unique_filename = f"hospital_req_{hospital_id}_{uuid4().hex}_{safe_name}"
            upload_dir = os.path.join(current_app.root_path, "uploads", "medical_proofs")
            os.makedirs(upload_dir, exist_ok=True)
            saved_file_path = os.path.join(upload_dir, unique_filename)
            medical_proof.save(saved_file_path)

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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending', FALSE)
                """,
                (
                    hospital_id,
                    "hospital",
                    patient_name,
                    blood_group,
                    units_required,
                    required_ml,
                    emergency,
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
                f"Requested {units_required} unit(s) of {blood_group} for patient {patient_name} ({emergency_level})",
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


@hospital.route("/api/emergency-status")
@jwt_required(roles=["hospital"])
def emergency_status_api():
    """Live emergency view for this hospital: urgent requests + accepted donor responses."""
    user_data = current_user_payload()
    hospital_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        """
        SELECT
            br.id,
            br.patient_name,
            br.blood_group,
            br.units_required,
            br.status,
            br.emergency_level,
            br.contact_number,
            br.ai_priority_score,
            (
                SELECT COUNT(*)
                FROM donor_responses dr
                WHERE dr.request_id = br.id
                  AND dr.response_status = 'Accepted'
            ) AS accepted_count,
            (
                SELECT MAX(dr.response_time)
                FROM donor_responses dr
                WHERE dr.request_id = br.id
            ) AS last_response_at
        FROM blood_requests br
        WHERE br.hospital_id = %s
          AND br.emergency = TRUE
          AND br.status IN ('Pending', 'Approved', 'Pending Transfer')
        ORDER BY br.ai_priority_score DESC, br.created_at DESC
        LIMIT 20
        """,
        (hospital_id,),
    )
    emergency_requests = cursor.fetchall()

    response_payload = []
    for req in emergency_requests:
        cursor.execute(
            """
            SELECT
                d.id,
                d.name,
                d.blood_group,
                d.address,
                d.phone,
                dr.response_time
            FROM donor_responses dr
            JOIN donors d ON d.id = dr.donor_id
            WHERE dr.request_id = %s
              AND dr.response_status = 'Accepted'
            ORDER BY dr.response_time ASC
            LIMIT %s
            """,
            (req["id"], max(int(req.get("units_required") or 1), 1)),
        )
        accepted_donors = cursor.fetchall()

        response_payload.append(
            {
                "request_id": req["id"],
                "patient_name": req.get("patient_name"),
                "blood_group": req.get("blood_group"),
                "units_required": int(req.get("units_required") or 0),
                "accepted_count": int(req.get("accepted_count") or 0),
                "status": req.get("status"),
                "emergency_level": req.get("emergency_level") or "Urgent",
                "contact_number": req.get("contact_number"),
                "ai_priority_score": int(req.get("ai_priority_score") or 0),
                "last_response_at": str(req.get("last_response_at") or ""),
                "accepted_donors": accepted_donors,
            }
        )

    cursor.close()
    return jsonify({"requests": response_payload})


@hospital.route("/api/donor-map")
@jwt_required(roles=["hospital"])
def donor_map_api():
    """Map-ready data API: nearby matched donors and nearby hospitals by optional filters."""
    user_data = current_user_payload()
    hospital_id = int(user_data.get("uid") or user_data.get("sub") or 0)

    blood_group = (request.args.get("blood_group") or "").strip().upper()
    location = (request.args.get("location") or "").strip()
    units_required = int(request.args.get("units_required") or 5)
    units_required = min(max(units_required, 1), 25)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    if not location:
        cursor.execute("SELECT address FROM hospitals WHERE id = %s", (hospital_id,))
        row = cursor.fetchone() or {}
        location = (row.get("address") or "").strip()

    matched_donors = []
    if blood_group:
        raw_rows = find_matching_donors(mysql, blood_group, location, units_required)
        for row in raw_rows:
            matched_donors.append(
                {
                    "donor_id": row[0],
                    "name": row[1],
                    "blood_group": row[2],
                    "address": row[3],
                    "phone": row[4],
                    "last_donation": str(row[5] or ""),
                    "response_rate": float(row[6] or 0),
                    "match_score": int(row[7] or 0),
                }
            )

    cursor.execute(
        """
        SELECT id, name, address, phone
        FROM hospitals
        WHERE approved = TRUE
          AND id <> %s
          AND ((address LIKE %s OR %s LIKE CONCAT('%%', address, '%%')) OR %s = '')
        ORDER BY id DESC
        LIMIT 25
        """,
        (hospital_id, f"%{location}%", location, location),
    )
    nearby_hospitals = cursor.fetchall()

    stock = None
    if blood_group:
        cursor.execute(
            "SELECT blood_group, units_available FROM blood_stock WHERE blood_group = %s",
            (blood_group,),
        )
        stock = cursor.fetchone()

    cursor.close()

    return jsonify(
        {
            "filters": {
                "blood_group": blood_group,
                "location": location,
                "units_required": units_required,
            },
            "matched_donors": matched_donors,
            "nearby_hospitals": nearby_hospitals,
            "stock": stock,
        }
    )
