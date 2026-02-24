# Database Migrations

Run migrations in order after creating database with `database/schema.sql`.

## Migration Files

1. `migrations/001_add_blood_camps_phone.sql`
   - Adds `phone` column to `blood_camps` if it does not already exist.
2. `migrations/002_backfill_empty_phones.sql`
   - Converts empty phone strings to `NULL` for cleaner SMS handling.
3. `migrations/003_add_camp_event_lifecycle.sql`
   - Adds camp event tables, required blood groups, donor registrations, and donation completion fields.
4. `migrations/004_add_unit_inventory_and_timeline.sql`
   - Adds unit-level blood inventory tracking and request timeline/audit history.
5. `migrations/005_add_google_auth_identity.sql`
   - Adds Google identity columns and unique `google_sub` keys for donor/hospital/camp accounts.

## How to Run

Using MySQL CLI:

```sql
SOURCE database/migrations/001_add_blood_camps_phone.sql;
SOURCE database/migrations/002_backfill_empty_phones.sql;
SOURCE database/migrations/003_add_camp_event_lifecycle.sql;
SOURCE database/migrations/004_add_unit_inventory_and_timeline.sql;
SOURCE database/migrations/005_add_google_auth_identity.sql;
```

Using phpMyAdmin:
- Open SQL tab.
- Paste each migration file content and execute in sequence.
