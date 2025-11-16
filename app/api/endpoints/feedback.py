from fastapi import APIRouter, HTTPException, Request, Body
from typing import Dict, Any, Optional
from datetime import datetime
import os
from jose import jwt, JWTError

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/feedback", tags=["feedback"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"


def _client():
    return supabase


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _get_user_id(req: Request, payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    auth = req.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.replace("Bearer ", "").strip()
        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            sub = data.get("sub")
            if sub:
                return str(sub)
        except JWTError:
            pass
    if req.headers.get("x-user-id"):
        return req.headers.get("x-user-id")
    if payload and payload.get("userId"):
        return str(payload.get("userId"))
    return None


@router.post("")
def submit_feedback(request: Request, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    rating = payload.get("rating")
    category = (payload.get("category") or "general").strip().lower()
    message = (payload.get("message") or "").strip()

    # Validate inputs
    try:
        rating_int = int(rating)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid rating")
    if rating_int < 1 or rating_int > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    allowed_categories = {"general", "food", "service", "app", "suggestion", "complaint"}
    if category not in allowed_categories:
        category = "general"

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if len(message) > 500:
        message = message[:500]

    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    row = {
        "user_id": user_id,
        "rating": rating_int,
        "category": category,
        "message": message,
        "created_at": _now_iso(),
    }

    try:
        ins = sb.table("feedback").insert(row).execute()
        created = (getattr(ins, "data", []) or [row])[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {e}")

    return {
        "success": True,
        "feedback": {
            "id": created.get("id"),
            "rating": created.get("rating"),
            "category": created.get("category"),
            "message": created.get("message"),
            "date": (created.get("created_at") or _now_iso())[:10],
        },
    }


@router.get("/mine")
def my_feedback(request: Request, limit: int = 50):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    try:
        limit_val = max(1, min(int(limit), 200))
    except Exception:
        limit_val = 50

    try:
        res = (
            sb.table("feedback")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit_val)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        items = [
            {
                "id": r.get("id"),
                "rating": r.get("rating"),
                "category": r.get("category"),
                "message": r.get("message"),
                "date": (r.get("created_at") or _now_iso())[:10],
            }
            for r in rows
        ]
        return {"success": True, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedback: {e}")
