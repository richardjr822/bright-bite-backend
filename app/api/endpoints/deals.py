from fastapi import APIRouter, HTTPException, Request, Query
from datetime import datetime, timezone, timedelta

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/deals", tags=["deals"])

def _client():
    return supabase

def _validate_offset(offset: int | None) -> int:
    if offset is None:
        return 0
    return offset if -720 <= offset <= 840 else 0

def _offset_from(request: Request, explicit: int | None) -> int:
    if explicit is not None:
        return _validate_offset(explicit)
    hdr = request.headers.get("x-tz-offset")
    try:
        if hdr:
            return _validate_offset(int(hdr))
    except Exception:
        pass
    return 0

def _shift_iso(ts: str | None, offset: int) -> str | None:
    if not ts:
        return None
    try:
        cleaned = ts.replace('Z', '+00:00')
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(minutes=offset)).isoformat()
    except Exception:
        return ts

@router.get("")
def list_deals(request: Request, tz_offset_minutes: int | None = Query(default=None)):
    offset = _offset_from(request, tz_offset_minutes)
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("deals").select("*").order("created_at", desc=True).execute()
        rows = getattr(res, "data", []) or []
        out = []
        for d in rows:
            out.append({
                "id": d.get("id"),
                "vendor_id": d.get("vendor_id"),
                "title": d.get("title"),
                "description": d.get("description"),
                "discount": d.get("discount"),
                "minSpend": float(d.get("min_spend", 0) or 0),
                "expiry": d.get("expiry"),
                "created_at_local": _shift_iso(d.get("created_at"), offset),
                "updated_at_local": _shift_iso(d.get("updated_at"), offset),
                "expiry_local": _shift_iso(d.get("expiry"), offset)
            })
        return {"success": True, "timezoneOffsetMinutes": offset, "deals": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list deals: {e}")
