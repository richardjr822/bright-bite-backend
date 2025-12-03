-- Migration: Clean up duplicate meal_preferences rows
-- Keep only the most recent preference for each user_id

-- Step 1: Create a temp table with the IDs to keep (most recent per user)
CREATE TEMP TABLE prefs_to_keep AS
SELECT DISTINCT ON (user_id) id
FROM public.meal_preferences
ORDER BY user_id, updated_at DESC NULLS LAST, created_at DESC NULLS LAST;

-- Step 2: Delete all preferences NOT in the keep list
DELETE FROM public.meal_preferences
WHERE id NOT IN (SELECT id FROM prefs_to_keep);

-- Step 3: Drop the temp table
DROP TABLE prefs_to_keep;

-- Step 4: Add unique constraint on user_id to prevent future duplicates
-- First check if constraint exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'meal_preferences_user_id_unique'
    ) THEN
        ALTER TABLE public.meal_preferences
        ADD CONSTRAINT meal_preferences_user_id_unique UNIQUE (user_id);
    END IF;
END $$;

-- Verify: Show remaining rows grouped by user
SELECT user_id, COUNT(*) as count 
FROM public.meal_preferences 
GROUP BY user_id 
HAVING COUNT(*) > 1;
-- Should return 0 rows if cleanup was successful
