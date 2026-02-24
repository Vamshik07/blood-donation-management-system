import MySQLdb.cursors
import logging

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from extensions import bcrypt, limiter, mysql, oauth
from models.models import (
    ROLE_DASHBOARD_MAP,
    ROLE_TABLE_MAP,
    decode_session_token,
    create_password_reset_token,
    create_login_verification_token,
    create_session_token,
    decode_login_verification_token,
    decode_password_reset_token,
    ensure_gmail_verified,
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
    return render_template("landing.html")


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

    google_configured = bool(oauth.google)

    show_resend = (
        role == "donor"
        and session.get("pending_login_verify_role") == "donor"
        and bool(session.get("pending_login_verify_email"))
        and bool(session.get("pending_login_verify_uid"))
    )

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        if role == "admin":
            cursor.execute("SELECT id, email, password FROM admin WHERE email = %s", (email,))
            user = cursor.fetchone()
        else:
            table_name = ROLE_TABLE_MAP[role]
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

        if role == "donor" and google_configured:
            verification_token = create_login_verification_token(user["id"], email, role)
            verify_link = url_for("auth.confirm_login", token=verification_token, _external=True)
            mail_sent = send_email(
                current_app.config,
                email,
                "Confirm your donor login",
                "We received a donor login request for your account.\n"
                f"If this was you, click to complete login (valid for 10 minutes):\n{verify_link}",
            )

            if not mail_sent:
                flash("Could not send verification email. Check MAIL_USERNAME/MAIL_PASSWORD (Gmail App Password).", "error")
                return render_template(
                    "login.html",
                    role=role,
                    show_resend=show_resend,
                    google_configured=google_configured,
                )

            session["pending_login_verify_uid"] = user["id"]
            session["pending_login_verify_email"] = email
            session["pending_login_verify_role"] = role

            flash("Verification email sent. Check your inbox to complete login.", "success")
            return render_template(
                "login.html",
                role=role,
                show_resend=True,
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

    google_configured = bool(oauth.google)

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

    hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
    cursor = mysql.connection.cursor()

    try:
        if role == "donor":
            cursor.execute(
                """
                INSERT INTO donors (name, email, password, phone, approved, auth_provider, email_verified)
                VALUES (%s, %s, %s, %s, FALSE, 'local', FALSE)
                """,
                ("", email, hashed_password, phone),
            )
        elif role == "hospital":
            cursor.execute(
                """
                INSERT INTO hospitals (name, email, password, approved, auth_provider, email_verified)
                VALUES (%s, %s, %s, FALSE, 'local', FALSE)
                """,
                ("", email, hashed_password),
            )
        else:
            cursor.execute(
                """
                INSERT INTO blood_camps (camp_name, email, password, phone, approved, auth_provider, email_verified)
                VALUES (%s, %s, %s, %s, FALSE, 'local', FALSE)
                """,
                ("", email, hashed_password, phone),
            )

        mysql.connection.commit()
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

    if not oauth.google:
        flash("Google OAuth is not configured. Add GOOGLE_CLIENT_ID/SECRET in .env", "error")
        return redirect(url_for("auth.login", role=role))

    session["pending_role"] = role
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth.route("/auth/google-callback")
def google_callback():
    pending_role = session.get("pending_role")
    if pending_role not in ["donor", "hospital", "camp"]:
        return redirect(url_for("auth.discover"))

    try:
        oauth.google.authorize_access_token()
        user_info = oauth.google.get("userinfo").json()
    except Exception:
        flash("Google authentication failed.", "error")
        return redirect(url_for("auth.login", role=pending_role))

    email = (user_info.get("email") or "").lower()
    google_sub = (user_info.get("sub") or "").strip()
    email_verified = user_info.get("email_verified", False)

    if not email_verified or not ensure_gmail_verified(email) or not google_sub:
        flash("Only verified Gmail accounts are allowed.", "error")
        return redirect(url_for("auth.login", role=pending_role))

    table_name = ROLE_TABLE_MAP[pending_role]
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        f"""
        SELECT id, email, google_sub
        FROM {table_name}
        WHERE email = %s OR google_sub = %s
        LIMIT 1
        """,
        (email, google_sub),
    )
    existing_user = cursor.fetchone()

    if existing_user:
        existing_sub = (existing_user.get("google_sub") or "").strip()
        existing_email = (existing_user.get("email") or "").strip().lower()
        if existing_sub and existing_sub != google_sub:
            cursor.close()
            flash("Account conflict detected. Contact administrator.", "error")
            return redirect(url_for("auth.login", role=pending_role))

        cursor.execute(
            f"""
            UPDATE {table_name}
            SET google_sub = %s,
                email_verified = TRUE,
                auth_provider = 'google',
                email = %s
            WHERE id = %s
            """,
            (google_sub, existing_email or email, existing_user["id"]),
        )
        user_id = existing_user["id"]
    else:
        if pending_role == "donor":
            cursor.execute(
                """
                INSERT INTO donors (name, email, password, approved, google_sub, email_verified, auth_provider)
                VALUES (%s, %s, %s, TRUE, %s, TRUE, 'google')
                """,
                ("", email, "", google_sub),
            )
        elif pending_role == "hospital":
            cursor.execute(
                """
                INSERT INTO hospitals (name, email, password, approved, google_sub, email_verified, auth_provider)
                VALUES (%s, %s, %s, TRUE, %s, TRUE, 'google')
                """,
                ("", email, "", google_sub),
            )
        else:
            cursor.execute(
                """
                INSERT INTO blood_camps (camp_name, email, password, approved, google_sub, email_verified, auth_provider)
                VALUES (%s, %s, %s, TRUE, %s, TRUE, 'google')
                """,
                ("", email, "", google_sub),
            )
        user_id = cursor.lastrowid

    mysql.connection.commit()
    cursor.close()

    session["jwt_token"] = create_session_token(user_id, pending_role, email)
    session["active_role"] = pending_role
    session["active_uid"] = user_id
    session["active_email"] = email
    session.permanent = True

    log_activity(
        mysql,
        pending_role,
        user_id,
        "google_login",
        "auth",
        user_id,
        f"{pending_role} logged in via Google OAuth",
    )

    return redirect(url_for(ROLE_DASHBOARD_MAP[pending_role]))


@auth.route("/logout")
def logout():
    actor_role = session.get("active_role")
    actor_id = session.get("active_uid")
    if actor_role:
        log_activity(
            mysql,
            actor_role,
            actor_id,
            "logout",
            "auth",
            actor_id,
            f"{actor_role} logged out",
        )
    session.clear()
    return redirect(url_for("auth.landing"))


@auth.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per 15 minutes; 5 per hour")
def forgot_password():
    if request.method == "POST":
        role = request.form.get("role", "").strip()
        email = request.form.get("email", "").strip().lower()
        generic_message = "If the account exists, a password reset link has been sent."

        if role not in ["admin", "donor", "hospital", "camp"] or not email:
            flash(generic_message, "success")
            return redirect(url_for("auth.forgot_password"))

        table_name = ROLE_TABLE_MAP[role]
        cursor = mysql.connection.cursor()
        cursor.execute(f"SELECT id FROM {table_name} WHERE email = %s", (email,))
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
