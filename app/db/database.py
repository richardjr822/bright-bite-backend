import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
# Prefer service role key when available to bypass RLS for server-side operations
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE") or os.getenv("SUPABASE_KEY")

supabase = create_client(supabase_url, supabase_key)