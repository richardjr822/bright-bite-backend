"""
Student Insights & Analytics Module
- Goal generation from meal preferences
- Engagement tracking
- Vendor access to aggregate student data
- Recommendation engine
"""

from fastapi import APIRouter, HTTPException, Request, Body, Query
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
import os
import sys
import hashlib

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/insights", tags=["insights"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"


def _client():
    return supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_user_from_token(req: Request) -> Optional[Dict[str, Any]]:
    """Extract user info from JWT token."""
    auth = req.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.replace("Bearer ", "").strip()
        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return data
        except JWTError:
            pass
    return None


def _get_user_id(req: Request, payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    data = _get_user_from_token(req)
    if data and data.get("sub"):
        return str(data.get("sub"))
    if req.headers.get("x-user-id"):
        return req.headers.get("x-user-id")
    if payload and payload.get("userId"):
        return str(payload.get("userId"))
    return None


# ==================== PRIVACY AGREEMENT ====================

@router.get("/privacy-status")
def get_privacy_status(request: Request):
    """Check if user has agreed to privacy terms."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        res = sb.table("users").select("agreed_to_terms").eq("id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if not rows:
            return {"agreed": False, "requires_agreement": True}
        
        agreed = rows[0].get("agreed_to_terms", False)
        return {"agreed": agreed, "requires_agreement": not agreed}
    except Exception as e:
        print(f"[privacy-status] Error: {e}", file=sys.stderr)
        return {"agreed": False, "requires_agreement": True}


@router.post("/accept-privacy")
def accept_privacy(request: Request, payload: Dict[str, Any] = Body(default={})):
    """Record user's acceptance of privacy terms (first login requirement)."""
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        sb.table("users").update({
            "agreed_to_terms": True,
            "updated_at": _now_iso()
        }).eq("id", user_id).execute()
        
        # Log engagement event
        _log_engagement(user_id, "privacy_accepted", {"timestamp": _now_iso()})
        
        return {"success": True, "message": "Privacy terms accepted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update: {e}")


# ==================== GOAL GENERATION & INSIGHTS ====================

def _calculate_bmr(age: int, sex: str, weight: float, height: float) -> float:
    """Calculate Basal Metabolic Rate using Mifflin-St Jeor equation."""
    if sex == "male":
        return 10 * weight + 6.25 * height - 5 * age + 5
    else:
        return 10 * weight + 6.25 * height - 5 * age - 161


def _calculate_tdee(bmr: float, activity_level: str) -> float:
    """Calculate Total Daily Energy Expenditure."""
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "very": 1.725,
        "extra": 1.9
    }
    return bmr * multipliers.get(activity_level, 1.55)


def _generate_goal_insights(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Generate personalized dietary goals and insights from user preferences."""
    
    age = int(prefs.get("age") or 25)
    sex = prefs.get("sex") or "male"
    weight = float(prefs.get("weight") or 70)
    height = float(prefs.get("height") or 170)
    goal = prefs.get("goal") or "maintain"
    activity_level = prefs.get("activity_level") or "moderate"
    calorie_target = int(prefs.get("calorie_target") or 2000)
    dietary_prefs = prefs.get("dietary_preference") or []
    health_conditions = prefs.get("health_conditions") or []
    
    # Calculate metabolic rates
    bmr = _calculate_bmr(age, sex, weight, height)
    tdee = _calculate_tdee(bmr, activity_level)
    
    # Adjust based on goal
    if goal == "lose":
        recommended_calories = int(tdee - 500)  # 500 cal deficit
        goal_description = "Weight Loss"
        primary_focus = "Create a sustainable calorie deficit while maintaining protein intake"
    elif goal == "gain":
        recommended_calories = int(tdee + 300)  # 300 cal surplus
        goal_description = "Weight Gain"
        primary_focus = "Build lean mass with quality calories and adequate protein"
    else:
        recommended_calories = int(tdee)
        goal_description = "Maintain Weight"
        primary_focus = "Balance your nutrition to maintain current body composition"
    
    # Calculate macro targets
    protein_target = int(weight * 1.6) if goal == "gain" else int(weight * 1.2)
    
    # Generate personalized insights
    insights = []
    
    # Goal-based insight
    insights.append({
        "type": "goal",
        "title": f"Your Goal: {goal_description}",
        "description": primary_focus,
        "priority": "high"
    })
    
    # Calorie insight
    cal_diff = calorie_target - recommended_calories
    if abs(cal_diff) > 200:
        insights.append({
            "type": "calorie",
            "title": "Calorie Target Adjustment",
            "description": f"Your target ({calorie_target} cal) differs from recommended ({recommended_calories} cal). Consider adjusting.",
            "priority": "medium"
        })
    
    # Dietary preference insights
    if "keto" in dietary_prefs or "low-carb" in dietary_prefs:
        insights.append({
            "type": "dietary",
            "title": "Low-Carb Focus",
            "description": "Prioritizing healthy fats and proteins while limiting carbohydrates.",
            "priority": "medium"
        })
    
    if "vegetarian" in dietary_prefs or "vegan" in dietary_prefs:
        insights.append({
            "type": "dietary",
            "title": "Plant-Based Nutrition",
            "description": "Ensure adequate protein from varied plant sources like legumes and tofu.",
            "priority": "medium"
        })
    
    # Health condition insights
    if "diabetes" in health_conditions:
        insights.append({
            "type": "health",
            "title": "Blood Sugar Management",
            "description": "Focus on low-glycemic foods and consistent meal timing.",
            "priority": "high"
        })
    
    if "hypertension" in health_conditions:
        insights.append({
            "type": "health",
            "title": "Heart Health",
            "description": "Limit sodium intake and increase potassium-rich foods.",
            "priority": "high"
        })
    
    # Weekly targets
    weekly_targets = {
        "calories": recommended_calories * 7,
        "protein": protein_target * 7,
        "water_liters": 2.5 * 7,
        "meals": int(prefs.get("meals_per_day") or 3) * 7
    }
    
    return {
        "user_metrics": {
            "bmr": round(bmr, 0),
            "tdee": round(tdee, 0),
            "recommended_calories": recommended_calories,
            "protein_target": protein_target
        },
        "goal_summary": {
            "goal": goal,
            "description": goal_description,
            "primary_focus": primary_focus
        },
        "insights": insights,
        "weekly_targets": weekly_targets,
        "generated_at": _now_iso()
    }


@router.get("/goals")
def get_user_goals(request: Request):
    """Generate and return personalized dietary goals based on meal preferences."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    # Fetch meal preferences
    try:
        res = sb.table("meal_preferences").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if not rows:
            return {
                "success": False,
                "message": "No meal preferences found. Complete your profile first.",
                "has_preferences": False
            }
        
        prefs = rows[0]
        insights = _generate_goal_insights(prefs)
        
        # Log engagement
        _log_engagement(user_id, "viewed_goals", {})
        
        return {
            "success": True,
            "has_preferences": True,
            **insights
        }
    except Exception as e:
        print(f"[goals] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ENGAGEMENT TRACKING ====================

def _log_engagement(user_id: str, event_type: str, metadata: Dict[str, Any] = None):
    """Log user engagement event."""
    sb = _client()
    if not sb:
        return
    
    try:
        row = {
            "user_id": user_id,
            "event_type": event_type,
            "metadata": metadata or {},
            "created_at": _now_iso()
        }
        sb.table("engagement_events").insert(row).execute()
    except Exception as e:
        # Table might not exist yet - fail silently
        print(f"[engagement] Log failed (table may not exist): {e}", file=sys.stderr)


@router.post("/track-event")
def track_engagement_event(request: Request, payload: Dict[str, Any] = Body(default={})):
    """Track a user engagement event."""
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    event_type = payload.get("event_type") or payload.get("eventType")
    if not event_type:
        raise HTTPException(status_code=400, detail="event_type is required")
    
    allowed_events = {
        "page_view", "meal_plan_generated", "meal_logged", "order_placed",
        "feedback_submitted", "preferences_updated", "goal_viewed",
        "recommendation_clicked", "vendor_viewed", "search_performed"
    }
    
    if event_type not in allowed_events:
        event_type = "custom"
    
    metadata = payload.get("metadata") or {}
    _log_engagement(user_id, event_type, metadata)
    
    return {"success": True}


@router.get("/engagement-summary")
def get_engagement_summary(request: Request):
    """Get engagement summary for the current user."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        # Get events from last 30 days
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        
        res = sb.table("engagement_events") \
            .select("event_type, created_at") \
            .eq("user_id", user_id) \
            .gte("created_at", thirty_days_ago) \
            .execute()
        
        events = getattr(res, "data", []) or []
        
        # Aggregate by event type
        event_counts = {}
        for e in events:
            et = e.get("event_type", "unknown")
            event_counts[et] = event_counts.get(et, 0) + 1
        
        # Calculate engagement score (0-100)
        total_events = len(events)
        days_active = len(set(e.get("created_at", "")[:10] for e in events))
        
        engagement_score = min(100, int(
            (days_active / 30) * 50 +  # Activity consistency
            min(total_events / 100, 1) * 50  # Event volume
        ))
        
        return {
            "success": True,
            "summary": {
                "total_events": total_events,
                "days_active": days_active,
                "engagement_score": engagement_score,
                "event_breakdown": event_counts
            },
            "period": "last_30_days"
        }
    except Exception as e:
        # Table might not exist
        return {
            "success": True,
            "summary": {
                "total_events": 0,
                "days_active": 0,
                "engagement_score": 0,
                "event_breakdown": {}
            },
            "period": "last_30_days"
        }


# ==================== VENDOR ANALYTICS (Student Insights) ====================

@router.get("/vendor/student-analytics")
def get_vendor_student_analytics(request: Request):
    """
    Vendor-only endpoint to view aggregate student preferences and demand insights.
    """
    user_data = _get_user_from_token(request)
    if not user_data:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if user_data.get("role") not in ["vendor", "admin"]:
        raise HTTPException(status_code=403, detail="Vendor access required")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        # Aggregate meal preferences - get ALL rows then deduplicate by user_id
        prefs_res = sb.table("meal_preferences").select("*").order("updated_at", desc=True).execute()
        all_prefs = getattr(prefs_res, "data", []) or []
        
        # Deduplicate: keep only the latest preference per user_id
        seen_users = set()
        prefs = []
        for p in all_prefs:
            user_id = p.get("user_id")
            if user_id and user_id not in seen_users:
                seen_users.add(user_id)
                prefs.append(p)
        
        # Aggregate dietary preferences
        dietary_counts = {}
        goal_counts = {"lose": 0, "maintain": 0, "gain": 0}
        allergy_counts = {}
        avg_calorie_target = 0
        total_users = len(prefs)
        
        for p in prefs:
            # Goals
            goal = p.get("goal") or "maintain"
            goal_counts[goal] = goal_counts.get(goal, 0) + 1
            
            # Dietary preferences
            diets = p.get("dietary_preference") or []
            for d in diets:
                dietary_counts[d] = dietary_counts.get(d, 0) + 1
            
            # Allergies
            allergies = p.get("allergies") or []
            for a in allergies:
                allergy_counts[a] = allergy_counts.get(a, 0) + 1
            
            # Calorie target
            cal = p.get("calorie_target")
            if cal:
                avg_calorie_target += int(cal)
        
        if total_users > 0:
            avg_calorie_target = round(avg_calorie_target / total_users)
        
        # Get popular ordered items (from orders table)
        popular_items = []
        try:
            orders_res = sb.table("orders").select("items").limit(500).execute()
            orders = getattr(orders_res, "data", []) or []
            
            item_counts = {}
            for o in orders:
                items = o.get("items") or []
                for item in items:
                    name = item.get("name", "Unknown")
                    item_counts[name] = item_counts.get(name, 0) + item.get("quantity", 1)
            
            # Sort by count
            sorted_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            popular_items = [{"name": name, "order_count": count} for name, count in sorted_items]
        except Exception:
            pass
        
        return {
            "success": True,
            "analytics": {
                "total_students_profiled": total_users,
                "goal_distribution": goal_counts,
                "dietary_preferences": dietary_counts,
                "common_allergies": allergy_counts,
                "average_calorie_target": avg_calorie_target,
                "popular_items": popular_items
            },
            "insights": [
                {
                    "type": "demand",
                    "title": "Top Goal",
                    "value": max(goal_counts, key=goal_counts.get) if goal_counts else "maintain"
                },
                {
                    "type": "dietary",
                    "title": "Most Common Diet",
                    "value": max(dietary_counts, key=dietary_counts.get) if dietary_counts else "balanced"
                }
            ],
            "generated_at": _now_iso()
        }
    except Exception as e:
        print(f"[vendor-analytics] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== RECOMMENDATION ENGINE ====================

@router.get("/recommendations")
def get_meal_recommendations(request: Request):
    """
    Get meal recommendations comparing:
    1. Algorithmic recommendations (based on user profile)
    2. Vendor available meals
    3. Vendor recommended meals (popular/promoted)
    """
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        # 1. Get user preferences
        prefs_res = sb.table("meal_preferences").select("*").eq("user_id", user_id).limit(1).execute()
        prefs = (getattr(prefs_res, "data", []) or [{}])[0]
        
        user_goal = prefs.get("goal") or "maintain"
        user_diets = prefs.get("dietary_preference") or []
        user_allergies = prefs.get("allergies") or []
        calorie_target = int(prefs.get("calorie_target") or 2000)
        meals_per_day = int(prefs.get("meals_per_day") or 3)
        per_meal_cal = calorie_target // meals_per_day
        
        # 2. Get all available vendor menu items
        menu_res = sb.table("menu_items").select("*").eq("is_available", True).execute()
        menu_items = getattr(menu_res, "data", []) or []
        
        # 3. Get order history for popularity scoring
        orders_res = sb.table("orders").select("items").limit(200).execute()
        orders = getattr(orders_res, "data", []) or []
        
        item_popularity = {}
        for o in orders:
            for item in (o.get("items") or []):
                item_id = item.get("id")
                if item_id:
                    item_popularity[item_id] = item_popularity.get(item_id, 0) + 1
        
        # 4. Score and categorize items
        def score_item(item: Dict) -> float:
            """Score an item based on how well it matches user preferences."""
            score = 50  # Base score
            
            calories = float(item.get("calories") or 0)
            protein = float(item.get("protein") or 0)
            
            # Calorie alignment (±200 cal from per-meal target)
            if abs(calories - per_meal_cal) < 100:
                score += 20
            elif abs(calories - per_meal_cal) < 200:
                score += 10
            
            # Goal alignment
            if user_goal == "gain" and protein > 25:
                score += 15
            elif user_goal == "lose" and calories < per_meal_cal:
                score += 15
            
            # Vegetarian check
            if "vegetarian" in user_diets or "vegan" in user_diets:
                if item.get("is_vegetarian"):
                    score += 10
                else:
                    score -= 30
            
            # Popularity bonus
            pop = item_popularity.get(item.get("id"), 0)
            score += min(pop * 2, 20)
            
            return min(100, max(0, score))
        
        # Score all items
        scored_items = []
        for item in menu_items:
            score = score_item(item)
            scored_items.append({
                **item,
                "match_score": score,
                "recommendation_reason": _get_recommendation_reason(item, user_goal, score)
            })
        
        # Sort by score
        scored_items.sort(key=lambda x: x["match_score"], reverse=True)
        
        # 5. Categorize recommendations
        algorithmic = scored_items[:6]  # Top matches based on profile
        
        # Vendor available (all items, sorted by category)
        vendor_available = sorted(menu_items, key=lambda x: x.get("category", ""))[:12]
        
        # Vendor recommended (most popular)
        vendor_recommended = sorted(
            menu_items, 
            key=lambda x: item_popularity.get(x.get("id"), 0), 
            reverse=True
        )[:6]
        
        # Log engagement
        _log_engagement(user_id, "recommendation_clicked", {"count": len(algorithmic)})
        
        return {
            "success": True,
            "recommendations": {
                "algorithmic": {
                    "title": "Personalized For You",
                    "description": f"Based on your {user_goal} goal and preferences",
                    "items": algorithmic
                },
                "vendor_available": {
                    "title": "All Available Meals",
                    "description": "Currently available from campus vendors",
                    "items": vendor_available
                },
                "vendor_recommended": {
                    "title": "Popular Choices",
                    "description": "Most ordered by students",
                    "items": vendor_recommended
                }
            },
            "user_context": {
                "goal": user_goal,
                "calorie_target": calorie_target,
                "dietary_preferences": user_diets
            }
        }
    except Exception as e:
        print(f"[recommendations] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


def _get_recommendation_reason(item: Dict, goal: str, score: float) -> str:
    """Generate a human-readable reason for the recommendation."""
    reasons = []
    
    calories = float(item.get("calories") or 0)
    protein = float(item.get("protein") or 0)
    
    if score > 80:
        reasons.append("Excellent match for your profile")
    elif score > 60:
        reasons.append("Good match")
    
    if goal == "gain" and protein > 25:
        reasons.append("High protein")
    elif goal == "lose" and calories < 400:
        reasons.append("Low calorie")
    
    if item.get("is_vegetarian"):
        reasons.append("Vegetarian")
    
    return " • ".join(reasons) if reasons else "Available now"


# ==================== NEXT WEEK MEALS PREVIEW ====================

@router.get("/next-week-preview")
def get_next_week_preview(request: Request):
    """Preview meals planned for next week."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        # Get saved meal plan
        res = sb.table("generated_plan_meals") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("day") \
            .execute()
        
        rows = getattr(res, "data", []) or []
        
        if not rows:
            return {
                "success": True,
                "has_plan": False,
                "message": "No meal plan generated yet. Generate one from your preferences."
            }
        
        # Group by day
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        plan = {d: [] for d in days}
        
        for row in rows:
            day = (row.get("day") or "").lower()
            if day in plan:
                plan[day].append({
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "type": row.get("meal_type"),
                    "calories": row.get("calories"),
                    "description": row.get("description"),
                    "macros": {
                        "protein": row.get("protein"),
                        "carbs": row.get("carbs"),
                        "fats": row.get("fats")
                    }
                })
        
        # Calculate daily totals
        daily_summary = {}
        for day, meals in plan.items():
            total_cal = sum(m.get("calories", 0) for m in meals)
            daily_summary[day] = {
                "meal_count": len(meals),
                "total_calories": total_cal
            }
        
        return {
            "success": True,
            "has_plan": True,
            "plan": plan,
            "daily_summary": daily_summary,
            "week_label": "Next Week Preview"
        }
    except Exception as e:
        print(f"[next-week] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== FEEDBACK RANKING INTEGRATION ====================

@router.get("/meal-rankings")
def get_meal_rankings(request: Request, limit: int = Query(20, ge=1, le=100)):
    """
    Get meals ranked by feedback scores.
    Integrates feedback loop into meal display.
    """
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database unavailable")
    
    try:
        # Get vendor reviews with ratings
        reviews_res = sb.table("vendor_reviews") \
            .select("vendor_id, rating") \
            .execute()
        reviews = getattr(reviews_res, "data", []) or []
        
        # Aggregate vendor ratings
        vendor_ratings = {}
        for r in reviews:
            vid = r.get("vendor_id")
            rating = r.get("rating", 0)
            if vid:
                if vid not in vendor_ratings:
                    vendor_ratings[vid] = {"total": 0, "count": 0}
                vendor_ratings[vid]["total"] += rating
                vendor_ratings[vid]["count"] += 1
        
        # Calculate averages
        for vid in vendor_ratings:
            data = vendor_ratings[vid]
            vendor_ratings[vid]["average"] = round(data["total"] / data["count"], 2) if data["count"] > 0 else 0
        
        # Get menu items with vendor ratings
        menu_res = sb.table("menu_items") \
            .select("*") \
            .eq("is_available", True) \
            .limit(limit) \
            .execute()
        items = getattr(menu_res, "data", []) or []
        
        # Enrich with ratings
        ranked_items = []
        for item in items:
            vid = item.get("vendor_id")
            vr = vendor_ratings.get(vid, {})
            ranked_items.append({
                **item,
                "vendor_rating": vr.get("average", 0),
                "review_count": vr.get("count", 0),
                "ranking_score": (vr.get("average", 0) * 0.6) + (min(vr.get("count", 0) / 10, 1) * 40)
            })
        
        # Sort by ranking score
        ranked_items.sort(key=lambda x: x["ranking_score"], reverse=True)
        
        return {
            "success": True,
            "items": ranked_items,
            "ranking_factors": ["vendor_rating", "review_count", "availability"]
        }
    except Exception as e:
        print(f"[meal-rankings] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))
