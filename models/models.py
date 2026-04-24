from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, flash, has_request_context, redirect, request, session, url_for


ROLE_TABLE_MAP = {
    "admin": "admin",
    "donor": "donors",
    "hospital": "hospitals",
    "camp": "blood_camps",
}

ROLE_DASHBOARD_MAP = {
    "admin": "admin.dashboard",
    "donor": "donor.dashboard",
    "hospital": "hospital.dashboard",
    "camp": "camp.dashboard",
}

SUPPORTED_BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}

DONOR_STATUS_REGISTERED = "Registered"
DONOR_STATUS_PRE_ELIGIBLE = "Pre-Eligible"
DONOR_STATUS_TEMP_DEFERRED = "Temporarily Deferred"
DONOR_STATUS_MEDICALLY_CLEARED = "Medically Cleared"
DONOR_STATUS_PERM_DEFERRED = "Permanently Deferred"

DONOR_NOTIFICATION_ALLOWED_STATUSES = {DONOR_STATUS_PRE_ELIGIBLE, DONOR_STATUS_MEDICALLY_CLEARED}

BLOOD_COMPATIBILITY_MAP = {
    "O-": ["O-"],
    "O+": ["O+", "O-"],
    "A-": ["A-", "O-"],
    "A+": ["A+", "A-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "AB-": ["AB-", "A-", "B-", "O-"],
    "AB+": ["AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"],
}


def is_supported_blood_group(value, allow_unknown=False):
    group = (value or "").strip().upper()
    if allow_unknown and group == "UNKNOWN":
        return True
    return group in SUPPORTED_BLOOD_GROUPS


def create_session_token(user_id, role, email):
    """Create short-lived JWT token used for role-based session handling."""
    payload = {
        "sub": str(user_id),
        "uid": user_id,
        "role": role,
        "email": email,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=8),
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def decode_session_token(token=None):
    """Decode JWT from explicit token, session, or auth cookie; return payload or None if invalid/expired."""
    token = token or session.get("jwt_token")

    if not token and has_request_context():
        auth_cookie_name = current_app.config.get("AUTH_COOKIE_NAME", "donation_auth")
        token = request.cookies.get(auth_cookie_name)

    if not token:
        return None

    try:
        payload = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])

        # Keep session in sync when token is recovered from cookie.
        session["jwt_token"] = token
        session["active_role"] = payload.get("role")
        session["active_uid"] = payload.get("uid") or payload.get("sub")
        session["active_email"] = payload.get("email")
        session.permanent = True

        return payload
    except jwt.InvalidTokenError:
        return None


def jwt_required(roles=None):
    """Protect routes with JWT and optional role checks."""

    def decorator(route_function):
        @wraps(route_function)
        def wrapper(*args, **kwargs):
            payload = decode_session_token()
            if not payload:
                fallback_role = session.get("active_role")
                fallback_uid = session.get("active_uid")
                fallback_email = session.get("active_email")

                if fallback_role and fallback_uid and fallback_email:
                    session["jwt_token"] = create_session_token(fallback_uid, fallback_role, fallback_email)
                    payload = {
                        "role": fallback_role,
                        "uid": fallback_uid,
                        "sub": str(fallback_uid),
                        "email": fallback_email,
                    }
                elif fallback_role:
                    payload = {
                        "role": fallback_role,
                        "uid": fallback_uid,
                        "sub": str(fallback_uid) if fallback_uid else None,
                        "email": fallback_email,
                    }

                if not payload:
                    flash("Please login to continue.", "error")
                    target_role = roles[0] if roles else "admin"
                    return redirect(url_for("auth.login", role=target_role))

            if roles and payload.get("role") not in roles:
                target_role = roles[0] if roles else "admin"
                flash(f"Please login as {target_role} to access that page.", "error")
                return redirect(url_for("auth.login", role=target_role))

            return route_function(*args, **kwargs)

        return wrapper

    return decorator


def current_user_payload():
    """Return current JWT payload for use inside protected routes."""
    payload = decode_session_token()
    if payload:
        return payload

    fallback_role = session.get("active_role")
    fallback_uid = session.get("active_uid")
    fallback_email = session.get("active_email")

    if fallback_role:
        return {
            "role": fallback_role,
            "uid": fallback_uid,
            "sub": str(fallback_uid) if fallback_uid else None,
            "email": fallback_email,
        }

    return {}


def ensure_gmail_verified(email):
    """Ensure email is a Gmail account for donor/hospital/camp auth flow."""
    return bool(email) and email.lower().endswith("@gmail.com")


def validate_donor_rules(age, health_status, fit_confirmation):
    """Validate donor eligibility rules from project requirements."""
    if age < 18 or age > 60:
        return "Age must be between 18 and 60 to donate."
    if not health_status:
        return "Health condition checkbox is required."
    if not fit_confirmation:
        return "You must confirm medical fitness before submission."
    return None


def calculate_alcohol_deferral_until(alcohol_consumed_recently, last_alcohol_consumption_datetime):
    if not alcohol_consumed_recently or not last_alcohol_consumption_datetime:
        return None
    return last_alcohol_consumption_datetime + timedelta(hours=24)


def resolve_donor_access_state(donor_row):
    donor_row = donor_row or {}
    now_utc_naive = datetime.utcnow()

    account_status = (donor_row.get("account_status") or "Registered").strip()
    donor_status = (donor_row.get("donor_status") or DONOR_STATUS_REGISTERED).strip()
    deferral_reason = (donor_row.get("deferral_reason") or "").strip()
    temporary_deferral_until = donor_row.get("temporary_deferral_until")
    is_permanently_deferred = bool(donor_row.get("is_permanently_deferred"))

    if is_permanently_deferred:
        return {
            "status": DONOR_STATUS_PERM_DEFERRED,
            "deferral_reason": deferral_reason or "Permanent Medical Deferral",
            "temporary_deferral_until": None,
            "eligible_for_notifications": False,
            "can_complete_donation": False,
        }

    if temporary_deferral_until and temporary_deferral_until > now_utc_naive:
        return {
            "status": DONOR_STATUS_TEMP_DEFERRED,
            "deferral_reason": deferral_reason or "Alcohol (24h Rule)",
            "temporary_deferral_until": temporary_deferral_until,
            "eligible_for_notifications": False,
            "can_complete_donation": False,
        }

    if account_status == "Registered":
        return {
            "status": DONOR_STATUS_REGISTERED,
            "deferral_reason": "",
            "temporary_deferral_until": None,
            "eligible_for_notifications": False,
            "can_complete_donation": False,
        }

    if donor_status == DONOR_STATUS_MEDICALLY_CLEARED:
        return {
            "status": DONOR_STATUS_MEDICALLY_CLEARED,
            "deferral_reason": "",
            "temporary_deferral_until": None,
            "eligible_for_notifications": True,
            "can_complete_donation": True,
        }

    return {
        "status": DONOR_STATUS_PRE_ELIGIBLE,
        "deferral_reason": "",
        "temporary_deferral_until": None,
        "eligible_for_notifications": True,
        "can_complete_donation": False,
    }


def calculate_required_ml(units):
    """Blood unit rule: 1 unit equals 450ml."""
    return units * 450


def find_matching_donors(mysql, blood_group, location, units_required):
    """Return eligible donors with weighted score using location, recency and rarity factors."""
    rare_group_bonus = 4 if blood_group in {"AB-", "B-", "A-", "O-"} else 0
    cursor = mysql.connection.cursor()
    cursor.execute(
        """
        SELECT
            id,
            name,
            blood_group,
            address,
            phone,
            last_donation,
            COALESCE(
                (
                    SELECT
                        SUM(CASE WHEN dr.response_status = 'Accepted' THEN 1 ELSE 0 END) /
                        NULLIF(COUNT(*), 0)
                    FROM donor_responses dr
                    WHERE dr.donor_id = donors.id
                ),
                0
            ) AS response_rate,
            (
                10
                + CASE WHEN (%s <> '' AND (address LIKE %s OR %s LIKE CONCAT('%%', address, '%%'))) THEN 10 ELSE 0 END
                + CASE
                    WHEN last_donation IS NULL THEN 5
                    WHEN DATEDIFF(CURDATE(), last_donation) >= 90 THEN 5
                    ELSE 0
                  END
                + ROUND(
                    COALESCE(
                        (
                            SELECT
                                SUM(CASE WHEN dr.response_status = 'Accepted' THEN 1 ELSE 0 END) /
                                NULLIF(COUNT(*), 0)
                            FROM donor_responses dr
                            WHERE dr.donor_id = donors.id
                        ),
                        0
                    ) * 10
                )
                + %s
            ) AS match_score
        FROM donors
                WHERE health_status = TRUE
          AND fit_confirmation = TRUE
            AND COALESCE(account_suspended, FALSE) = FALSE
            AND COALESCE(is_permanently_deferred, FALSE) = FALSE
            AND (
                donor_status IN ('Pre-Eligible', 'Medically Cleared')
                OR (donor_status = 'Temporarily Deferred' AND (temporary_deferral_until IS NULL OR temporary_deferral_until <= NOW()))
              )
                    AND (temporary_deferral_until IS NULL OR temporary_deferral_until <= NOW())
          AND blood_group = %s
          AND blood_group != 'UNKNOWN'
          AND blood_group_verified = TRUE
          AND (last_donation IS NULL OR DATEDIFF(CURDATE(), last_donation) >= 90)
          AND ((address LIKE %s OR %s LIKE CONCAT('%%', address, '%%')) OR %s = '')
        ORDER BY
          match_score DESC,
          CASE WHEN last_donation IS NULL THEN 0 ELSE 1 END,
          last_donation ASC,
          id ASC
        LIMIT %s
        """,
        (
            location,
            f"%{location}%",
            location,
            rare_group_bonus,
            blood_group,
            f"%{location}%",
            location,
            location,
            units_required,
        ),
    )
    rows = cursor.fetchall()
    cursor.close()
    return rows


def _fetch_eligible_donors_by_groups(mysql, requested_group, donor_groups, location, limit, excluded_ids=None):
    if not donor_groups or int(limit or 0) <= 0:
        return []

    excluded_ids = excluded_ids or set()
    sanitized_groups = [group for group in donor_groups if group in SUPPORTED_BLOOD_GROUPS]
    if not sanitized_groups:
        return []

    rare_group_bonus = 4 if requested_group in {"AB-", "B-", "A-", "O-"} else 0

    group_placeholders = ", ".join(["%s"] * len(sanitized_groups))
    query_params = [
        location,
        f"%{location}%",
        location,
        rare_group_bonus,
        *sanitized_groups,
        f"%{location}%",
        location,
        location,
    ]

    excluded_clause = ""
    if excluded_ids:
        excluded_placeholders = ", ".join(["%s"] * len(excluded_ids))
        excluded_clause = f" AND id NOT IN ({excluded_placeholders})"
        query_params.extend(list(excluded_ids))

    query_params.append(int(limit))

    cursor = mysql.connection.cursor()
    cursor.execute(
        f"""
        SELECT
            id,
            name,
            blood_group,
            address,
            phone,
            last_donation,
            COALESCE(
                (
                    SELECT
                        SUM(CASE WHEN dr.response_status = 'Accepted' THEN 1 ELSE 0 END) /
                        NULLIF(COUNT(*), 0)
                    FROM donor_responses dr
                    WHERE dr.donor_id = donors.id
                ),
                0
            ) AS response_rate,
            (
                10
                + CASE WHEN (%s <> '' AND (address LIKE %s OR %s LIKE CONCAT('%%', address, '%%'))) THEN 10 ELSE 0 END
                + CASE
                    WHEN last_donation IS NULL THEN 5
                    WHEN DATEDIFF(CURDATE(), last_donation) >= 90 THEN 5
                    ELSE 0
                  END
                + ROUND(
                    COALESCE(
                        (
                            SELECT
                                SUM(CASE WHEN dr.response_status = 'Accepted' THEN 1 ELSE 0 END) /
                                NULLIF(COUNT(*), 0)
                            FROM donor_responses dr
                            WHERE dr.donor_id = donors.id
                        ),
                        0
                    ) * 10
                )
                + %s
            ) AS match_score
        FROM donors
        WHERE COALESCE(approved, TRUE) = TRUE
                    AND COALESCE(account_suspended, FALSE) = FALSE
                    AND COALESCE(is_permanently_deferred, FALSE) = FALSE
          AND health_status = TRUE
          AND fit_confirmation = TRUE
                    AND (
                                donor_status IN ('Pre-Eligible', 'Medically Cleared')
                                OR (donor_status = 'Temporarily Deferred' AND (temporary_deferral_until IS NULL OR temporary_deferral_until <= NOW()))
                            )
                    AND (temporary_deferral_until IS NULL OR temporary_deferral_until <= NOW())
          AND blood_group IN ({group_placeholders})
          AND blood_group != 'UNKNOWN'
          AND blood_group_verified = TRUE
          AND (last_donation IS NULL OR DATEDIFF(CURDATE(), last_donation) >= 90)
          AND ((address LIKE %s OR %s LIKE CONCAT('%%', address, '%%')) OR %s = '')
          {excluded_clause}
        ORDER BY
          match_score DESC,
          CASE WHEN last_donation IS NULL THEN 0 ELSE 1 END,
          last_donation ASC,
          id ASC
        LIMIT %s
        """,
        tuple(query_params),
    )
    rows = cursor.fetchall()
    cursor.close()
    return rows


def find_matching_donors_tiered(
    mysql,
    requested_group,
    location,
    units_required,
    emergency_level="Normal",
    ai_priority_score=0,
    accepted_units=0,
):
    requested_group = (requested_group or "").strip().upper()
    if requested_group not in SUPPORTED_BLOOD_GROUPS:
        return {"notified_donors": [], "activated_tiers": [], "remaining_units": int(units_required or 0)}

    required_units = max(int(units_required or 0), 0)
    remaining_units = max(required_units - max(int(accepted_units or 0), 0), 0)
    if remaining_units <= 0:
        return {"notified_donors": [], "activated_tiers": [], "remaining_units": 0}

    emergency_level = (emergency_level or "Normal").strip().title()
    is_urgent = emergency_level == "Urgent"

    priority_threshold = int(current_app.config.get("DONOR_ROUTING_EMERGENCY_PRIORITY_THRESHOLD", 70) or 70)
    rare_protection_enabled = bool(current_app.config.get("RARE_BLOOD_PROTECTION_ENABLED", True))

    compatible_groups = BLOOD_COMPATIBILITY_MAP.get(requested_group, [requested_group])

    tier_1_groups = [requested_group]
    tier_2_groups = [group for group in compatible_groups if group.endswith("+") and group != requested_group]
    tier_3_groups = [group for group in compatible_groups if group.endswith("-") and group != requested_group]

    if rare_protection_enabled and requested_group != "O-":
        tier_3_groups = [group for group in tier_3_groups if group != "O-"]

    tiers = [
        {"name": "Tier 1", "groups": tier_1_groups},
        {"name": "Tier 2", "groups": tier_2_groups},
        {"name": "Tier 3", "groups": tier_3_groups},
    ]

    notified_donors = []
    activated_tiers = []
    notified_ids = set()

    for tier in tiers:
        if remaining_units <= 0:
            break

        donor_rows = _fetch_eligible_donors_by_groups(
            mysql,
            requested_group=requested_group,
            donor_groups=tier["groups"],
            location=location,
            limit=remaining_units,
            excluded_ids=notified_ids,
        )

        if not donor_rows:
            continue

        activated_tiers.append(tier["name"])
        for row in donor_rows:
            donor_id = row[0]
            notified_ids.add(donor_id)
            notified_donors.append({"tier": tier["name"], "donor": row})

        remaining_units = max(remaining_units - len(donor_rows), 0)

    allow_tier_4 = (
        requested_group != "O-"
        and is_urgent
        and remaining_units > 0
        and int(ai_priority_score or 0) >= priority_threshold
    )
    if allow_tier_4:
        donor_rows = _fetch_eligible_donors_by_groups(
            mysql,
            requested_group=requested_group,
            donor_groups=["O-"],
            location=location,
            limit=remaining_units,
            excluded_ids=notified_ids,
        )
        if donor_rows:
            activated_tiers.append("Tier 4")
            for row in donor_rows:
                donor_id = row[0]
                notified_ids.add(donor_id)
                notified_donors.append({"tier": "Tier 4", "donor": row})
            remaining_units = max(remaining_units - len(donor_rows), 0)

    return {
        "notified_donors": notified_donors,
        "activated_tiers": activated_tiers,
        "remaining_units": remaining_units,
    }


def create_password_reset_token(email, role):
    """Create a short-lived token for password reset links."""
    reset_minutes = int(current_app.config.get("RESET_TOKEN_MINUTES", 30) or 30)
    reset_minutes = max(15, min(30, reset_minutes))
    payload = {
        "email": email,
        "role": role,
        "purpose": "password_reset",
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=reset_minutes),
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def decode_password_reset_token(token):
    """Validate and decode password reset token."""
    try:
        data = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
        if data.get("purpose") != "password_reset":
            return None
        return data
    except jwt.InvalidTokenError:
        return None


def create_login_verification_token(user_id, email, role):
    """Create short-lived token used to confirm login via email link."""
    payload = {
        "uid": user_id,
        "email": email,
        "role": role,
        "purpose": "login_verify",
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def decode_login_verification_token(token):
    """Validate and decode donor login verification token."""
    try:
        data = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
        if data.get("purpose") != "login_verify":
            return None
        return data
    except jwt.InvalidTokenError:
        return None


def log_activity(mysql, actor_role, actor_id, action, entity_type, entity_id=None, details=None):
    """Persist an activity log entry and swallow logging errors safely."""
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            INSERT INTO activity_logs (actor_role, actor_id, action, entity_type, entity_id, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (actor_role, actor_id, action, entity_type, entity_id, details),
        )
        mysql.connection.commit()
        cursor.close()
    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            pass


def log_donor_deferral_event(mysql, donor_id, reason, consumed_at, deferral_until, source="profile_update"):
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            INSERT INTO donor_deferral_events (
                donor_id,
                reason,
                consumed_at,
                deferral_until,
                source
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (donor_id, reason, consumed_at, deferral_until, source),
        )
        mysql.connection.commit()
        cursor.close()
    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            pass


def get_minimum_donation_gap_days(gender):
    """Return minimum days between donations based on donor gender."""
    if isinstance(gender, str):
        gender = gender.strip().lower()
    
    if gender == "female":
        return 120
    elif gender == "male":
        return 90
    else:
        return 90  # default fallback


def check_donation_eligibility(mysql, donor_id):
    """
    Check if a donor is eligible to donate based on gender and last_donation_date.
    Returns (is_eligible: bool, next_eligible_date: date or None, message: str)
    """
    cursor = mysql.connection.cursor()
    cursor.execute(
        """
        SELECT gender, last_donation, temporary_deferral_until
        FROM donors
        WHERE id = %s
        """,
        (donor_id,),
    )
    result = cursor.fetchone()
    cursor.close()

    if not result:
        return False, None, "Donor profile not found."

    gender, last_donation, temporary_deferral_until = result

    now_utc_naive = datetime.utcnow()
    if temporary_deferral_until and temporary_deferral_until > now_utc_naive:
        return (
            False,
            temporary_deferral_until.date(),
            (
                "Temporarily ineligible due to Alcohol (24h Rule). "
                f"Eligible after {temporary_deferral_until.strftime('%B %d, %Y %I:%M %p')} UTC."
            ),
        )

    # If no previous donation, always eligible
    if not last_donation:
        return True, None, ""

    # Calculate minimum gap and next eligible date
    min_gap_days = get_minimum_donation_gap_days(gender)
    next_eligible = last_donation + timedelta(days=min_gap_days)
    today = datetime.now(tz=timezone.utc).date()

    if today >= next_eligible:
        return True, None, ""
    else:
        return False, next_eligible, f"Based on your last donation date, you can donate again on {next_eligible.strftime('%B %d, %Y')}. Thank you for your willingness to save lives."


def get_donor_next_eligible_date(mysql, donor_id):
    """
    Get the next eligible donation date for a donor.
    Returns date or None if eligible now.
    """
    cursor = mysql.connection.cursor()
    cursor.execute(
        """
        SELECT gender, last_donation, temporary_deferral_until
        FROM donors
        WHERE id = %s
        """,
        (donor_id,),
    )
    result = cursor.fetchone()
    cursor.close()

    if not result:
        return None

    gender, last_donation, temporary_deferral_until = result
    today = datetime.now(tz=timezone.utc).date()

    next_eligible_dates = []
    if last_donation:
        min_gap_days = get_minimum_donation_gap_days(gender)
        next_eligible_dates.append(last_donation + timedelta(days=min_gap_days))

    if temporary_deferral_until and temporary_deferral_until > datetime.utcnow():
        next_eligible_dates.append(temporary_deferral_until.date())

    if not next_eligible_dates:
        return None

    next_eligible = max(next_eligible_dates)
    if today >= next_eligible:
        return None
    return next_eligible
