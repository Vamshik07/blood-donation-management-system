from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, flash, redirect, session, url_for


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


def decode_session_token():
    """Decode JWT from session; return payload or None if invalid/expired."""
    token = session.get("jwt_token")
    if not token:
        return None

    try:
        return jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
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


def calculate_required_ml(units):
    """Blood unit rule: 1 unit equals 450ml."""
    return units * 450


def find_matching_donors(mysql, blood_group, location, units_required):
    """Return approved donors with weighted score using location, recency and rarity factors."""
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
            (
                10
                + CASE WHEN (%s <> '' AND (address LIKE %s OR %s LIKE CONCAT('%%', address, '%%'))) THEN 10 ELSE 0 END
                + CASE
                    WHEN last_donation IS NULL THEN 5
                    WHEN DATEDIFF(CURDATE(), last_donation) >= 90 THEN 5
                    ELSE 0
                  END
                + %s
            ) AS match_score
        FROM donors
        WHERE approved = TRUE
          AND health_status = TRUE
          AND fit_confirmation = TRUE
          AND blood_group = %s
          AND blood_group != 'UNKNOWN'
          AND blood_group_verified = TRUE
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
