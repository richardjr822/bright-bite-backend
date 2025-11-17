from fastapi import APIRouter, HTTPException, Request, Body, Query
from typing import Dict, Any, List, Optional, Tuple
import os
from datetime import datetime, timedelta, timezone
from app.meal_plans.ai_service import ai_generate, preference_signature
import hashlib

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/meal-plans", tags=["meal-plans"])

# ---------- Config ----------
PLAN_TABLE = os.getenv("GENERATED_PLAN_TABLE", "generated_plan_meals")

# ---------- Supabase helper ----------
def _client():
    if supabase:
        return supabase
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None

# ---------- Date helpers ----------
def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _today_bounds_utc() -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = datetime(year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start.isoformat(), end.isoformat()

def _sum_macros(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    total = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fats": 0.0}
    for r in rows or []:
        total["calories"] += float(r.get("calories", 0) or 0)
        total["protein"] += float(r.get("protein", 0) or 0)
        total["carbs"] += float(r.get("carbs", 0) or 0)
        total["fats"] += float(r.get("fats", 0) or 0)
    return {k: round(v, 2) for k, v in total.items()}

# ---------- Preferences (single row per user) ----------
def _load_prefs(user_id: str) -> Optional[Dict[str, Any]]:
    sb = _client()
    if not sb: return None
    try:
        r = sb.table("meal_preferences").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(r, "data", []) or []
        return rows[0] if rows else None
    except Exception:
        return None

def _create_prefs(user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    sb = _client()
    if not sb: return {}
    row = {
        "user_id": user_id,
        "age": data.get("age"),
        "sex": data.get("sex"),
        "height": data.get("height"),
        "weight": data.get("weight"),
        "goal": data.get("goal", "maintain"),
        "activity_level": data.get("activityLevel", "moderate"),
        "dietary_preference": data.get("dietaryPreference", []),
        "avoid_foods": data.get("avoidFoods", ""),
        "allergies": data.get("allergies", []),
        "health_conditions": data.get("healthConditions", []),
        "calorie_target": data.get("calorieTarget", 2000),
        "macro_preference": data.get("macroPreference", "balanced"),
        "meals_per_day": data.get("mealsPerDay", 3),
        "meal_complexity": data.get("mealComplexity", "simple"),
        "meal_prep_style": data.get("mealPrepStyle", "daily"),
        "daily_budget": data.get("dailyBudget"),
        "cooking_time": data.get("cookingTime"),
        "cooking_methods": data.get("cookingMethod", []),
        "special_goals": data.get("specialGoals", []),
        "appetite": data.get("appetite", "normal"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        r = sb.table("meal_preferences").insert(row).execute()
        return (getattr(r, "data", []) or [row])[0]
    except Exception:
        existing = _load_prefs(user_id)
        return existing or row

def _patch_prefs(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    sb = _client()
    if not sb: return {}
    mapping = {
        "age":"age","sex":"sex","height":"height","weight":"weight",
        "goal":"goal","activityLevel":"activity_level",
        "dietaryPreference":"dietary_preference","avoidFoods":"avoid_foods",
        "allergies":"allergies","healthConditions":"health_conditions",
        "calorieTarget":"calorie_target","macroPreference":"macro_preference",
        "mealsPerDay":"meals_per_day","mealComplexity":"meal_complexity",
        "mealPrepStyle":"meal_prep_style","dailyBudget":"daily_budget",
        "cookingTime":"cooking_time","cookingMethod":"cooking_methods",
        "specialGoals":"special_goals","appetite":"appetite",
    }
    upd = {}
    for k,v in patch.items():
        col = mapping.get(k)
        if col is not None:
            upd[col] = v
    if not upd: return _load_prefs(user_id) or {}
    upd["updated_at"] = _now_iso()
    try:
        r = sb.table("meal_preferences").upsert({**upd, "user_id": user_id}).execute()
        rows = getattr(r, "data", []) or []
        return rows[0] if rows else (_load_prefs(user_id) or {})
    except Exception:
        return _load_prefs(user_id) or {}

# ---------- Meal plan storage ----------
def _save_plan(user_id: str, plan: Dict[str, List[Dict[str, Any]]] ):
    """Persist generated plan meals into separate table generated_plan_meals, leaving user intake (meals) untouched.

    Table expected schema (Postgres suggestion):
      id (bigint PK), user_id text, day text, name text, meal_type text, calories int,
      protein int, carbs int, fats int, prep_time int, description text, created_at timestamptz default now()

    This function is resilient: if table doesn't exist or insertion fails, it silently returns.
    """
    sb = _client()
    if not sb:
        return
    rows = []
    for day, meals in (plan or {}).items():
        for m in meals or []:
            macros = m.get("macros") or {}
            rows.append({
                "user_id": user_id,
                "day": day,
                "name": m.get("name", "Meal"),
                "meal_type": (m.get("type") or m.get("meal_type") or "snack").lower(),
                "calories": int(m.get("calories", 0) or 0),
                "protein": int(macros.get("protein", 0) or 0),
                "carbs": int(macros.get("carbs", 0) or 0),
                "fats": int(macros.get("fats", 0) or 0),
                "prep_time": int(m.get("prep_time", 20) or 20),
                "description": m.get("description", "Generated meal."),
            })
    if not rows:
        return
    try:
        # Remove previous generated meals for user
        sb.table(PLAN_TABLE).delete().eq("user_id", user_id).execute()
    except Exception:
        # table may not exist yet; abort persistence
        return
    try:
        chunk = 500
        for i in range(0, len(rows), chunk):
            sb.table(PLAN_TABLE).insert(rows[i:i+chunk]).execute()
    except Exception:
        # Ignore partial failures
        pass

def _load_saved_plan(user_id: str, meals_per_day: int) -> Dict[str, List[Dict[str, Any]]]:
    """Load generated plan from generated_plan_meals table grouped by day.

    If table missing or empty for user, returns empty dict.
    """
    sb = _client()
    if not sb:
        return {}
    try:
        r = sb.table(PLAN_TABLE).select("*").eq("user_id", user_id).order("day", desc=False).order("id", desc=False).execute()
        rows = getattr(r, "data", []) or []
    except Exception:
        return {}
    if not rows:
        return {}
    days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    out: Dict[str, List[Dict[str, Any]]] = {d: [] for d in days}
    for row in rows:
        day = (row.get("day") or "").lower()
        if day not in out:
            continue
        out[day].append({
            "id": str(row.get("id")),
            "name": row.get("name"),
            "type": row.get("meal_type"),
            "calories": int(row.get("calories", 0) or 0),
            "prep_time": int(row.get("prep_time", 20) or 20),
            "description": row.get("description") or "Generated meal.",
            "macros": {
                "protein": int(row.get("protein", 0) or 0),
                "carbs": int(row.get("carbs", 0) or 0),
                "fats": int(row.get("fats", 0) or 0),
            }
        })
    return out

# ---------- Routes: Preferences ----------
@router.get("/preferences")
def get_preferences(request: Request):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id")
    prefs = _load_prefs(user_id)
    # Return empty preferences for new users instead of 404
    return {"preferences": prefs or {}}

@router.post("/preferences", status_code=201)
def create_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    existing = _load_prefs(user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Preferences already exist")
    created = _create_prefs(user_id, preferences or {})
    return {"preferences": created}

@router.patch("/preferences")
def patch_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    if not _load_prefs(user_id):
        raise HTTPException(status_code=404, detail="No preferences to update")
    updated = _patch_prefs(user_id, preferences or {})
    return {"preferences": updated}

# Optional: fetch by path user_id (handy for admin/tools)
@router.get("/preferences/{user_id}")
def get_preferences_by_user(user_id: str):
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    prefs = _load_prefs(user_id)
    # Return empty preferences for new users instead of 404
    return {"preferences": prefs or {}}

def _update_plan_hash(user_id: str, plan_hash: str):
    sb = _client()
    if not sb: return
    try:
        sb.table("meal_preferences").update({"plan_hash": plan_hash, "plan_updated_at": _now_iso(), "updated_at": _now_iso()}).eq("user_id", user_id).execute()
    except Exception:
        pass

def _get_plan_hash(user_id: str) -> Optional[str]:
    prefs = _load_prefs(user_id)
    if not prefs:
        return None
    return prefs.get("plan_hash")

def _has_saved_plan(user_id: str) -> bool:
    sb = _client()
    if not sb:
        return False
    try:
        r = sb.table(PLAN_TABLE).select("id").eq("user_id", user_id).limit(1).execute()
        rows = getattr(r, "data", []) or []
        return bool(rows)
    except Exception:
        return False

# ---------- Routes: AI Plan Generation ----------
@router.post("/generate")
def generate_plan(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    saved = _load_prefs(user_id)
    if not saved:
        raise HTTPException(status_code=412, detail="Meal preferences required")

    force = bool(preferences.get("force") or preferences.get("force_regenerate"))
    merged = {**saved, **(preferences or {})}
    norm = {
        "goal": merged.get("goal","maintain"),
        "macroPreference": merged.get("macro_preference") or merged.get("macroPreference","balanced"),
        "calorieTarget": merged.get("calorie_target") or merged.get("calorieTarget",2000),
        "mealsPerDay": merged.get("meals_per_day") or merged.get("mealsPerDay",3),
        "dietaryPreference": merged.get("dietary_preference") or merged.get("dietaryPreference") or [],
        "avoidFoods": merged.get("avoid_foods") or merged.get("avoidFoods") or "",
        "allergies": merged.get("allergies") or [],
        "specialGoals": merged.get("special_goals") or [],
        "cookingMethod": merged.get("cooking_methods") or [],
        "mealComplexity": merged.get("meal_complexity") or merged.get("mealComplexity","simple"),
        "mealPrepStyle": merged.get("meal_prep_style") or merged.get("mealPrepStyle","daily"),
        "dailyBudget": merged.get("daily_budget") or merged.get("dailyBudget"),
        "cookingTime": merged.get("cooking_time") or merged.get("cookingTime"),
        "healthConditions": merged.get("health_conditions") or merged.get("healthConditions") or [],
        "appetite": merged.get("appetite") or "normal",
    }

    current_hash = preference_signature(norm)
    existing_hash = _get_plan_hash(user_id)
    has_plan = _has_saved_plan(user_id)

    # If not forced and hashes match + generated plan exists, reuse stored generated plan.
    if not force and existing_hash and existing_hash == current_hash and has_plan:
        meals_per_day = int(norm.get("mealsPerDay") or 3)
        plan = _load_saved_plan(user_id, meals_per_day)
        if plan:
            return {"plan": plan, "reused": True, "persisted": True}

    plan = ai_generate(norm)
    if not isinstance(plan, dict):
        raise HTTPException(status_code=500, detail="AI returned invalid plan")

    _save_plan(user_id, plan)
    _update_plan_hash(user_id, current_hash)
    return {"plan": plan, "reused": False, "persisted": True}

@router.get("/plan")
def get_saved_plan(request: Request):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id")
    prefs = _load_prefs(user_id)
    if not prefs:
        raise HTTPException(status_code=412, detail="Meal preferences required")
    meals_per_day = int(prefs.get("meals_per_day", 3) or 3)
    plan = _load_saved_plan(user_id, meals_per_day)
    if not plan or all(len(v) == 0 for v in plan.values()):
        raise HTTPException(status_code=404, detail="No saved generated plan")
    return {"plan": plan, "plan_hash": prefs.get("plan_hash")}

# ---------- Routes: Meal Logging ----------
@router.post("/meals", status_code=201)
def log_meal(meal: Dict[str, Any] = Body(default={}), request: Request = None):
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase client not configured")
    user_id = (meal or {}).get("user_id") or (meal or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    name = (meal or {}).get("name")
    meal_type = ((meal or {}).get("meal_type") or (meal or {}).get("mealType") or "snack").lower()
    if not name:
        raise HTTPException(status_code=400, detail="Missing meal name")

    def _to_num(v) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    row = {
        "user_id": user_id,
        "name": name,
        "meal_type": meal_type,
        "calories": _to_num((meal or {}).get("calories")),
        "protein": _to_num((meal or {}).get("protein")),
        "carbs": _to_num((meal or {}).get("carbs")),
        "fats": _to_num((meal or {}).get("fats")),
        "meal_time": (meal or {}).get("meal_time") or (meal or {}).get("mealTime") or _now_iso(),
        # created_at uses DB default
    }
    try:
        res = sb.table("meals").insert(row).execute()
        rows = getattr(res, "data", []) or []
        created = rows[0] if rows else row
        return {"meal": created}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log meal: {e}")

@router.get("/meals")
def list_meals(today: bool = Query(False, description="If true, only return today's meals"), request: Request = None):
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase client not configured")
    user_id = request.headers.get("x-user-id") if request else None
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id")
    try:
        query = sb.table("meals").select("*").eq("user_id", user_id)
        if today:
            start_iso, end_iso = _today_bounds_utc()
            query = query.gte("meal_time", start_iso).lte("meal_time", end_iso)
        res = query.order("meal_time", desc=True).execute()
        meals = getattr(res, "data", []) or []
        return {"meals": meals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list meals: {e}")

@router.get("/meals/summary")
def meals_summary_today(request: Request = None):
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase client not configured")
    user_id = request.headers.get("x-user-id") if request else None
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id")
    try:
        start_iso, end_iso = _today_bounds_utc()
        res = (
            sb.table("meals")
            .select("*")
            .eq("user_id", user_id)
            .gte("meal_time", start_iso)
            .lte("meal_time", end_iso)
            .execute()
        )
        meals = getattr(res, "data", []) or []
        totals = _sum_macros(meals)
        return {"summary": {"date": start_iso[:10], "totals": totals, "count": len(meals)}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute summary: {e}")

def preference_signature(prefs: dict) -> str:
    keys = ["goal","macroPreference","calorieTarget","mealsPerDay","dietaryPreference",
            "avoidFoods","allergies","healthConditions","specialGoals","dailyBudget",
            "cookingTime","cookingMethod","mealComplexity","mealPrepStyle","appetite"]
    parts = []
    for k in keys:
        v = prefs.get(k)
        if isinstance(v, list):
            parts.append(f"{k}=" + ",".join(sorted(map(str, v))))
        else:
            parts.append(f"{k}={v}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()

def _prompt(prefs: dict, split: list[int]) -> str:
    return f"""
Return ONLY valid JSON (no markdown).

Object shape:
{{
  "monday":[Meal], ... "sunday":[Meal]
}}
Meal {{
  "id": string? (omit if unknown),
  "name": string (Filipino / accessible dish),
  "type": "Breakfast"|"Lunch"|"Dinner"|"Snack",
  "calories": int,
  "prep_time": int,
  "description": string (<=180 chars; MUST explain why it matches: goal={prefs.get('goal')}, macro={prefs.get('macroPreference')},
     appetite={prefs.get('appetite')}, budget={prefs.get('dailyBudget','n/a')} PHP, cooking methods {', '.join(prefs.get('cookingMethod', [])) or 'flexible'},
     special goals {', '.join(prefs.get('specialGoals', [])) or 'none'}, health conditions {', '.join(prefs.get('healthConditions', [])) or 'none'},
     dietary prefs {', '.join(prefs.get('dietaryPreference', [])) or 'none'}, allergy avoidance {', '.join(prefs.get('allergies', [])) or 'none'};
     Mention support for weight gain if goal=gain (higher calorie & protein), fat loss if goal=lose (lean protein & fiber), maintain if balanced.
     NO ingredient list, NO cooking steps.)
  "macros": {{ "protein": int, "carbs": int, "fats": int }}
}}

Targets:
- daily calories ≈ {int(prefs.get('calorieTarget',2000))}
- meals/day = {int(prefs.get('mealsPerDay',3))}
- approximate meal split = {split}

Constraints:
- Respect dietary preferences: {', '.join(prefs.get('dietaryPreference', [])) or 'none'}
- Avoid allergies: {', '.join(prefs.get('allergies', [])) or 'none'}
- Avoid foods: {prefs.get('avoidFoods','none')}
- Complexity: {prefs.get('mealComplexity','simple')} / cooking time ≤ {prefs.get('cookingTime','flex')}
- Use cooking methods when possible: {', '.join(prefs.get('cookingMethod', [])) or 'any'}
- Appetite {prefs.get('appetite','average')} (light=slightly smaller portions; heavy=slightly larger within total)
- Variety: do not repeat exact dish name more than twice/week.

Output ONLY JSON.
""".strip()

# In /generate route ensure existing plan reuse:
# (Make sure you previously added plan_hash + plan reuse logic)

