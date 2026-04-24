-- Migration 007: Add Admin Camp Donations Table
-- Purpose: Allow donors to select donation dates for admin camp donations
-- Features: Donors can suggest donation date, reselect if unable, no second approval needed

ALTER TABLE `donation_management`.`donors` 
ADD COLUMN IF NOT EXISTS `admin_camp_scheduled_date` DATE NULL,
ADD COLUMN IF NOT EXISTS `admin_camp_donation_status` VARCHAR(50) DEFAULT 'None' COMMENT 'None|Pending|Donated|Missed';

CREATE TABLE IF NOT EXISTS `donation_management`.`admin_camp_donations` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `donor_id` INT NOT NULL,
  `selected_donation_date` DATE NOT NULL COMMENT 'Date donor wants to donate at admin camp',
  `status` VARCHAR(50) NOT NULL DEFAULT 'Pending' COMMENT 'Pending|Donated|Missed|Canceled',
  `donated_at` DATETIME NULL COMMENT 'When donation was actually marked complete',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (`donor_id`) REFERENCES `donors`(`id`) ON DELETE CASCADE,
  INDEX `idx_donor_status` (`donor_id`, `status`),
  INDEX `idx_date_status` (`selected_donation_date`, `status`)
);

-- Index for finding pending donations by date
CREATE INDEX IF NOT EXISTS `idx_pending_by_date` ON `admin_camp_donations`(`selected_donation_date`, `status`);
