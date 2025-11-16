from fastapi import APIRouter, Request, Body, HTTPException, Query
from typing import Any, Dict
from app.api.endpoints import meal_plans as mp  # reuse helpers & Supabase client logic

router = APIRouter(tags=["nutrition"])

# ----- Preferences Aliases -----
@router.get("/meal-preferences")
def get_my_preferences(request: Request):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id header")
    prefs = mp._load_prefs(user_id)
    if not prefs:
        raise HTTPException(status_code=404, detail="No preferences")
    return {"success": True, "data": prefs}

@router.get("/meal-preferences/{user_id}")
def get_preferences_by_user(user_id: str):
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    prefs = mp._load_prefs(user_id)
    if not prefs:
        raise HTTPException(status_code=404, detail="No preferences")
    return {"success": True, "data": prefs}

@router.post("/meal-preferences", status_code=201)
def create_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    existing = mp._load_prefs(user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Preferences already exist")
    created = mp._create_prefs(user_id, preferences or {})
    return {"success": True, "data": created}

@router.patch("/meal-preferences")
def patch_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    if not mp._load_prefs(user_id):
        raise HTTPException(status_code=404, detail="No preferences to update")
    updated = mp._patch_prefs(user_id, preferences or {})
    return {"success": True, "data": updated}

# ----- Meal Logging Aliases -----
@router.post("/meals", status_code=201)
def log_meal(meal: Dict[str, Any] = Body(default={}), request: Request = None):
    # Delegate to original logic, then wrap response
    req = request  # original function expects request param name 'request'
    result = mp.log_meal(meal=meal, request=req)
    return {"success": True, "data": result.get("meal")}

@router.get("/meals/{user_id}")
def list_meals_for_user(user_id: str, today: bool = Query(False, description="If true, only return today's meals")):
    # Original list_meals pulls user from header; we reproduce logic using helpers
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    # Temporarily forge request-like object not needed; re-implement minimal query using mp._client()
    sb = mp._client()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase client not configured")
    try:
        query = sb.table("meals").select("*").eq("user_id", user_id)
        if today:
            start_iso, end_iso = mp._today_bounds_utc()
            query = query.gte("meal_time", start_iso).lte("meal_time", end_iso)
        res = query.order("meal_time", desc=True).execute()
        meals = getattr(res, "data", []) or []
        return {"success": True, "data": meals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list meals: {e}")

@router.get("/meals/{user_id}/summary")
def meals_summary(user_id: str):
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    sb = mp._client()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase client not configured")
    try:
        start_iso, end_iso = mp._today_bounds_utc()
        res = sb.table("meals").select("*").eq("user_id", user_id).gte("meal_time", start_iso).lte("meal_time", end_iso).execute()
        meals = getattr(res, "data", []) or []
        totals = mp._sum_macros(meals)
        summary = {"date": start_iso[:10], "totals": totals, "count": len(meals)}
        return {"success": True, "data": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute summary: {e}")

# ----- Plan Generation Alias -----
@router.post("/meal-plans/generate")
def generate_plan(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    # Call existing generate endpoint logic
    result = mp.generate_plan(preferences=preferences, request=request)
    return {"success": True, "data": result.get("plan"), "reused": result.get("reused", False)}
