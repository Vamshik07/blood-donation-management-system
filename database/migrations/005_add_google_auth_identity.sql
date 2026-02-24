ALTER TABLE donors
    ADD COLUMN IF NOT EXISTS google_sub VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) DEFAULT 'google';

ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS google_sub VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) DEFAULT 'google';

ALTER TABLE blood_camps
    ADD COLUMN IF NOT EXISTS google_sub VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) DEFAULT 'google';

CREATE UNIQUE INDEX uq_donors_google_sub ON donors (google_sub);
CREATE UNIQUE INDEX uq_hospitals_google_sub ON hospitals (google_sub);
CREATE UNIQUE INDEX uq_camps_google_sub ON blood_camps (google_sub);