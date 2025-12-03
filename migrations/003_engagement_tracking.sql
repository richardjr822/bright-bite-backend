-- Migration: Engagement Tracking & Insights Tables
-- Run this on your Supabase database

-- Engagement Events Table (tracks user activity)
CREATE TABLE IF NOT EXISTS public.engagement_events (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  event_type text NOT NULL,
  metadata jsonb DEFAULT '{}',
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT engagement_events_pkey PRIMARY KEY (id),
  CONSTRAINT engagement_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);

-- Index for fast user queries
CREATE INDEX IF NOT EXISTS idx_engagement_events_user_id ON public.engagement_events(user_id);
CREATE INDEX IF NOT EXISTS idx_engagement_events_created_at ON public.engagement_events(created_at);
CREATE INDEX IF NOT EXISTS idx_engagement_events_type ON public.engagement_events(event_type);

-- Enable RLS
ALTER TABLE public.engagement_events ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their own events
CREATE POLICY "Users can view own engagement events" ON public.engagement_events
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own engagement events" ON public.engagement_events
  FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Grant access to service role
GRANT ALL ON public.engagement_events TO service_role;
GRANT SELECT, INSERT ON public.engagement_events TO authenticated;
