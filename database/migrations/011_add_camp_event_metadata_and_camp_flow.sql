USE donation_management;

ALTER TABLE camp_events
    ADD COLUMN IF NOT EXISTS event_end_date DATE NULL AFTER event_date;

ALTER TABLE camp_events
    ADD COLUMN IF NOT EXISTS organizer_name VARCHAR(120) NULL AFTER contact_info;

ALTER TABLE camp_events
    ADD COLUMN IF NOT EXISTS camp_phone VARCHAR(20) NULL AFTER organizer_name;

-- Camps should not need admin approval before creating their first event.
UPDATE blood_camps bc
LEFT JOIN (
    SELECT camp_id, COUNT(*) AS total_events
    FROM camp_events
    GROUP BY camp_id
) ce ON ce.camp_id = bc.id
SET bc.approved = TRUE
WHERE bc.approved = FALSE
  AND COALESCE(ce.total_events, 0) = 0;
