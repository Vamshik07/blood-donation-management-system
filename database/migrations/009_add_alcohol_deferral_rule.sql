ALTER TABLE donors
    ADD COLUMN IF NOT EXISTS alcohol_consumed_recently BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_alcohol_consumption_datetime DATETIME NULL,
    ADD COLUMN IF NOT EXISTS temporary_deferral_until DATETIME NULL;

CREATE TABLE IF NOT EXISTS donor_deferral_events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    donor_id INT NOT NULL,
    reason VARCHAR(120) NOT NULL,
    consumed_at DATETIME NULL,
    deferral_until DATETIME NULL,
    source VARCHAR(40) DEFAULT 'profile_update',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE,
    INDEX idx_deferral_donor_created (donor_id, created_at)
);
