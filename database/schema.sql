CREATE DATABASE IF NOT EXISTS donation_management;
USE donation_management;

-- Admin table: only pre-approved admin can login.
CREATE TABLE IF NOT EXISTS admin (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Donors table: registration + profile + admin approval.
CREATE TABLE IF NOT EXISTS donors (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    email VARCHAR(100) UNIQUE,
    password VARCHAR(255),
    age INT,
    blood_group VARCHAR(10),
    address TEXT,
    phone VARCHAR(15),
    last_donation DATE,
    scheduled_donation_at DATE,
    donation_status VARCHAR(20) DEFAULT 'Pending',
    donation_completed_at DATETIME,
    units_donated INT DEFAULT 0,
    google_sub VARCHAR(100) UNIQUE,
    email_verified BOOLEAN DEFAULT FALSE,
    auth_provider VARCHAR(20) DEFAULT 'google',
    health_status BOOLEAN DEFAULT FALSE,
    fit_confirmation BOOLEAN DEFAULT FALSE,
    approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Hospitals table: registration + admin approval.
CREATE TABLE IF NOT EXISTS hospitals (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    email VARCHAR(100) UNIQUE,
    password VARCHAR(255),
    address TEXT,
    phone VARCHAR(15),
    google_sub VARCHAR(100) UNIQUE,
    email_verified BOOLEAN DEFAULT FALSE,
    auth_provider VARCHAR(20) DEFAULT 'google',
    approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Blood camps table: registration + admin approval.
CREATE TABLE IF NOT EXISTS blood_camps (
    id INT PRIMARY KEY AUTO_INCREMENT,
    camp_name VARCHAR(100),
    email VARCHAR(100) UNIQUE,
    password VARCHAR(255),
    phone VARCHAR(15),
    google_sub VARCHAR(100) UNIQUE,
    email_verified BOOLEAN DEFAULT FALSE,
    auth_provider VARCHAR(20) DEFAULT 'google',
    location TEXT,
    days INT,
    slots_per_day INT,
    expected_donors INT,
    approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Blood requests submitted by hospitals.
CREATE TABLE IF NOT EXISTS blood_requests (
    id INT PRIMARY KEY AUTO_INCREMENT,
    hospital_id INT,
    patient_name VARCHAR(100),
    blood_group VARCHAR(10),
    units_required INT,
    required_ml INT,
    emergency BOOLEAN,
    hospital_address TEXT,
    contact_number VARCHAR(15),
    status VARCHAR(50) DEFAULT 'Pending',
    admin_approved BOOLEAN DEFAULT FALSE,
    transferred_units INT DEFAULT 0,
    transferred_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
);

-- Approval audit for admin actions.
CREATE TABLE IF NOT EXISTS approvals (
    id INT PRIMARY KEY AUTO_INCREMENT,
    entity_type VARCHAR(30) NOT NULL,
    entity_id INT NOT NULL,
    action VARCHAR(20) NOT NULL,
    approved_by INT NOT NULL,
    note VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (approved_by) REFERENCES admin(id)
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    actor_role VARCHAR(20) NOT NULL,
    actor_id INT NULL,
    action VARCHAR(60) NOT NULL,
    entity_type VARCHAR(40) NOT NULL,
    entity_id INT NULL,
    details VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Blood stock inventory by blood group.
CREATE TABLE IF NOT EXISTS blood_stock (
    id INT PRIMARY KEY AUTO_INCREMENT,
    blood_group VARCHAR(10) UNIQUE NOT NULL,
    units_available INT DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Insert blood groups for stock tracking.
INSERT IGNORE INTO blood_stock (blood_group, units_available) VALUES
('O+', 0),
('O-', 0),
('A+', 0),
('A-', 0),
('B+', 0),
('B-', 0),
('AB+', 0),
('AB-', 0);

-- Unit-level blood inventory for full traceability.
CREATE TABLE IF NOT EXISTS blood_inventory_units (
    id INT PRIMARY KEY AUTO_INCREMENT,
    unit_tracking_id VARCHAR(40) UNIQUE NOT NULL,
    blood_group VARCHAR(10) NOT NULL,
    collection_source VARCHAR(20) NOT NULL,
    source_ref_id INT NULL,
    collection_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'Available',
    request_id INT NULL,
    reserved_at DATETIME NULL,
    used_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_inventory_group_status (blood_group, status, expiry_date),
    FOREIGN KEY (request_id) REFERENCES blood_requests(id)
);

-- Request lifecycle timeline and audit entries.
CREATE TABLE IF NOT EXISTS request_status_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    request_id INT NOT NULL,
    status VARCHAR(40) NOT NULL,
    note VARCHAR(255),
    changed_by_role VARCHAR(20) NOT NULL,
    changed_by_id INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES blood_requests(id)
);

-- Camp events created by approved blood camps.
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

-- Required blood groups for each camp event.
CREATE TABLE IF NOT EXISTS camp_event_blood_groups (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_id INT NOT NULL,
    blood_group VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event_group (event_id, blood_group),
    FOREIGN KEY (event_id) REFERENCES camp_events(id) ON DELETE CASCADE
);

-- Donor registrations for camp events.
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

-- Insert admin user after generating bcrypt hash in app shell.
-- Example:
-- INSERT INTO admin (email, password)
-- VALUES ('admin@blood.com', '$2b$12$hashedpasswordhere');

-- If upgrading existing DB, run:
-- ALTER TABLE blood_camps ADD COLUMN phone VARCHAR(15);
-- ALTER TABLE blood_requests ADD COLUMN allocation_details TEXT;
