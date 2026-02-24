from flask_bcrypt import Bcrypt

from app import app
from extensions import mysql

DEMO_EMAIL = "demo.donor@gmail.com"
DEMO_PASSWORD = "DemoDonor@123"
DEMO_NAME = "Demo Donor"
DEMO_PHONE = "+911234567890"
DEMO_AGE = 25
DEMO_BLOOD_GROUP = "O+"
DEMO_ADDRESS = "Demo City"


def seed_demo_donor():
    bcrypt = Bcrypt()
    hashed_password = bcrypt.generate_password_hash(DEMO_PASSWORD).decode("utf-8")

    with app.app_context():
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT id FROM donors WHERE email = %s", (DEMO_EMAIL,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE donors
                SET name = %s,
                    password = %s,
                    phone = %s,
                    age = %s,
                    blood_group = %s,
                    address = %s,
                    health_status = TRUE,
                    fit_confirmation = TRUE,
                    approved = TRUE
                WHERE email = %s
                """,
                (
                    DEMO_NAME,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_AGE,
                    DEMO_BLOOD_GROUP,
                    DEMO_ADDRESS,
                    DEMO_EMAIL,
                ),
            )
            action = "updated"
        else:
            cursor.execute(
                """
                INSERT INTO donors
                    (name, email, password, phone, age, blood_group, address, health_status, fit_confirmation, approved)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, TRUE)
                """,
                (
                    DEMO_NAME,
                    DEMO_EMAIL,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_AGE,
                    DEMO_BLOOD_GROUP,
                    DEMO_ADDRESS,
                ),
            )
            action = "created"

        mysql.connection.commit()
        cursor.close()

    print(f"Demo donor {action} successfully.")
    print(f"Email: {DEMO_EMAIL}")
    print(f"Password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed_demo_donor()
