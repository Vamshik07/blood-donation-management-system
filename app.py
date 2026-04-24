from datetime import timedelta

from flask import Flask, request, session

from config import Config
from extensions import bcrypt, limiter, login_manager, mail, mysql, oauth
from models.models import decode_session_token


@login_manager.user_loader
def load_user(_user_id):
    """This project uses JWT/session role auth, so no Flask-Login user model is loaded."""
    return None

def ensure_default_admin(app):
    """Ensure default admin account exists in database."""
    email = (app.config.get("ADMIN_DEFAULT_EMAIL") or "").strip().lower()
    password = app.config.get("ADMIN_DEFAULT_PASSWORD") or ""

    if not email or not password:
        return

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT id FROM admin WHERE email = %s", (email,))
        existing = cursor.fetchone()
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")

        if not existing:
            cursor.execute(
                "INSERT INTO admin (email, password) VALUES (%s, %s)",
                (email, hashed_password),
            )
        else:
            cursor.execute(
                "UPDATE admin SET password = %s WHERE email = %s",
                (hashed_password, email),
            )

        mysql.connection.commit()

        cursor.close()
    except Exception:
        # Schema may not be initialized yet.
        return


def ensure_core_tables():
    """Create required tables if they do not exist."""
    cursor = mysql.connection.cursor()

    def ensure_column(table_name, column_name, column_ddl):
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            """,
            (table_name, column_name),
        )
        exists = cursor.fetchone()[0]
        if not exists:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}")

    def ensure_unique_index(table_name, index_name, columns):
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            """,
            (table_name, index_name),
        )
        exists = cursor.fetchone()[0]
        if not exists:
            cursor.execute(f"ALTER TABLE {table_name} ADD UNIQUE KEY {index_name} ({columns})")

    def ensure_varchar_column(table_name, column_name, target_length, default_value=None):
        cursor.execute(
            """
            SELECT DATA_TYPE, IFNULL(CHARACTER_MAXIMUM_LENGTH, 0)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            """,
            (table_name, column_name),
        )
        column_meta = cursor.fetchone()
        if not column_meta:
            return

        data_type = (column_meta[0] or "").lower()
        max_length = int(column_meta[1] or 0)

        if data_type == "varchar" and max_length >= target_length:
            return

        default_clause = ""
        if default_value is not None:
            safe_default = str(default_value).replace("'", "''")
            default_clause = f" DEFAULT '{safe_default}'"

        cursor.execute(
            f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} VARCHAR({target_length}){default_clause}"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin (
            id INT PRIMARY KEY AUTO_INCREMENT,
            email VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS donors (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
            password VARCHAR(255),
            age INT,
            blood_group VARCHAR(10),
            address TEXT,
            phone VARCHAR(15),
            last_donation DATE,
            account_status VARCHAR(30) DEFAULT 'Registered',
            donor_status VARCHAR(30) DEFAULT 'Registered',
            account_suspended BOOLEAN DEFAULT FALSE,
            is_permanently_deferred BOOLEAN DEFAULT FALSE,
            deferral_reason VARCHAR(255) NULL,
            alcohol_consumed_recently BOOLEAN DEFAULT FALSE,
            last_alcohol_consumption_datetime DATETIME NULL,
            temporary_deferral_until DATETIME NULL,
            health_status BOOLEAN DEFAULT FALSE,
            fit_confirmation BOOLEAN DEFAULT FALSE,
            approved BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS hospitals (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
            password VARCHAR(255),
            address TEXT,
            phone VARCHAR(15),
            approved BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blood_camps (
            id INT PRIMARY KEY AUTO_INCREMENT,
            camp_name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
            password VARCHAR(255),
            phone VARCHAR(15),
            location TEXT,
            days INT,
            slots_per_day INT,
            expected_donors INT,
            approved BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blood_requests (
            id INT PRIMARY KEY AUTO_INCREMENT,
            hospital_id INT,
            requester_donor_id INT NULL,
            requester_role VARCHAR(20) DEFAULT 'hospital',
            patient_name VARCHAR(100),
            blood_group VARCHAR(10),
            units_required INT,
            required_ml INT,
            emergency BOOLEAN,
            emergency_level VARCHAR(20) DEFAULT 'Normal',
            hospital_address TEXT,
            hospital_name_snapshot VARCHAR(120) NULL,
            hospital_location_snapshot VARCHAR(255) NULL,
            contact_number VARCHAR(15),
            relationship_with_patient VARCHAR(100) NULL,
            medical_proof_path VARCHAR(255) NULL,
            additional_notes TEXT NULL,
            status VARCHAR(50) DEFAULT 'Pending',
            admin_approved BOOLEAN DEFAULT FALSE,
            transferred_units INT DEFAULT 0,
            transferred_at DATETIME NULL,
            ai_priority_score INT DEFAULT 0,
            fraud_risk_score INT DEFAULT 0,
            fraud_flags VARCHAR(255) NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id),
            FOREIGN KEY (requester_donor_id) REFERENCES donors(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS donor_responses (
            id INT PRIMARY KEY AUTO_INCREMENT,
            request_id INT NOT NULL,
            donor_id INT NOT NULL,
            response_status VARCHAR(20) NOT NULL,
            response_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_request_donor_response (request_id, donor_id),
            FOREIGN KEY (request_id) REFERENCES blood_requests(id) ON DELETE CASCADE,
            FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT NOT NULL,
            user_role VARCHAR(20) NOT NULL,
            message VARCHAR(500) NOT NULL,
            type VARCHAR(40) DEFAULT 'info',
            related_request_id INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_notifications_target (user_role, user_id, created_at),
            FOREIGN KEY (related_request_id) REFERENCES blood_requests(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS camp_events (
            id INT PRIMARY KEY AUTO_INCREMENT,
            camp_id INT NOT NULL,
            event_name VARCHAR(120) NOT NULL,
            location VARCHAR(255) NOT NULL,
            event_date DATE NOT NULL,
            event_end_date DATE NULL,
            target_units INT NOT NULL,
            contact_info VARCHAR(100),
            organizer_name VARCHAR(120) NULL,
            camp_phone VARCHAR(20) NULL,
            status VARCHAR(20) DEFAULT 'Upcoming',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (camp_id) REFERENCES blood_camps(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS camp_event_blood_groups (
            id INT PRIMARY KEY AUTO_INCREMENT,
            event_id INT NOT NULL,
            blood_group VARCHAR(10) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_event_group (event_id, blood_group),
            FOREIGN KEY (event_id) REFERENCES camp_events(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS camp_event_registrations (
            id INT PRIMARY KEY AUTO_INCREMENT,
            event_id INT NOT NULL,
            donor_id INT NOT NULL,
            preferred_slot VARCHAR(50) NOT NULL,
            registration_status VARCHAR(20) DEFAULT 'Registered',
            units_collected INT DEFAULT 0,
            donated_at DATETIME NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_event_donor (event_id, donor_id),
            FOREIGN KEY (event_id) REFERENCES camp_events(id) ON DELETE CASCADE,
            FOREIGN KEY (donor_id) REFERENCES donors(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blood_inventory_units (
            id INT PRIMARY KEY AUTO_INCREMENT,
            unit_tracking_id VARCHAR(40) UNIQUE NOT NULL,
            blood_group VARCHAR(10) NOT NULL,
            collection_source VARCHAR(20) NOT NULL,
            source_ref_id INT NULL,
            collection_date DATE NOT NULL,
            expiry_date DATE NOT NULL,
            status VARCHAR(20) DEFAULT 'Available',
            request_id INT NULL,
            reserved_at DATETIME NULL,
            used_at DATETIME NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_inventory_group_status (blood_group, status, expiry_date),
            FOREIGN KEY (request_id) REFERENCES blood_requests(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS request_status_history (
            id INT PRIMARY KEY AUTO_INCREMENT,
            request_id INT NOT NULL,
            status VARCHAR(40) NOT NULL,
            note VARCHAR(255),
            changed_by_role VARCHAR(20) NOT NULL,
            changed_by_id INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (request_id) REFERENCES blood_requests(id)
        )
        """
    )

    # Upgrade existing legacy schema in-place.
    ensure_column("donors", "scheduled_donation_at", "DATE NULL")
    ensure_column("donors", "donation_status", "VARCHAR(20) DEFAULT 'Pending'")
    ensure_column("donors", "donation_completed_at", "DATETIME NULL")
    ensure_column("donors", "units_donated", "INT DEFAULT 0")
    ensure_column("donors", "google_sub", "VARCHAR(100) NULL")
    ensure_column("donors", "email_verified", "BOOLEAN DEFAULT FALSE")
    ensure_column("donors", "auth_provider", "VARCHAR(20) DEFAULT 'google'")
    ensure_column("donors", "blood_group_verified", "BOOLEAN DEFAULT FALSE")
    ensure_column("donors", "gender", "VARCHAR(20) NULL")
    ensure_column("donors", "account_status", "VARCHAR(30) DEFAULT 'Registered'")
    ensure_column("donors", "donor_status", "VARCHAR(30) DEFAULT 'Registered'")
    ensure_column("donors", "account_suspended", "BOOLEAN DEFAULT FALSE")
    ensure_column("donors", "is_permanently_deferred", "BOOLEAN DEFAULT FALSE")
    ensure_column("donors", "deferral_reason", "VARCHAR(255) NULL")
    ensure_column("donors", "alcohol_consumed_recently", "BOOLEAN DEFAULT FALSE")
    ensure_column("donors", "last_alcohol_consumption_datetime", "DATETIME NULL")
    ensure_column("donors", "temporary_deferral_until", "DATETIME NULL")
    ensure_unique_index("donors", "uq_donors_google_sub", "google_sub")

    ensure_column("hospitals", "google_sub", "VARCHAR(100) NULL")
    ensure_column("hospitals", "email_verified", "BOOLEAN DEFAULT FALSE")
    ensure_column("hospitals", "auth_provider", "VARCHAR(20) DEFAULT 'google'")
    ensure_unique_index("hospitals", "uq_hospitals_google_sub", "google_sub")

    ensure_column("blood_camps", "google_sub", "VARCHAR(100) NULL")
    ensure_column("blood_camps", "email_verified", "BOOLEAN DEFAULT FALSE")
    ensure_column("blood_camps", "auth_provider", "VARCHAR(20) DEFAULT 'google'")
    ensure_unique_index("blood_camps", "uq_camps_google_sub", "google_sub")

    ensure_column("blood_requests", "hospital_id", "INT NULL")
    ensure_column("blood_requests", "patient_name", "VARCHAR(100) NULL")
    ensure_column("blood_requests", "blood_group", "VARCHAR(10) NULL")
    ensure_column("blood_requests", "units_required", "INT NULL")
    ensure_column("blood_requests", "required_ml", "INT NULL")
    ensure_column("blood_requests", "emergency", "BOOLEAN DEFAULT FALSE")
    ensure_column("blood_requests", "requester_donor_id", "INT NULL")
    ensure_column("blood_requests", "requester_role", "VARCHAR(20) DEFAULT 'hospital'")
    ensure_column("blood_requests", "emergency_level", "VARCHAR(20) DEFAULT 'Normal'")
    ensure_column("blood_requests", "hospital_address", "TEXT NULL")
    ensure_column("blood_requests", "hospital_name_snapshot", "VARCHAR(120) NULL")
    ensure_column("blood_requests", "hospital_location_snapshot", "VARCHAR(255) NULL")
    ensure_column("blood_requests", "contact_number", "VARCHAR(15) NULL")
    ensure_column("blood_requests", "relationship_with_patient", "VARCHAR(100) NULL")
    ensure_column("blood_requests", "medical_proof_path", "VARCHAR(255) NULL")
    ensure_column("blood_requests", "additional_notes", "TEXT NULL")
    ensure_column("blood_requests", "status", "VARCHAR(50) DEFAULT 'Pending'")
    ensure_column("blood_requests", "admin_approved", "BOOLEAN DEFAULT FALSE")
    ensure_column("blood_requests", "transferred_units", "INT DEFAULT 0")
    ensure_column("blood_requests", "transferred_at", "DATETIME NULL")
    ensure_column("blood_requests", "ai_priority_score", "INT DEFAULT 0")
    ensure_column("blood_requests", "fraud_risk_score", "INT DEFAULT 0")
    ensure_column("blood_requests", "fraud_flags", "VARCHAR(255) NULL")
    ensure_column("blood_requests", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column("blood_requests", "allocation_details", "TEXT NULL")
    ensure_varchar_column("blood_requests", "status", 50, "Pending")

    ensure_column("camp_events", "status", "VARCHAR(20) DEFAULT 'Upcoming'")
    ensure_column("camp_events", "contact_info", "VARCHAR(100) NULL")
    ensure_column("camp_events", "event_end_date", "DATE NULL")
    ensure_column("camp_events", "organizer_name", "VARCHAR(120) NULL")
    ensure_column("camp_events", "camp_phone", "VARCHAR(20) NULL")

    ensure_column("camp_event_registrations", "registration_status", "VARCHAR(20) DEFAULT 'Registered'")
    ensure_column("camp_event_registrations", "units_collected", "INT DEFAULT 0")
    ensure_column("camp_event_registrations", "donated_at", "DATETIME NULL")

    # Admin Camp Donations table for donors to select & reselect donation dates
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_camp_donations (
            id INT PRIMARY KEY AUTO_INCREMENT,
            donor_id INT NOT NULL,
            selected_donation_date DATE NOT NULL,
            status VARCHAR(50) DEFAULT 'Pending' COMMENT 'Pending|Donated|Missed|Canceled',
            donated_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE,
            INDEX idx_donor_status (donor_id, status),
            INDEX idx_date_status (selected_donation_date, status)
        )
        """
    )

    ensure_column("donors", "admin_camp_scheduled_date", "DATE NULL")
    ensure_column("donors", "admin_camp_donation_status", "VARCHAR(50) DEFAULT 'None' COMMENT 'None|Pending|Donated|Missed'")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id INT PRIMARY KEY AUTO_INCREMENT,
            entity_type VARCHAR(30) NOT NULL,
            entity_id INT NOT NULL,
            action VARCHAR(20) NOT NULL,
            approved_by INT NOT NULL,
            note VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (approved_by) REFERENCES admin(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            actor_role VARCHAR(20) NOT NULL,
            actor_id INT NULL,
            action VARCHAR(60) NOT NULL,
            entity_type VARCHAR(40) NOT NULL,
            entity_id INT NULL,
            details VARCHAR(500),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS donor_deferral_events (
            id INT PRIMARY KEY AUTO_INCREMENT,
            donor_id INT NOT NULL,
            reason VARCHAR(120) NOT NULL,
            consumed_at DATETIME NULL,
            deferral_until DATETIME NULL,
            source VARCHAR(40) DEFAULT 'profile_update',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE,
            INDEX idx_deferral_donor_created (donor_id, created_at)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blood_stock (
            id INT PRIMARY KEY AUTO_INCREMENT,
            blood_group VARCHAR(10) UNIQUE NOT NULL,
            units_available INT DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )

    # Initialize blood stock for all blood groups if empty
    cursor.execute("SELECT COUNT(*) FROM blood_stock")
    if cursor.fetchone()[0] == 0:
        blood_groups = ['O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-']
        for bg in blood_groups:
            cursor.execute(
                "INSERT INTO blood_stock (blood_group, units_available) VALUES (%s, 0)",
                (bg,)
            )

    # Normalize legacy rows where status became blank/null due to older enum-like schemas.
    cursor.execute(
        """
        UPDATE blood_requests
        SET status = CASE
            WHEN COALESCE(admin_approved, FALSE) THEN 'Pending Transfer'
            ELSE 'Pending'
        END
        WHERE status IS NULL OR TRIM(status) = ''
        """
    )

    cursor.execute(
        """
        UPDATE donors
        SET account_status = CASE
            WHEN name IS NULL OR TRIM(name) = '' OR age IS NULL OR address IS NULL OR TRIM(address) = '' THEN 'Registered'
            ELSE 'ProfileCompleted'
        END
        WHERE account_status IS NULL OR TRIM(account_status) = ''
        """
    )

    cursor.execute(
        """
        UPDATE donors
        SET donor_status = CASE
            WHEN COALESCE(is_permanently_deferred, FALSE) = TRUE THEN 'Permanently Deferred'
            WHEN temporary_deferral_until IS NOT NULL AND temporary_deferral_until > NOW() THEN 'Temporarily Deferred'
            WHEN account_status = 'Registered' THEN 'Registered'
            WHEN COALESCE(blood_group_verified, FALSE) = TRUE THEN 'Medically Cleared'
            ELSE 'Pre-Eligible'
        END
        WHERE donor_status IS NULL OR TRIM(donor_status) = ''
        """
    )

    mysql.connection.commit()
    cursor.close()


def create_app():
    """Application factory for the blood donation management system."""
    app = Flask(__name__)
    app.config.from_object(Config)

    mysql.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"

    # Google OAuth setup for donor/hospital/camp registration verification.
    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        oauth.google = None

    # Blueprints (application layer routes).
    from routes.admin_routes import admin
    from routes.auth_routes import auth
    from routes.camp_routes import camp
    from routes.donor_routes import donor
    from routes.hospital_routes import hospital

    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(donor)
    app.register_blueprint(hospital)
    app.register_blueprint(camp)

    @app.before_request
    def sync_auth_session_state():
        payload = decode_session_token()

        if payload:
            session["active_role"] = payload.get("role")
            session["active_uid"] = payload.get("uid") or payload.get("sub")
            session["active_email"] = payload.get("email")

            from models.models import create_session_token

            # Rotate token on each authenticated request to keep active sessions stable.
            session["jwt_token"] = create_session_token(
                session.get("active_uid"),
                session.get("active_role"),
                session.get("active_email"),
            )
        else:
            # Try to restore from fallback session variables
            fallback_role = session.get("active_role")
            fallback_uid = session.get("active_uid")
            fallback_email = session.get("active_email")
            if fallback_role and fallback_uid and fallback_email:
                from models.models import create_session_token
                # Create a new token to extend the session lifetime
                session["jwt_token"] = create_session_token(fallback_uid, fallback_role, fallback_email)
                payload = {
                    "role": fallback_role,
                    "uid": fallback_uid,
                    "sub": str(fallback_uid),
                    "email": fallback_email,
                }

        # Always mark session as permanent if user is authenticated
        if payload or session.get("active_role"):
            session.permanent = True

    @app.after_request
    def sync_auth_cookie(response):
        auth_cookie_name = app.config.get("AUTH_COOKIE_NAME", "auth_token")
        session_token = session.get("jwt_token")
        secure_cookie = bool(app.config.get("SESSION_COOKIE_SECURE")) and request.is_secure
        max_age_seconds = int(app.config.get("PERMANENT_SESSION_LIFETIME", timedelta(hours=8)).total_seconds())

        if session_token:
            response.set_cookie(
                auth_cookie_name,
                session_token,
                max_age=max_age_seconds,
                httponly=True,
                samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
                secure=secure_cookie,
                path="/",
            )
        else:
            response.delete_cookie(
                auth_cookie_name,
                path="/",
                samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
                secure=secure_cookie,
            )

        return response

    @app.after_request
    def disable_dashboard_caching(response):
        if (
            request.path.startswith("/admin")
            or request.path.startswith("/hospital")
            or request.path.startswith("/donor")
            or request.path.startswith("/camp")
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.context_processor
    def inject_auth_state():
        payload = decode_session_token()
        if not payload:
            fallback_role = session.get("active_role")
            if fallback_role:
                return {"is_authenticated": True, "active_role": fallback_role}
            return {"is_authenticated": False, "active_role": None}
        return {"is_authenticated": True, "active_role": payload.get("role")}

    with app.app_context():
        ensure_core_tables()
        ensure_default_admin(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
