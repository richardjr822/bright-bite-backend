import os
from pydantic import BaseModel
from dotenv import load_dotenv
from functools import lru_cache

load_dotenv()

class Settings(BaseModel):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "BrightBite API"
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    SUPABASE_URL: str = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    MODEL_NAME: str = os.getenv("MEALPLAN_MODEL", "gpt-4o-mini")
    MAX_TOKENS: int = int(os.getenv("MEALPLAN_MAX_TOKENS", "1200"))
    TEMPERATURE: float = float(os.getenv("MEALPLAN_TEMPERATURE", "0.4"))


@lru_cache
def get_settings():
    return Settings()