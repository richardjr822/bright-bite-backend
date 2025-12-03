-- Add delivery_address column to orders table
-- This stores the student's delivery location (Building, Floor, Room)

ALTER TABLE public.orders 
ADD COLUMN IF NOT EXISTS delivery_address TEXT;

COMMENT ON COLUMN public.orders.delivery_address IS 'Delivery location entered by student during checkout (Building, Floor, Room)';
