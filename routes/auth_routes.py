import MySQLdb.cursors
import logging
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from extensions import bcrypt, limiter, mysql
from models.models import (
    ROLE_DASHBOARD_MAP,
    ROLE_TABLE_MAP,
    calculate_alcohol_deferral_until,
    is_supported_blood_group,
    decode_session_token,
    create_password_reset_token,
    create_login_verification_token,
    create_session_token,
    decode_login_verification_token,
    decode_password_reset_token,
    log_donor_deferral_event,
    validate_donor_rules,
    log_activity,
)
from services.email_service import is_email_configured, send_email

auth = Blueprint("auth", __name__)


def _verify_and_maybe_upgrade_password(role, user, raw_password):
    stored_password = (user.get("password") or "").strip()
    if not stored_password or not raw_password:
        return False

    try:
        if bcrypt.check_password_hash(stored_password, raw_password):
            return True
    except (TypeError, ValueError):
        pass

    if stored_password != raw_password:
        return False

    table_name = "admin" if role == "admin" else ROLE_TABLE_MAP[role]
    upgraded_hash = bcrypt.generate_password_hash(raw_password).decode("utf-8")

    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            f"UPDATE {table_name} SET password = %s WHERE id = %s",
            (upgraded_hash, user["id"]),
        )
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
    finally:
        cursor.close()

    return True


@auth.route("/")
def landing():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        cursor.execute("SELECT COUNT(*) AS total FROM donors")
        total_donors = int((cursor.fetchone() or {}).get("total") or 0)

        cursor.execute("SELECT COUNT(*) AS total FROM blood_requests")
        total_requests = int((cursor.fetchone() or {}).get("total") or 0)

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM camp_events
            WHERE status = 'Upcoming' AND event_date >= CURDATE()
            """
        )
        upcoming_camps = int((cursor.fetchone() or {}).get("total") or 0)

        cursor.execute(
            """
            SELECT
                COALESCE(SUM(COALESCE(units_donated, 0)), 0) AS donor_units,
                COALESCE(
                    (
                        SELECT SUM(COALESCE(transferred_units, units_required, 0))
                        FROM blood_requests
                        WHERE status = 'Transferred'
                    ),
                    0
                ) AS transferred_units
            FROM donors
            """
        )
        impact_row = cursor.fetchone() or {}
        total_units = int(impact_row.get("donor_units") or 0) + int(impact_row.get("transferred_units") or 0)
        lives_saved = total_units * 3
    finally:
        cursor.close()

    return render_template(
        "landing.html",
        total_donors=total_donors,
        total_requests=total_requests,
        upcoming_camps=upcoming_camps,
        lives_saved=lives_saved,
    )


@auth.route("/discover")
def discover():
    return render_template("discover.html")


@auth.route("/login")
def login_redirect():
    return redirect(url_for("auth.discover"))


@auth.route("/portal/<role>")
def portal(role):
    if role not in ["admin", "donor", "hospital", "camp"]:
        return redirect(url_for("auth.discover"))
    return redirect(url_for("auth.login", role=role))


@auth.route("/login/<role>", methods=["GET", "POST"])
def login(role):
    if role not in ["admin", "donor", "hospital", "camp"]:
        return redirect(url_for("auth.discover"))

    existing_payload = decode_session_token()
    if existing_payload and existing_payload.get("role") in ROLE_DASHBOARD_MAP:
        if existing_payload.get("role") == role:
            return redirect(url_for(ROLE_DASHBOARD_MAP[existing_payload["role"]]))

    google_configured = False

    show_resend = False

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        if role == "admin":
            cursor.execute("SELECT id, email, password FROM admin WHERE email = %s", (email,))
            user = cursor.fetchone()
        else:
            table_name = ROLE_TABLE_MAP[role]
            if role == "donor":
                cursor.execute(
                    f"SELECT id, email, password, approved, account_suspended FROM {table_name} WHERE email = %s",
                    (email,),
                )
            else:
                cursor.execute(
                    f"SELECT id, email, password, approved FROM {table_name} WHERE email = %s",
                    (email,),
                )
            user = cursor.fetchone()

        cursor.close()

        if not user:
            if role == "admin":
                flash("No admin account found for this email.", "error")
            else:
                flash(f"No {role} account found for this email. Please register first.", "error")
            return render_template(
                "login.html",
                role=role,
                show_resend=show_resend,
                google_configured=google_configured,
            )

        if not _verify_and_maybe_upgrade_password(role, user, password):
            flash("Incorrect password.", "error")
            return render_template(
                "login.html",
                role=role,
                show_resend=show_resend,
                google_configured=google_configured,
            )

        if role == "donor" and bool(user.get("account_suspended")):
            flash("Your donor account is suspended. Please contact support.", "error")
            return render_template(
                "login.html",
                role=role,
                show_resend=show_resend,
                google_configured=google_configured,
            )

        session.pop("pending_login_verify_uid", None)
        session.pop("pending_login_verify_email", None)
        session.pop("pending_login_verify_role", None)

        session["jwt_token"] = create_session_token(user["id"], role, email)
        session["active_role"] = role
        session["active_uid"] = user["id"]
        session["active_email"] = email
        session.permanent = True

        log_activity(
            mysql,
            role,
            user["id"],
            "login",
            "auth",
            user["id"],
            f"{role} logged in",
        )

        if role == "admin":
            return redirect(url_for("admin.dashboard"))
        if role == "donor":
            return redirect(url_for("donor.dashboard"))
        if role == "hospital":
            return redirect(url_for("hospital.dashboard"))
        return redirect(url_for("camp.dashboard"))

    return render_template(
        "login.html",
        role=role,
        show_resend=show_resend,
        google_configured=google_configured,
    )


@auth.route("/resend-login-verification", methods=["POST"])
def resend_login_verification():
    pending_role = session.get("pending_login_verify_role")
    pending_email = session.get("pending_login_verify_email")
    pending_uid = session.get("pending_login_verify_uid")

    if pending_role != "donor" or not pending_email or not pending_uid:
        flash("No pending donor login verification found.", "error")
        return redirect(url_for("auth.login", role="donor"))

    verification_token = create_login_verification_token(pending_uid, pending_email, pending_role)
    verify_link = url_for("auth.confirm_login", token=verification_token, _external=True)
    mail_sent = send_email(
        current_app.config,
        pending_email,
        "Confirm your donor login",
        "We received a donor login request for your account.\n"
        f"If this was you, click to complete login (valid for 10 minutes):\n{verify_link}",
    )

    if not mail_sent:
        flash("Could not resend verification email. Check MAIL_USERNAME/MAIL_PASSWORD (Gmail App Password).", "error")
    else:
        flash("Verification email resent. Check your inbox.", "success")

    return redirect(url_for("auth.login", role="donor"))


@auth.route("/confirm-login/<token>")
def confirm_login(token):
    token_data = decode_login_verification_token(token)
    if not token_data:
        flash("Login verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login", role="donor"))

    if token_data.get("role") != "donor":
        flash("Login verification link is invalid.", "error")
        return redirect(url_for("auth.login", role="donor"))

    session.pop("pending_login_verify_uid", None)
    session.pop("pending_login_verify_email", None)
    session.pop("pending_login_verify_role", None)

    session["jwt_token"] = create_session_token(
        token_data["uid"], token_data["role"], token_data["email"]
    )
    session["active_role"] = token_data["role"]
    session["active_uid"] = token_data["uid"]
    session["active_email"] = token_data["email"]
    session.permanent = True
    log_activity(
        mysql,
        token_data["role"],
        token_data["uid"],
        "login_confirmed",
        "auth",
        token_data["uid"],
        "Donor login confirmed via email verification",
    )
    flash("Login confirmed successfully.", "success")
    return redirect(url_for("donor.dashboard"))


@auth.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ["donor", "hospital", "camp"]:
        flash("Admin registration is disabled. Use admin login only.", "error")
        return redirect(url_for("auth.login", role="admin"))

    google_configured = False

    if request.method == "GET":
        return render_template("register.html", role=role, google_configured=google_configured)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()
    phone = request.form.get("phone", "").strip()

    if not password or len(password) < 6:
        flash("Password must be at least 6 characters long.", "error")
        return redirect(url_for("auth.register", role=role))

    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.register", role=role))

    if role in ["donor", "camp"]:
        if not phone:
            flash("Mobile number is required for SMS updates.", "error")
            return redirect(url_for("auth.register", role=role))
        if not phone.replace("+", "", 1).isdigit() or len(phone.replace("+", "", 1)) < 10:
            flash("Enter a valid mobile number with at least 10 digits.", "error")
            return redirect(url_for("auth.register", role=role))

    donor_payload = None
    if role == "donor":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Full name is required.", "error")
            return redirect(url_for("auth.register", role=role))

        donor_payload = {
            "name": name,
            "blood_group": "UNKNOWN",
            "age": None,
            "gender": "",
            "address": "",
            "last_donation": None,
            "alcohol_consumed_recently": False,
            "last_alcohol_consumption_datetime": None,
            "temporary_deferral_until": None,
            "health_status": False,
            "fit_confirmation": False,
        }

    hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
    cursor = mysql.connection.cursor()

    try:
        if role == "donor":
            cursor.execute(
                """
                INSERT INTO donors (
                    name,
                    email,
                    password,
                    phone,
                    blood_group,
                    age,
                    gender,
                    address,
                    last_donation,
                    account_status,
                    donor_status,
                    account_suspended,
                    is_permanently_deferred,
                    deferral_reason,
                    alcohol_consumed_recently,
                    last_alcohol_consumption_datetime,
                    temporary_deferral_until,
                    health_status,
                    fit_confirmation,
                    approved,
                    auth_provider,
                    email_verified
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Registered', 'Registered', FALSE, FALSE, NULL, %s, %s, %s, %s, %s, TRUE, 'local', FALSE)
                """,
                (
                    donor_payload["name"],
                    email,
                    hashed_password,
                    phone,
                    donor_payload["blood_group"],
                    donor_payload["age"],
                    donor_payload["gender"],
                    donor_payload["address"],
                    donor_payload["last_donation"],
                    donor_payload["alcohol_consumed_recently"],
                    donor_payload["last_alcohol_consumption_datetime"],
                    donor_payload["temporary_deferral_until"],
                    donor_payload["health_status"],
                    donor_payload["fit_confirmation"],
                ),
            )
            donor_id = cursor.lastrowid
        elif role == "hospital":
            cursor.execute(
                """
                INSERT INTO hospitals (name, email, password, approved, auth_provider, email_verified)
                VALUES (%s, %s, %s, TRUE, 'local', FALSE)
                """,
                ("", email, hashed_password),
            )
        else:
            cursor.execute(
                """
                INSERT INTO blood_camps (camp_name, email, password, phone, approved, auth_provider, email_verified)
                VALUES (%s, %s, %s, %s, TRUE, 'local', FALSE)
                """,
                ("", email, hashed_password, phone),
            )

        mysql.connection.commit()

        if role == "donor":
            log_activity(
                mysql,
                "donor",
                donor_id,
                "donor_registered",
                "donor_profile",
                donor_id,
                "Donor account created with Registered status (progressive profiling pending).",
            )

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("auth.login", role=role))
    except Exception:
        mysql.connection.rollback()
        flash("Account already exists or could not be created.", "error")
        return redirect(url_for("auth.register", role=role))
    finally:
        cursor.close()


@auth.route("/auth/google-login/<role>")
def google_login(role):
    if role not in ["donor", "hospital", "camp"]:
        return redirect(url_for("auth.discover"))

    flash("Google login is disabled. Please use email and password.", "error")
    return redirect(url_for("auth.login", role=role))


@auth.route("/auth/google-callback")
def google_callback():
    flash("Google login is disabled. Please use email and password.", "error")
    return redirect(url_for("auth.discover"))


@auth.route("/logout")
def logout():
    payload = decode_session_token() or {}
    role = payload.get("role") or session.get("active_role") or "user"
    uid = payload.get("uid") or payload.get("sub") or session.get("active_uid")

    if uid:
        log_activity(
            mysql,
            role,
            uid,
            "logout",
            "auth",
            uid,
            f"{role} logged out",
        )

    session.pop("jwt_token", None)
    session.pop("active_role", None)
    session.pop("active_uid", None)
    session.pop("active_email", None)
    session.pop("pending_role", None)
    session.pop("pending_login_verify_uid", None)
    session.pop("pending_login_verify_email", None)
    session.pop("pending_login_verify_role", None)

    flash("You have been logged out.", "success")
    return redirect(url_for("auth.discover"))


@auth.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per 15 minutes; 5 per hour")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "").strip().lower()

        generic_message = (
            "If an account with that email exists, a password reset link has been sent. "
            "Please check your inbox."
        )

        if role in ROLE_TABLE_MAP and email:
            table_name = ROLE_TABLE_MAP[role]
            cursor = mysql.connection.cursor()
            cursor.execute(
                f"SELECT id FROM {table_name} WHERE email = %s",
                (email,),
            )
            user_exists = cursor.fetchone()
            cursor.close()

            if user_exists:
                token = create_password_reset_token(email, role)
                reset_link = url_for("auth.reset_password", token=token, _external=True, _scheme="https")
                reset_minutes = max(15, min(30, int(current_app.config.get("RESET_TOKEN_MINUTES", 30) or 30)))
                html_body = render_template(
                    "emails/password_reset_email.html",
                    reset_link=reset_link,
                    reset_minutes=reset_minutes,
                )

                if is_email_configured(current_app.config):
                    send_email(
                        current_app.config,
                        email,
                        "Reset your password - Let's Save Lives",
                        f"Click this secure link to reset your password (valid for {reset_minutes} minutes):\n{reset_link}",
                        html_body=html_body,
                    )
                else:
                    logging.warning("MAIL config missing; password reset email not sent.")

        flash(generic_message, "success")
        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html")


@auth.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_data = decode_password_reset_token(token)
    if not token_data:
        flash("Reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("reset_password.html", token=token)

        if new_password != confirm_password:
            flash("Password confirmation does not match.", "error")
            return render_template("reset_password.html", token=token)

        email = token_data["email"]
        role = token_data["role"]
        table_name = ROLE_TABLE_MAP[role]
        hashed_password = bcrypt.generate_password_hash(new_password).decode("utf-8")

        cursor = mysql.connection.cursor()
        cursor.execute(
            f"UPDATE {table_name} SET password = %s WHERE email = %s",
            (hashed_password, email),
        )
        mysql.connection.commit()
        cursor.close()

        flash("Password reset successful. You can login now.", "success")
        return redirect(url_for("auth.login", role=role))

    return render_template("reset_password.html", token=token)
