-- Add is_promoted column to menu_items table
-- This allows vendors to promote one meal that will be displayed in "Promoted Meal" section

ALTER TABLE public.menu_items 
ADD COLUMN IF NOT EXISTS is_promoted BOOLEAN NOT NULL DEFAULT false;

-- Create index for faster queries on promoted items
CREATE INDEX IF NOT EXISTS idx_menu_items_is_promoted ON public.menu_items(is_promoted) WHERE is_promoted = true;

-- Add comment to explain the column
COMMENT ON COLUMN public.menu_items.is_promoted IS 'Indicates if this menu item is currently promoted by the vendor. Only one item per vendor can be promoted at a time.';
