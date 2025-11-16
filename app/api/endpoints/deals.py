from fastapi import APIRouter, HTTPException

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/deals", tags=["deals"])

def _client():
    return supabase

@router.get("")
def list_deals():
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
            })
        return {"success": True, "deals": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list deals: {e}")
