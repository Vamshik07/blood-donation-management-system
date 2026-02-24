-- Clear all user data (keeps admin and database structure)
USE donation_management;

-- Disable foreign key checks temporarily
SET FOREIGN_KEY_CHECKS = 0;

-- Clear user tables
TRUNCATE TABLE donors;
TRUNCATE TABLE hospitals;
TRUNCATE TABLE blood_camps;

-- Clear related data
TRUNCATE TABLE blood_requests;
TRUNCATE TABLE donations;
TRUNCATE TABLE camp_events;
TRUNCATE TABLE camp_event_registrations;
TRUNCATE TABLE camp_event_blood_groups;
TRUNCATE TABLE blood_inventory_units;
TRUNCATE TABLE blood_stock;
TRUNCATE TABLE request_status_history;
TRUNCATE TABLE donor_donation_schedules;
TRUNCATE TABLE activity_logs;

-- Re-enable foreign key checks
SET FOREIGN_KEY_CHECKS = 1;

-- Verify tables are empty
SELECT 'Donors count:' as info, COUNT(*) as count FROM donors
UNION ALL
SELECT 'Hospitals count:', COUNT(*) FROM hospitals
UNION ALL
SELECT 'Blood Camps count:', COUNT(*) FROM blood_camps
UNION ALL
SELECT 'Blood Requests count:', COUNT(*) FROM blood_requests
UNION ALL
SELECT 'Donations count:', COUNT(*) FROM donations
UNION ALL
SELECT 'Admin count (preserved):', COUNT(*) FROM admin;
