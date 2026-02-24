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

ALTER TABLE blood_requests
    ADD COLUMN IF NOT EXISTS allocation_details TEXT NULL;