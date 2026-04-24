from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

from flask_bcrypt import Bcrypt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from extensions import mysql

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
DONORS_PER_GROUP = 3
UNITS_PER_GROUP = 8
DEFAULT_PASSWORD = "DemoDonor@123"


def build_donor_rows():
    rows = []
    sequence = 1
    for group in BLOOD_GROUPS:
        for _ in range(DONORS_PER_GROUP):
            rows.append(
                {
                    "name": f"Demo Donor {sequence:02d}",
                    "email": f"demo.donor{sequence:02d}@gmail.com",
                    "phone": f"+9190000{sequence:05d}",
                    "age": 22 + (sequence % 20),
                    "blood_group": group,
                    "address": f"Demo City Sector {(sequence % 12) + 1}",
                    "scheduled_donation_at": date.today() + timedelta(days=(sequence % 7) + 1),
                }
            )
            sequence += 1
    return rows


def seed_bulk_donors_and_inventory():
    bcrypt = Bcrypt()
    hashed_password = bcrypt.generate_password_hash(DEFAULT_PASSWORD).decode("utf-8")

    donor_rows = build_donor_rows()

    with app.app_context():
        cursor = mysql.connection.cursor()

        seeded_donor_ids = []
        for donor in donor_rows:
            cursor.execute(
                """
                INSERT INTO donors (
                    name,
                    email,
                    password,
                    phone,
                    age,
                    blood_group,
                    blood_group_verified,
                    address,
                    health_status,
                    fit_confirmation,
                    approved,
                    account_status,
                    donor_status,
                    account_suspended,
                    is_permanently_deferred,
                    temporary_deferral_until,
                    donation_status,
                    units_donated,
                    scheduled_donation_at,
                    email_verified,
                    auth_provider
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, TRUE, %s, TRUE, TRUE, TRUE,
                    'ProfileCompleted', 'Medically Cleared', FALSE, FALSE, NULL,
                    'Pending', 0, %s, TRUE, 'google'
                )
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    password = VALUES(password),
                    phone = VALUES(phone),
                    age = VALUES(age),
                    blood_group = VALUES(blood_group),
                    blood_group_verified = TRUE,
                    address = VALUES(address),
                    health_status = TRUE,
                    fit_confirmation = TRUE,
                    approved = TRUE,
                    account_status = 'ProfileCompleted',
                    donor_status = 'Medically Cleared',
                    account_suspended = FALSE,
                    is_permanently_deferred = FALSE,
                    temporary_deferral_until = NULL,
                    donation_status = 'Pending',
                    scheduled_donation_at = VALUES(scheduled_donation_at),
                    email_verified = TRUE,
                    auth_provider = 'google'
                """,
                (
                    donor["name"],
                    donor["email"],
                    hashed_password,
                    donor["phone"],
                    donor["age"],
                    donor["blood_group"],
                    donor["address"],
                    donor["scheduled_donation_at"],
                ),
            )
            cursor.execute("SELECT id FROM donors WHERE email = %s", (donor["email"],))
            row = cursor.fetchone()
            if row:
                seeded_donor_ids.append(row[0])

        inserted_units = 0
        for group in BLOOD_GROUPS:
            for _ in range(UNITS_PER_GROUP):
                cursor.execute(
                    """
                    INSERT INTO blood_inventory_units (
                        unit_tracking_id,
                        blood_group,
                        collection_source,
                        source_ref_id,
                        collection_date,
                        expiry_date,
                        status
                    )
                    VALUES (%s, %s, 'DonorSeed', NULL, CURDATE(), DATE_ADD(CURDATE(), INTERVAL 35 DAY), 'Available')
                    """,
                    (f"SEED-{uuid4().hex[:16].upper()}", group),
                )
                inserted_units += 1

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
            ) inv ON inv.blood_group = bs.blood_group
            SET bs.units_available = inv.available_units
            """
        )

        mysql.connection.commit()
        cursor.close()

    print("Bulk donor and inventory seeding completed.")
    print(f"Donors processed: {len(donor_rows)}")
    print(f"Donor IDs resolved: {len(seeded_donor_ids)}")
    print(f"Inventory units inserted: {inserted_units}")
    print(f"Default donor password: {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    seed_bulk_donors_and_inventory()
