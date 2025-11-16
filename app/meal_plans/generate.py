from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, Optional, List
import os
from datetime import datetime
from uuid import UUID
from app.schemas.meal_plan import MealPlanResponse
from app.db.database import supabase
from .ai_service import ai_generate

router = APIRouter()
# NEW: configurable generated plan table
PLAN_TABLE = os.getenv("GENERATED_PLAN_TABLE", "generated_plan_meals")
DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def _canonical_day(d: str) -> str:
    if not d: return "monday"
    dl = d.strip().lower()
    return dl if dl in DAYS else "monday"

def _to_db(preferences: Dict[str, Any]) -> Dict[str, Any]:
    # map camelCase -> snake_case, cast where needed
    return {
        "age": int(preferences.get("age") or 0) or None,
        "sex": preferences.get("sex") or None,
        "height": float(preferences.get("height") or 0) or None,
        "weight": float(preferences.get("weight") or 0) or None,
        "goal": preferences.get("goal") or "maintain",
        "activity_level": preferences.get("activityLevel") or "moderate",
        "dietary_preference": preferences.get("dietaryPreference") or [],
        "avoid_foods": preferences.get("avoidFoods") or None,
        "allergies": preferences.get("allergies") or [],
        "health_conditions": preferences.get("healthConditions") or [],
        "calorie_target": int(preferences.get("calorieTarget") or 2000),
        "macro_preference": preferences.get("macroPreference") or "balanced",
        "meals_per_day": int(preferences.get("mealsPerDay") or 3),
        "meal_complexity": preferences.get("mealComplexity") or "simple",
        "meal_prep_style": preferences.get("mealPrepStyle") or "daily",
        "daily_budget": preferences.get("dailyBudget") or None,
        "cooking_time": preferences.get("cookingTime") or None,
        "cooking_methods": preferences.get("cookingMethod") or [],
        "special_goals": preferences.get("specialGoals") or [],
        "appetite": preferences.get("appetite") or "normal",
    }

def _save_preferences(prefs: Dict[str, Any], user_id: Optional[str]) -> None:
    try:
        if not user_id:
            return
        row = _to_db(prefs) | {"user_id": user_id}
        # best-effort insert; ignore failures
        supabase.table("meal_preferences").insert(row).execute()
    except Exception:
        # swallow DB errors so generation still works
        pass

def save_plan_for_user(user_id: str, plan: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    Persist AI generated plan into generated_plan_meals (NOT meals intake table).
    """
    sb = _get_client()
    if not sb or not user_id: return
    try:
        # Remove previous generated plan meals for this user only
        sb.table(PLAN_TABLE).delete().eq("user_id", user_id).execute()
    except Exception:
        pass

    rows: List[Dict[str, Any]] = []
    for day_key, meals in (plan or {}).items():
        day = _canonical_day(day_key)
        for m in (meals or []):
            macros = m.get("macros") or {}
            rows.append({
                "user_id": user_id,
                "day": day,
                "name": m.get("name","Meal"),
                "meal_type": (m.get("type") or m.get("meal_type") or "Snack"),
                "calories": int(m.get("calories",0)),
                "protein": int(macros.get("protein",0)),
                "carbs": int(macros.get("carbs",0)),
                "fats": int(macros.get("fats",0)),
                "prep_time": int(m.get("prep_time", m.get("prepTime", 20)) or 20),
                "description": m.get("description") or "",
            })
    if not rows:
        return
    try:
        chunk = 500
        for i in range(0,len(rows),chunk):
            sb.table(PLAN_TABLE).insert(rows[i:i+chunk]).execute()
    except Exception:
        pass

def load_saved_plan_for_user(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load previously generated plan from generated_plan_meals.
    Returns normalized 7-day dict; days with no meals -> empty list.
    """
    sb = _get_client()
    if not sb or not user_id:
        return {d: [] for d in DAYS}
    try:
        res = sb.table(PLAN_TABLE).select("*").eq("user_id", user_id).order("day", desc=False).execute()
        rows = getattr(res, "data", []) or []
    except Exception:
        rows = []
    grouped: Dict[str, List[Dict[str, Any]]] = {d: [] for d in DAYS}
    for r in rows:
        day = _canonical_day(r.get("day"))
        grouped[day].append({
            "id": r.get("id"),
            "name": r.get("name"),
            "type": r.get("meal_type"),
            "calories": int(r.get("calories",0)),
            "prep_time": int(r.get("prep_time",20)),
            "description": r.get("description") or "",
            "macros": {
                "protein": int(r.get("protein",0)),
                "carbs": int(r.get("carbs",0)),
                "fats": int(r.get("fats",0)),
            }
        })
    return grouped

@router.get("/plan", response_model=MealPlanResponse)
async def get_generated_plan(request: Request):
    """
    Fetch persisted generated plan (does not include manually logged intake).
    """
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id header")
    plan = load_saved_plan_for_user(user_id)
    # If all empty, treat as not found
    if all(len(v)==0 for v in plan.values()):
        raise HTTPException(status_code=404, detail="No generated plan saved")
    return MealPlanResponse(plan=plan)

@router.post("/generate", response_model=MealPlanResponse)
async def generate_meal_plan(preferences: Dict[str, Any], request: Request):
    try:
        user_id = preferences.get("userId") or request.headers.get("x-user-id")
        _save_preferences(preferences, user_id)
        raw = ai_generate(preferences)  # raw is dict day->list[meals]
        if user_id:
            save_plan_for_user(user_id, raw)
        return MealPlanResponse(plan=raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to generate meal plan")

def _get_client():
    try:
        from app.db.database import supabase as sb  # project client if available
        if sb: return sb
    except Exception:
        pass
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

def load_user_preferences(user_id: str) -> Optional[Dict[str, Any]]:
    sb = _get_client()
    if not sb: return None
    try:
        res = sb.table("meal_preferences").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        return rows[0] if rows else None
    except Exception:
        return None

def create_user_preferences(user_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    sb = _get_client()
    if not sb: return {}
    row = {
        "user_id": user_id,
        "age": prefs.get("age"),
        "sex": prefs.get("sex"),
        "height": prefs.get("height"),
        "weight": prefs.get("weight"),
        "goal": prefs.get("goal","maintain"),
        "activity_level": prefs.get("activityLevel","moderate"),
        "dietary_preference": prefs.get("dietaryPreference") or [],
        "avoid_foods": prefs.get("avoidFoods") or "",
        "allergies": prefs.get("allergies") or [],
        "health_conditions": prefs.get("healthConditions") or [],
        "calorie_target": prefs.get("calorieTarget", 2000),
        "macro_preference": prefs.get("macroPreference","balanced"),
        "meals_per_day": prefs.get("mealsPerDay", 3),
        "meal_complexity": prefs.get("mealComplexity","simple"),
        "meal_prep_style": prefs.get("mealPrepStyle","daily"),
        "daily_budget": prefs.get("dailyBudget"),
        "cooking_time": prefs.get("cookingTime"),
        "cooking_methods": prefs.get("cookingMethod") or [],
        "special_goals": prefs.get("specialGoals") or [],
    }
    try:
        res = sb.table("meal_preferences").insert(row).execute()
        return (getattr(res, "data", []) or [row])[0]
    except Exception:
        # If unique constraint exists, return existing
        existing = load_user_preferences(user_id)
        if existing: return existing
        return row

def patch_user_preferences(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    sb = _get_client()
    if not sb: return {}
    # Map patch keys -> DB columns (safe updates only)
    mapping = {
        "age":"age", "sex":"sex", "height":"height", "weight":"weight",
        "goal":"goal", "activityLevel":"activity_level",
        "dietaryPreference":"dietary_preference", "avoidFoods":"avoid_foods",
        "allergies":"allergies", "healthConditions":"health_conditions",
        "calorieTarget":"calorie_target", "macroPreference":"macro_preference",
        "mealsPerDay":"meals_per_day", "mealComplexity":"meal_complexity",
        "mealPrepStyle":"meal_prep_style", "dailyBudget":"daily_budget",
        "cookingTime":"cooking_time", "cookingMethod":"cooking_methods",
        "specialGoals":"special_goals",
    }
    upd = {}
    for k,v in patch.items():
        col = mapping.get(k)
        if col is not None:
            upd[col] = v
    if not upd: return load_user_preferences(user_id) or {}
    upd["updated_at"] = datetime.utcnow().isoformat()
    try:
        res = sb.table("meal_preferences").update(upd).eq("user_id", user_id).execute()
        rows = getattr(res, "data", []) or []
        return rows[0] if rows else (load_user_preferences(user_id) or {})
    except Exception:
        return load_user_preferences(user_id) or {}

def _get_client():
    try:
        from app.db.database import supabase as sb  # project client if available
        if sb: return sb
    except Exception:
        pass
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

def load_user_preferences(user_id: str) -> Optional[Dict[str, Any]]:
    sb = _get_client()
    if not sb: return None
    try:
        res = sb.table("meal_preferences").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        return rows[0] if rows else None
    except Exception:
        return None

def create_user_preferences(user_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    sb = _get_client()
    if not sb: return {}
    row = {
        "user_id": user_id,
        "age": prefs.get("age"),
        "sex": prefs.get("sex"),
        "height": prefs.get("height"),
        "weight": prefs.get("weight"),
        "goal": prefs.get("goal","maintain"),
        "activity_level": prefs.get("activityLevel","moderate"),
        "dietary_preference": prefs.get("dietaryPreference") or [],
        "avoid_foods": prefs.get("avoidFoods") or "",
        "allergies": prefs.get("allergies") or [],
        "health_conditions": prefs.get("healthConditions") or [],
        "calorie_target": prefs.get("calorieTarget", 2000),
        "macro_preference": prefs.get("macroPreference","balanced"),
        "meals_per_day": prefs.get("mealsPerDay", 3),
        "meal_complexity": prefs.get("mealComplexity","simple"),
        "meal_prep_style": prefs.get("mealPrepStyle","daily"),
        "daily_budget": prefs.get("dailyBudget"),
        "cooking_time": prefs.get("cookingTime"),
        "cooking_methods": prefs.get("cookingMethod") or [],
        "special_goals": prefs.get("specialGoals") or [],
    }
    try:
        res = sb.table("meal_preferences").insert(row).execute()
        return (getattr(res, "data", []) or [row])[0]
    except Exception:
        # If unique constraint exists, return existing
        existing = load_user_preferences(user_id)
        if existing: return existing
        return row

def patch_user_preferences(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    sb = _get_client()
    if not sb: return {}
    # Map patch keys -> DB columns (safe updates only)
    mapping = {
        "age":"age", "sex":"sex", "height":"height", "weight":"weight",
        "goal":"goal", "activityLevel":"activity_level",
        "dietaryPreference":"dietary_preference", "avoidFoods":"avoid_foods",
        "allergies":"allergies", "healthConditions":"health_conditions",
        "calorieTarget":"calorie_target", "macroPreference":"macro_preference",
        "mealsPerDay":"meals_per_day", "mealComplexity":"meal_complexity",
        "mealPrepStyle":"meal_prep_style", "dailyBudget":"daily_budget",
        "cookingTime":"cooking_time", "cookingMethod":"cooking_methods",
        "specialGoals":"special_goals",
    }
    upd = {}
    for k,v in patch.items():
        col = mapping.get(k)
        if col is not None:
            upd[col] = v
    if not upd: return load_user_preferences(user_id) or {}
    upd["updated_at"] = datetime.utcnow().isoformat()
    try:
        res = sb.table("meal_preferences").update(upd).eq("user_id", user_id).execute()
        rows = getattr(res, "data", []) or []
        return rows[0] if rows else (load_user_preferences(user_id) or {})
    except Exception:
        return load_user_preferences(user_id) or {}

def save_plan_for_user(user_id: str, plan: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    Persist AI generated plan into generated_plan_meals (NOT meals intake table).
    """
    sb = _get_client()
    if not sb or not user_id: return
    try:
        # Remove previous generated plan meals for this user only
        sb.table(PLAN_TABLE).delete().eq("user_id", user_id).execute()
    except Exception:
        pass

    rows: List[Dict[str, Any]] = []
    for day_key, meals in (plan or {}).items():
        day = _canonical_day(day_key)
        for m in (meals or []):
            macros = m.get("macros") or {}
            rows.append({
                "user_id": user_id,
                "day": day,
                "name": m.get("name","Meal"),
                "meal_type": (m.get("type") or m.get("meal_type") or "Snack"),
                "calories": int(m.get("calories",0)),
                "protein": int(macros.get("protein",0)),
                "carbs": int(macros.get("carbs",0)),
                "fats": int(macros.get("fats",0)),
                "prep_time": int(m.get("prep_time", m.get("prepTime", 20)) or 20),
                "description": m.get("description") or "",
            })
    if not rows:
        return
    try:
        chunk = 500
        for i in range(0,len(rows),chunk):
            sb.table(PLAN_TABLE).insert(rows[i:i+chunk]).execute()
    except Exception:
        pass

def load_saved_plan_for_user(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load previously generated plan from generated_plan_meals.
    Returns normalized 7-day dict; days with no meals -> empty list.
    """
    sb = _get_client()
    if not sb or not user_id:
        return {d: [] for d in DAYS}
    try:
        res = sb.table(PLAN_TABLE).select("*").eq("user_id", user_id).order("day", desc=False).execute()
        rows = getattr(res, "data", []) or []
    except Exception:
        rows = []
    grouped: Dict[str, List[Dict[str, Any]]] = {d: [] for d in DAYS}
    for r in rows:
        day = _canonical_day(r.get("day"))
        grouped[day].append({
            "id": r.get("id"),
            "name": r.get("name"),
            "type": r.get("meal_type"),
            "calories": int(r.get("calories",0)),
            "prep_time": int(r.get("prep_time",20)),
            "description": r.get("description") or "",
            "macros": {
                "protein": int(r.get("protein",0)),
                "carbs": int(r.get("carbs",0)),
                "fats": int(r.get("fats",0)),
            }
        })
    return grouped

@router.get("/plan", response_model=MealPlanResponse)
async def get_generated_plan(request: Request):
    """
    Fetch persisted generated plan (does not include manually logged intake).
    """
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id header")
    plan = load_saved_plan_for_user(user_id)
    # If all empty, treat as not found
    if all(len(v)==0 for v in plan.values()):
        raise HTTPException(status_code=404, detail="No generated plan saved")
    return MealPlanResponse(plan=plan)

@router.post("/generate", response_model=MealPlanResponse)
async def generate_meal_plan(preferences: Dict[str, Any], request: Request):
    try:
        user_id = preferences.get("userId") or request.headers.get("x-user-id")
        _save_preferences(preferences, user_id)
        raw = ai_generate(preferences)  # raw is dict day->list[meals]
        if user_id:
            save_plan_for_user(user_id, raw)
        return MealPlanResponse(plan=raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to generate meal plan")
