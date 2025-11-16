from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, Optional
from datetime import date
import os
from jose import jwt, JWTError

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/vouchers", tags=["vouchers"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"

def _client():
    return supabase

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
    return None

@router.get("")
def list_my_vouchers(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = (
            sb.table("vouchers")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        out = []
        for v in rows:
            out.append({
                "id": v.get("id"),
                "code": v.get("code"),
                "title": v.get("title"),
                "description": v.get("description"),
                "expiry": v.get("expiry_date"),
                "used": bool(v.get("used", False)),
            })
        return {"success": True, "vouchers": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list vouchers: {e}")

@router.post("/{voucher_id}/use")
def use_voucher(voucher_id: str, request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("vouchers").select("*").eq("id", voucher_id).eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if not rows:
            raise HTTPException(status_code=404, detail="Voucher not found")
        v = rows[0]
        if bool(v.get("used", False)):
            return {"success": True, "voucher": {
                "id": v.get("id"),
                "code": v.get("code"),
                "title": v.get("title"),
                "description": v.get("description"),
                "expiry": v.get("expiry_date"),
                "used": True,
            }}
        # Check expiry
        try:
            if v.get("expiry_date") and date.fromisoformat(str(v.get("expiry_date"))) < date.today():
                raise HTTPException(status_code=400, detail="Voucher expired")
        except ValueError:
            pass
        sb.table("vouchers").update({"used": True}).eq("id", voucher_id).execute()
        return {"success": True, "voucher": {
            "id": v.get("id"),
            "code": v.get("code"),
            "title": v.get("title"),
            "description": v.get("description"),
            "expiry": v.get("expiry_date"),
            "used": True,
        }}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to use voucher: {e}")
