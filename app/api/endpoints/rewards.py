from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, date
import os
import random
import string
from jose import jwt, JWTError

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/rewards", tags=["rewards"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"

def _client():
    return supabase

def _now_iso():
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

def _ensure_student_profile(user_id: str) -> Dict[str, Any]:
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("student_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if rows:
            return rows[0]
    except Exception:
        pass
    row = {"user_id": user_id, "organization_name": "", "wallet_balance": 0, "points": 0}
    try:
        sb.table("student_profiles").insert(row).execute()
    except Exception:
        pass
    try:
        res2 = sb.table("student_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        rows2 = getattr(res2, "data", []) or []
        if rows2:
            return rows2[0]
    except Exception:
        pass
    return row

@router.get("")
def list_rewards():
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("rewards").select("*").eq("available", True).order("created_at", desc=True).execute()
        rows = getattr(res, "data", []) or []
        out = []
        for r in rows:
            out.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "description": r.get("description"),
                "points_required": r.get("points_required", 0),
                "type": r.get("type") or "discount",
                "expiry_days": r.get("expiry_days", 30),
                "available": bool(r.get("available", True)),
            })
        return {"success": True, "rewards": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list rewards: {e}")

@router.get("/points")
def get_points(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    profile = _ensure_student_profile(user_id)
    return {"success": True, "points": int(profile.get("points", 0) or 0)}

def _generate_code(n: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(random.choice(alphabet) for _ in range(n))

@router.post("/redeem")
def redeem_reward(request: Request, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    reward_id = payload.get("reward_id") or payload.get("rewardId")
    if not reward_id:
        raise HTTPException(status_code=400, detail="reward_id is required")

    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    # Fetch reward
    try:
        rres = sb.table("rewards").select("*").eq("id", reward_id).eq("available", True).limit(1).execute()
        rrows = getattr(rres, "data", []) or []
        if not rrows:
            raise HTTPException(status_code=404, detail="Reward not found or unavailable")
        reward = rrows[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch reward: {e}")

    # Ensure profile and enough points
    profile = _ensure_student_profile(user_id)
    current_points = int(profile.get("points", 0) or 0)
    cost = int(reward.get("points_required", 0) or 0)
    if current_points < cost:
        raise HTTPException(status_code=400, detail="Not enough points")

    # Idempotency key (optional)
    idem_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or payload.get("idempotency_key")
    if idem_key:
        try:
            ex = sb.table("vouchers").select("*").eq("id", idem_key).eq("user_id", user_id).limit(1).execute()
            ex_rows = getattr(ex, "data", []) or []
            if ex_rows:
                # Return without deducting again
                # Refresh profile points
                pref = _ensure_student_profile(user_id)
                return {"success": True, "points": int(pref.get("points", 0) or 0), "voucher": ex_rows[0]}
        except Exception:
            pass

    # Prepare voucher
    expiry_days = int(reward.get("expiry_days", 30) or 30)
    expiry_dt = date.today() + timedelta(days=expiry_days)
    code = _generate_code(10)
    voucher_row = {
        "user_id": user_id,
        "reward_id": reward.get("id"),
        "code": code,
        "title": reward.get("title"),
        "description": reward.get("description"),
        "expiry_date": expiry_dt.isoformat(),
        "used": False,
    }

    # Insert voucher first; if success, deduct points; on duplicate/idempotency conflict, fetch existing without deduct
    try:
        if idem_key:
            voucher_row["id"] = idem_key
        ins = sb.table("vouchers").insert(voucher_row).execute()
        voucher = (getattr(ins, "data", []) or [voucher_row])[0]
        # Deduct points now
        new_points = max(0, current_points - cost)
        try:
            sb.table("student_profiles").update({"points": new_points, "updated_at": _now_iso()}).eq("user_id", user_id).execute()
        except Exception:
            pass
    except Exception:
        # Duplicate/idempotent? Try fetch existing
        try:
            if idem_key:
                ex = sb.table("vouchers").select("*").eq("id", idem_key).limit(1).execute()
                ex_rows = getattr(ex, "data", []) or []
                if ex_rows:
                    voucher = ex_rows[0]
                    new_points = current_points  # don't deduct again
                else:
                    # Fallback: check recent voucher for this reward
                    recent = sb.table("vouchers").select("*").eq("user_id", user_id).eq("reward_id", reward.get("id")).order("created_at", desc=True).limit(1).execute()
                    rrows = getattr(recent, "data", []) or []
                    voucher = rrows[0] if rrows else voucher_row
                    new_points = current_points
            else:
                # Fallback recent check
                recent = sb.table("vouchers").select("*").eq("user_id", user_id).eq("reward_id", reward.get("id")).order("created_at", desc=True).limit(1).execute()
                rrows = getattr(recent, "data", []) or []
                voucher = rrows[0] if rrows else voucher_row
                new_points = current_points
        except Exception:
            voucher = voucher_row
            new_points = current_points

    return {"success": True, "points": new_points, "voucher": {
        "id": voucher.get("id"),
        "code": voucher.get("code"),
        "title": voucher.get("title"),
        "description": voucher.get("description"),
        "expiry": voucher.get("expiry_date"),
        "used": bool(voucher.get("used", False)),
    }}
