-- Add proof_of_delivery field to orders table
-- This stores the URL of the delivery proof image uploaded by delivery staff

ALTER TABLE public.orders 
ADD COLUMN IF NOT EXISTS proof_of_delivery_url TEXT;

-- Add comment to explain the column
COMMENT ON COLUMN public.orders.proof_of_delivery_url IS 'URL of the proof of delivery image uploaded by delivery staff when marking order as delivered';

-- Add index for faster queries
CREATE INDEX IF NOT EXISTS idx_orders_proof_of_delivery ON public.orders(proof_of_delivery_url) WHERE proof_of_delivery_url IS NOT NULL;
