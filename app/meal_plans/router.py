from fastapi import APIRouter, Body, HTTPException, Request, status
from typing import Dict, Any, List, Optional
from .ai_service import ai_generate
from .generate import (
    load_user_preferences, create_user_preferences, patch_user_preferences,
    save_plan_for_user
)

router = APIRouter(prefix="/meal-plans", tags=["meal-plans"])

def _get_user_id(preferences: Dict[str, Any], request: Optional[Request]) -> Optional[str]:
    return (preferences or {}).get("userId") or (request.headers.get("x-user-id") if request else None)

@router.get("/preferences")
def get_preferences(request: Request):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing x-user-id")
    prefs = load_user_preferences(user_id)
    if not prefs:
        raise HTTPException(status_code=404, detail="No preferences")
    return {"preferences": prefs}

@router.post("/preferences", status_code=201)
def create_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = _get_user_id(preferences, request)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    existing = load_user_preferences(user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Preferences already exist")
    created = create_user_preferences(user_id, preferences or {})
    return {"preferences": created}

@router.patch("/preferences")
def update_preferences(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = _get_user_id(preferences, request)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")
    existing = load_user_preferences(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No preferences to update")
    updated = patch_user_preferences(user_id, preferences or {})
    return {"preferences": updated}

@router.post("/generate")
def generate_plan(preferences: Dict[str, Any] = Body(default={}), request: Request = None):
    user_id = _get_user_id(preferences, request)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")

    saved_prefs = load_user_preferences(user_id)
    if not saved_prefs:
        # Block generation until preference exists
        raise HTTPException(status_code=412, detail="Meal preferences required")

    # Merge: DB as base, request overrides (light)
    merged = {**saved_prefs, **(preferences or {})}
    # Normalize expected keys
    merged = {
        "goal": merged.get("goal","maintain"),
        "macroPreference": merged.get("macro_preference") or merged.get("macroPreference","balanced"),
        "calorieTarget": merged.get("calorie_target") or merged.get("calorieTarget", 2000),
        "mealsPerDay": merged.get("meals_per_day") or merged.get("mealsPerDay", 3),
        "dietaryPreference": merged.get("dietary_preference") or merged.get("dietaryPreference") or [],
        "avoidFoods": merged.get("avoid_foods") or merged.get("avoidFoods") or "",
        "allergies": merged.get("allergies") or [],
        "specialGoals": merged.get("special_goals") or [],
        "cookingMethod": merged.get("cooking_methods") or [],
    }

    plan = ai_generate(merged)
    if not isinstance(plan, dict):
        raise HTTPException(status_code=500, detail="AI returned invalid plan")

    # Save plan for this user
    try:
        save_plan_for_user(user_id, plan)
    except Exception:
        # Non-fatal
        pass

    return {"plan": plan}