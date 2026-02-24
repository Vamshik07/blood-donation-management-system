USE donation_management;

-- Optional normalization: keep NULL instead of empty strings.
UPDATE donors SET phone = NULL WHERE phone = '';
UPDATE hospitals SET phone = NULL WHERE phone = '';
UPDATE blood_camps SET phone = NULL WHERE phone = '';
