ALTER TABLE donors
    ADD COLUMN IF NOT EXISTS account_status VARCHAR(30) DEFAULT 'Registered',
    ADD COLUMN IF NOT EXISTS donor_status VARCHAR(30) DEFAULT 'Registered',
    ADD COLUMN IF NOT EXISTS account_suspended BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_permanently_deferred BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deferral_reason VARCHAR(255) NULL;

UPDATE donors
SET account_status = CASE
        WHEN name IS NULL OR TRIM(name) = '' OR age IS NULL OR address IS NULL OR TRIM(address) = '' THEN 'Registered'
        ELSE 'ProfileCompleted'
    END
WHERE account_status IS NULL OR TRIM(account_status) = '';

UPDATE donors
SET donor_status = CASE
        WHEN COALESCE(is_permanently_deferred, FALSE) = TRUE THEN 'Permanently Deferred'
        WHEN temporary_deferral_until IS NOT NULL AND temporary_deferral_until > NOW() THEN 'Temporarily Deferred'
        WHEN account_status = 'Registered' THEN 'Registered'
        WHEN COALESCE(blood_group_verified, FALSE) = TRUE THEN 'Medically Cleared'
        ELSE 'Pre-Eligible'
    END
WHERE donor_status IS NULL OR TRIM(donor_status) = '';
