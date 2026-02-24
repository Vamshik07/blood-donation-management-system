-- Add blood group verification tracking for donors
-- When donors select "I don't know", their blood group will be tested during first donation
-- and marked as verified by camp staff

ALTER TABLE donors 
ADD COLUMN IF NOT EXISTS blood_group_verified BOOLEAN DEFAULT FALSE;

-- Mark existing donors with known blood groups as verified (migration data fix)
UPDATE donors 
SET blood_group_verified = TRUE 
WHERE blood_group IS NOT NULL 
  AND blood_group != '' 
  AND blood_group != 'UNKNOWN';
