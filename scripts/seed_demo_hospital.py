from flask_bcrypt import Bcrypt

from app import app
from extensions import mysql

DEMO_EMAIL = "demo.hospital@gmail.com"
DEMO_PASSWORD = "DemoHospital@123"
DEMO_NAME = "Demo Hospital"
DEMO_PHONE = "+911234567891"
DEMO_ADDRESS = "Demo Medical City"


def seed_demo_hospital():
    bcrypt = Bcrypt()
    hashed_password = bcrypt.generate_password_hash(DEMO_PASSWORD).decode("utf-8")

    with app.app_context():
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT id FROM hospitals WHERE email = %s", (DEMO_EMAIL,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE hospitals
                SET name = %s,
                    password = %s,
                    phone = %s,
                    address = %s,
                    approved = TRUE
                WHERE email = %s
                """,
                (
                    DEMO_NAME,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_ADDRESS,
                    DEMO_EMAIL,
                ),
            )
            action = "updated"
        else:
            cursor.execute(
                """
                INSERT INTO hospitals
                    (name, email, password, phone, address, approved)
                VALUES
                    (%s, %s, %s, %s, %s, TRUE)
                """,
                (
                    DEMO_NAME,
                    DEMO_EMAIL,
                    hashed_password,
                    DEMO_PHONE,
                    DEMO_ADDRESS,
                ),
            )
            action = "created"

        mysql.connection.commit()
        cursor.close()

    print(f"Demo hospital {action} successfully.")
    print(f"Email: {DEMO_EMAIL}")
    print(f"Password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed_demo_hospital()
