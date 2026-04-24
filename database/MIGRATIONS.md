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
6. `migrations/006_add_blood_group_verification.sql`
   - Adds blood group verification flags and related donor fields.
7. `migrations/007_add_admin_camp_donations.sql`
   - Adds admin-managed donor camp scheduling and status tracking.
8. `migrations/008_add_ai_priority_and_fraud_scores.sql`
   - Adds AI-assisted priority and fraud risk scoring columns for blood requests.
9. `migrations/009_add_alcohol_deferral_rule.sql`
   - Adds donor alcohol 24-hour temporary deferral fields and deferral audit table.
10. `migrations/010_add_layered_donor_access_states.sql`
   - Adds layered donor access states (Registered, Pre-Eligible, Temporarily Deferred, Medically Cleared, Permanently Deferred) and backfills status.
11. `migrations/011_add_camp_event_metadata_and_camp_flow.sql`
   - Adds camp event end-date/organizer/camp-phone fields and unlocks camps with no events to the new approval flow.

## How to Run

Using MySQL CLI:

```sql
SOURCE database/migrations/001_add_blood_camps_phone.sql;
SOURCE database/migrations/002_backfill_empty_phones.sql;
SOURCE database/migrations/003_add_camp_event_lifecycle.sql;
SOURCE database/migrations/004_add_unit_inventory_and_timeline.sql;
SOURCE database/migrations/005_add_google_auth_identity.sql;
SOURCE database/migrations/006_add_blood_group_verification.sql;
SOURCE database/migrations/007_add_admin_camp_donations.sql;
SOURCE database/migrations/008_add_ai_priority_and_fraud_scores.sql;
SOURCE database/migrations/009_add_alcohol_deferral_rule.sql;
SOURCE database/migrations/010_add_layered_donor_access_states.sql;
SOURCE database/migrations/011_add_camp_event_metadata_and_camp_flow.sql;
```

Using phpMyAdmin:
- Open SQL tab.
- Paste each migration file content and execute in sequence.
