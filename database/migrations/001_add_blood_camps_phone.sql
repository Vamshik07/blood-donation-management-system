USE donation_management;

SET @column_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'donation_management'
      AND TABLE_NAME = 'blood_camps'
      AND COLUMN_NAME = 'phone'
);

SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE blood_camps ADD COLUMN phone VARCHAR(15) NULL AFTER password',
    'SELECT "phone column already exists in blood_camps" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
