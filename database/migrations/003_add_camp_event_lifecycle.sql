CREATE TABLE IF NOT EXISTS camp_events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    camp_id INT NOT NULL,
    event_name VARCHAR(120) NOT NULL,
    location VARCHAR(255) NOT NULL,
    event_date DATE NOT NULL,
    target_units INT NOT NULL,
    contact_info VARCHAR(100),
    status VARCHAR(20) DEFAULT 'Upcoming',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camp_id) REFERENCES blood_camps(id)
);

CREATE TABLE IF NOT EXISTS camp_event_blood_groups (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_id INT NOT NULL,
    blood_group VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event_group (event_id, blood_group),
    FOREIGN KEY (event_id) REFERENCES camp_events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS camp_event_registrations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_id INT NOT NULL,
    donor_id INT NOT NULL,
    preferred_slot VARCHAR(50) NOT NULL,
    registration_status VARCHAR(20) DEFAULT 'Registered',
    units_collected INT DEFAULT 0,
    donated_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event_donor (event_id, donor_id),
    FOREIGN KEY (event_id) REFERENCES camp_events(id) ON DELETE CASCADE,
    FOREIGN KEY (donor_id) REFERENCES donors(id)
);

ALTER TABLE camp_events
    ADD COLUMN IF NOT EXISTS contact_info VARCHAR(100) NULL;

ALTER TABLE camp_events
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Upcoming';

ALTER TABLE camp_event_registrations
    ADD COLUMN IF NOT EXISTS registration_status VARCHAR(20) DEFAULT 'Registered';

ALTER TABLE camp_event_registrations
    ADD COLUMN IF NOT EXISTS units_collected INT DEFAULT 0;

ALTER TABLE camp_event_registrations
    ADD COLUMN IF NOT EXISTS donated_at DATETIME NULL;