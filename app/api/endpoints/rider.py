from fastapi import APIRouter, HTTPException, Request, Body
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import os
from jose import jwt, JWTError

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(tags=["rider"]) 

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"


def _client():
    return supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _ensure_rider_profile(user_id: str) -> Dict[str, Any]:
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("rider_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if rows:
            return rows[0]
    except Exception:
        pass
    row = {
        "user_id": user_id,
        "is_online": False,
        "total_deliveries": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        sb.table("rider_profiles").insert(row).execute()
    except Exception:
        pass
    try:
        res2 = sb.table("rider_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        rows2 = getattr(res2, "data", []) or []
        if rows2:
            return rows2[0]
    except Exception:
        pass
    return row


# ==================== Profile ====================

@router.get("/profile")
def get_profile(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    # user
    try:
        ures = sb.table("users").select("id, full_name, email").eq("id", user_id).limit(1).execute()
        urows = getattr(ures, "data", []) or []
        user = urows[0] if urows else {"id": user_id}
    except Exception:
        user = {"id": user_id}
    prof = _ensure_rider_profile(user_id)
    return {
        "user": user,
        "profile": prof,
    }


@router.put("/profile")
def update_profile(request: Request, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    allowed = {
        "phone", "address", "license_number", "vehicle_type", "vehicle_model", "vehicle_plate", "vehicle_color"
    }
    update = {k: v for k, v in (payload or {}).items() if k in allowed}
    if not update:
        return {"success": True}
    update["updated_at"] = _now_iso()
    _ensure_rider_profile(user_id)
    try:
        sb.table("rider_profiles").update(update).eq("user_id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {e}")
    return {"success": True}


@router.put("/availability")
def set_availability(request: Request, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    is_online = bool(payload.get("is_online", False))
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    _ensure_rider_profile(user_id)
    try:
        sb.table("rider_profiles").update({"is_online": is_online, "updated_at": _now_iso()}).eq("user_id", user_id).execute()
        return {"success": True, "is_online": is_online}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update availability: {e}")


# ==================== Orders for Rider ====================

ACTIVE_DB = ["READY_FOR_PICKUP", "ON_THE_WAY", "ARRIVING_SOON"]
HISTORY_DB = ["DELIVERED", "COMPLETED"]


def _map_active_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": r.get("id"),
            "orderId": r.get("order_code") or r.get("id"),
            "status": r.get("status"),
            "totalAmount": float(r.get("total", 0) or 0),
            "estimatedPickupTime": r.get("estimated_pickup_time"),
            "estimatedDeliveryTime": r.get("estimated_delivery_time"),
            "acceptedAt": r.get("accepted_at"),
            "restaurant_id": r.get("restaurant_id"),
            "user_id": r.get("user_id"),
            "items": r.get("items") or [],
        }
        for r in rows
    ]


@router.get("/orders/active")
def get_active_orders(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").select(
            "id, order_code, user_id, restaurant_id, items, total, status, accepted_at, estimated_pickup_time, estimated_delivery_time"
        ).eq("rider_id", user_id).in_("status", ACTIVE_DB).order("created_at", desc=True).execute()
        rows = getattr(res, "data", []) or []

        # Collect vendor and student ids
        vendor_ids = list({r.get("restaurant_id") for r in rows if r.get("restaurant_id")})
        student_ids = list({r.get("user_id") for r in rows if r.get("user_id")})

        vendors_map: Dict[str, Dict[str, Any]] = {}
        students_map: Dict[str, Dict[str, Any]] = {}

        if vendor_ids:
            vres = sb.table("users").select("id, full_name, organization").in_("id", vendor_ids).execute()
            vendors_map = {v["id"]: v for v in (getattr(vres, "data", []) or [])}
            # vendor profiles for pickup address
            try:
                vpres = sb.table("vendor_profiles").select("user_id, business_address, business_name").in_("user_id", vendor_ids).execute()
                for vp in (getattr(vpres, "data", []) or []):
                    if vp.get("user_id") in vendors_map:
                        vendors_map[vp["user_id"]]["business_address"] = vp.get("business_address")
                        vendors_map[vp["user_id"]]["business_name"] = vp.get("business_name")
            except Exception:
                pass

        if student_ids:
            sres = sb.table("users").select("id, full_name, email").in_("id", student_ids).execute()
            students_map = {s["id"]: s for s in (getattr(sres, "data", []) or [])}
            try:
                spres = sb.table("student_profiles").select("user_id, organization_name").in_("user_id", student_ids).execute()
                for sp in (getattr(spres, "data", []) or []):
                    uid = sp.get("user_id")
                    if uid in students_map:
                        students_map[uid]["organization_name"] = sp.get("organization_name")
            except Exception:
                pass

        mapped = []
        for r in _map_active_rows(rows):
            vend = vendors_map.get(r.get("restaurant_id"), {})
            stud = students_map.get(r.get("user_id"), {})
            mapped.append({
                **r,
                "restaurantName": vend.get("business_name") or vend.get("organization") or vend.get("full_name") or "Vendor",
                "pickupAddress": vend.get("business_address") or "Pickup counter",
                "deliveryAddress": (students_map.get(r.get("user_id"), {}) or {}).get("organization_name") or "Campus delivery",
                "customerName": stud.get("full_name") or "Customer",
                "customerPhone": "N/A",
            })
        return {"orders": mapped}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch active deliveries: {e}")


@router.get("/orders/history")
def get_history(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").select(
            "id, order_code, user_id, restaurant_id, total, status, delivered_at, distance_km, delivery_fee"
        ).eq("rider_id", user_id).in_("status", HISTORY_DB).order("delivered_at", desc=True).execute()
        rows = getattr(res, "data", []) or []
        vendor_ids = list({r.get("restaurant_id") for r in rows if r.get("restaurant_id")})
        student_ids = list({r.get("user_id") for r in rows if r.get("user_id")})

        vendors_map: Dict[str, Dict[str, Any]] = {}
        students_map: Dict[str, Dict[str, Any]] = {}

        if vendor_ids:
            vres = sb.table("users").select("id, full_name, organization").in_("id", vendor_ids).execute()
            vendors_map = {v["id"]: v for v in (getattr(vres, "data", []) or [])}
        if student_ids:
            sres = sb.table("users").select("id, full_name").in_("id", student_ids).execute()
            students_map = {s["id"]: s for s in (getattr(sres, "data", []) or [])}

        mapped = []
        for r in rows:
            mapped.append({
                "id": r.get("id"),
                "orderId": r.get("order_code") or r.get("id"),
                "customerName": (students_map.get(r.get("user_id"), {}) or {}).get("full_name") or "Customer",
                "restaurantName": (vendors_map.get(r.get("restaurant_id"), {}) or {}).get("organization") or "Vendor",
                "completedAt": r.get("delivered_at"),
                "amount": float(r.get("total", 0) or 0),
                "fee": float(r.get("delivery_fee", 0) or 0),
                "distance": f"{float(r.get('distance_km') or 0):.1f} km",
                "duration": "",
                "rating": None,
              })
        return {"history": mapped}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {e}")


@router.put("/orders/{order_id}/status")
def update_status(request: Request, order_id: str, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw_status = (payload or {}).get("status")
    parsed_status: Optional[str] = None
    if isinstance(raw_status, str):
        parsed_status = raw_status
    elif isinstance(raw_status, dict):
        parsed_status = raw_status.get("value") or raw_status.get("status") or raw_status.get("label")
    elif raw_status is None:
        parsed_status = None
    else:
        parsed_status = str(raw_status)

    if not parsed_status:
        raise HTTPException(status_code=400, detail="status is required")

    new_status = parsed_status.strip().upper()
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    # Map UI statuses to DB statuses
    ui_to_db = {
        "PENDING_PICKUP": "READY_FOR_PICKUP",
        "PICKED_UP": "ON_THE_WAY",
        "DELIVERING": "ON_THE_WAY",
        "COMPLETED": "DELIVERED",
    }
    allowed_db_statuses = {"PENDING", "CONFIRMED", "PREPARING", "READY_FOR_PICKUP", "ON_THE_WAY", "ARRIVING_SOON", "DELIVERED", "COMPLETED", "CANCELLED"}
    db_status = ui_to_db.get(new_status, new_status)
    if db_status not in allowed_db_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{parsed_status}'. Allowed: {sorted(list(allowed_db_statuses))}")
    try:
        # Ensure order belongs to rider before update
        chk = sb.table("orders").select("id, rider_id, status").eq("id", order_id).limit(1).execute()
        row = (getattr(chk, "data", []) or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if str(row.get("rider_id") or "") != str(user_id):
            raise HTTPException(status_code=403, detail="Order not assigned to this rider")

        upd = {"status": db_status, "updated_at": _now_iso()}
        if db_status in ("DELIVERED", "COMPLETED"):
            upd["delivered_at"] = datetime.now(timezone.utc).isoformat()
        res = sb.table("orders").update(upd).eq("id", order_id).eq("rider_id", user_id).execute()
        data = getattr(res, "data", []) or []
        if not data:
            raise HTTPException(status_code=404, detail="Order not found or not assigned to rider")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        # Surface backend/DB error so client can see what's wrong
        raise HTTPException(status_code=500, detail=f"Failed to update status: {e}")


# ==================== Earnings ====================

@router.get("/earnings/summary")
def earnings_summary(request: Request, days: int = 30):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        # last N days delivered/completed
        from_ts = (datetime.now(timezone.utc)).isoformat()
        # Supabase can't run SQL functions; compute with filtering
        res = sb.table("orders").select("delivery_fee, distance_km, delivered_at, status").eq("rider_id", user_id).in_("status", ["DELIVERED", "COMPLETED"]).execute()
        rows = getattr(res, "data", []) or []
        total_fee = sum(float(r.get("delivery_fee", 0) or 0) for r in rows)
        total_distance = sum(float(r.get("distance_km", 0) or 0) for r in rows)
        deliveries = len(rows)
        avg_fee = (total_fee / deliveries) if deliveries else 0
        return {
            "total_deliveries": deliveries,
            "total_fee": float(total_fee),
            "avg_fee": float(avg_fee),
            "total_distance": float(total_distance),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute earnings: {e}")


# ==================== Available orders (unassigned, ready for pickup) ====================

@router.get("/orders/available")
def get_available_orders(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").select(
            "id, order_code, user_id, restaurant_id, items, total, status, estimated_pickup_time, estimated_delivery_time, created_at"
        ).is_("rider_id", None).eq("status", "READY_FOR_PICKUP").order("created_at", desc=True).execute()
        rows = getattr(res, "data", []) or []
        vendor_ids = list({r.get("restaurant_id") for r in rows if r.get("restaurant_id")})
        student_ids = list({r.get("user_id") for r in rows if r.get("user_id")})

        vendors_map: Dict[str, Dict[str, Any]] = {}
        students_map: Dict[str, Dict[str, Any]] = {}

        if vendor_ids:
            vres = sb.table("users").select("id, full_name, organization").in_("id", vendor_ids).execute()
            vendors_map = {v["id"]: v for v in (getattr(vres, "data", []) or [])}
            try:
                vpres = sb.table("vendor_profiles").select("user_id, business_address, business_name").in_("user_id", vendor_ids).execute()
                for vp in (getattr(vpres, "data", []) or []):
                    if vp.get("user_id") in vendors_map:
                        vendors_map[vp["user_id"]]["business_address"] = vp.get("business_address")
                        vendors_map[vp["user_id"]]["business_name"] = vp.get("business_name")
            except Exception:
                pass

        if student_ids:
            sres = sb.table("users").select("id, full_name, email").in_("id", student_ids).execute()
            students_map = {s["id"]: s for s in (getattr(sres, "data", []) or [])}
            try:
                spres = sb.table("student_profiles").select("user_id, organization_name").in_("user_id", student_ids).execute()
                for sp in (getattr(spres, "data", []) or []):
                    uid = sp.get("user_id")
                    if uid in students_map:
                        students_map[uid]["organization_name"] = sp.get("organization_name")
            except Exception:
                pass

        mapped = []
        for r in rows:
            vend = vendors_map.get(r.get("restaurant_id"), {})
            stud = students_map.get(r.get("user_id"), {})
            mapped.append({
                "id": r.get("id"),
                "orderId": r.get("order_code") or r.get("id"),
                "status": r.get("status"),
                "totalAmount": float(r.get("total", 0) or 0),
                "estimatedPickupTime": r.get("estimated_pickup_time"),
                "estimatedDeliveryTime": r.get("estimated_delivery_time"),
                "restaurantName": vend.get("business_name") or vend.get("organization") or vend.get("full_name") or "Vendor",
                "pickupAddress": vend.get("business_address") or "Pickup counter",
                "deliveryAddress": stud.get("organization_name") or "Campus delivery",
                "customerName": stud.get("full_name") or "Customer",
                "customerPhone": "N/A",
                "items": r.get("items") or [],
            })
        return {"orders": mapped}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch available deliveries: {e}")


@router.put("/orders/{order_id}/accept")
def accept_order(request: Request, order_id: str):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        # Ensure order is unassigned and ready
        chk = sb.table("orders").select("id, status, rider_id").eq("id", order_id).limit(1).execute()
        row = (getattr(chk, "data", []) or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if row.get("rider_id") and str(row.get("rider_id")) != str(user_id):
            raise HTTPException(status_code=409, detail="Order already assigned")

        upd = {
            "rider_id": user_id,
            "accepted_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if row.get("status") not in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
            upd["status"] = "READY_FOR_PICKUP"

        res = sb.table("orders").update(upd).eq("id", order_id).execute()
        data = getattr(res, "data", []) or []
        if not data:
            raise HTTPException(status_code=500, detail="Failed to accept order")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to accept order: {e}")
