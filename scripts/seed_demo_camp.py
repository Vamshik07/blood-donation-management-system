from flask_bcrypt import Bcrypt

from app import app
from extensions import mysql

DEMO_EMAIL = "demo.camp@gmail.com"
DEMO_PASSWORD = "DemoCamp@123"
DEMO_NAME = "Demo Blood Camp"
DEMO_PHONE = "+911234567892"
DEMO_LOCATION = "Demo Community Hall"
DEMO_DAYS = 2
DEMO_SLOTS_PER_DAY = 25
DEMO_EXPECTED_DONORS = 40


def seed_demo_camp():
    bcrypt = Bcrypt()
    hashed_password = bcrypt.generate_password_hash(DEMO_PASSWORD).decode("utf-8")

    with app.app_context():
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT id FROM blood_camps WHERE email = %s", (DEMO_EMAIL,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE blood_camps
                SET camp_name = %s,
                    password = %s,
                    phone = %s,
                    location = %s,
                    days = %s,
                    slots_per_day = %s,
                    expected_donors = %s,
                    approved = TRUE
                WHERE email = %s
                """,
                (
                    DEMO_NAME,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_LOCATION,
                    DEMO_DAYS,
                    DEMO_SLOTS_PER_DAY,
                    DEMO_EXPECTED_DONORS,
                    DEMO_EMAIL,
                ),
            )
            action = "updated"
        else:
            cursor.execute(
                """
                INSERT INTO blood_camps
                    (camp_name, email, password, phone, location, days, slots_per_day, expected_donors, approved)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    DEMO_NAME,
                    DEMO_EMAIL,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_LOCATION,
                    DEMO_DAYS,
                    DEMO_SLOTS_PER_DAY,
                    DEMO_EXPECTED_DONORS,
                ),
            )
            action = "created"

        mysql.connection.commit()
        cursor.close()

    print(f"Demo camp {action} successfully.")
    print(f"Email: {DEMO_EMAIL}")
    print(f"Password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed_demo_camp()
