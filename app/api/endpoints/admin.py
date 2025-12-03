from fastapi import APIRouter, HTTPException, Depends, status
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any, Dict
from pydantic import BaseModel
from app.db.database import supabase
from app.core.security import get_current_user, verify_password, get_password_hash

ACTIVE_ORDER_STATUSES: List[str] = [
    "PENDING_CONFIRMATION",
    "CONFIRMED",
    "PAYMENT_PROCESSING",
    "PREPARING",
    "READY_FOR_PICKUP",
    "ON_THE_WAY",
    "ARRIVING_SOON"
]

FINAL_REVENUE_STATUSES: List[str] = ["DELIVERED", "COMPLETED"]

class RejectVendorBody(BaseModel):
    reason: str | None = None

class DealCreate(BaseModel):
    vendor_id: str
    title: str
    description: Optional[str] = None
    discount: Optional[str] = None
    min_spend: Optional[float] = None
    minSpend: Optional[float] = None
    expiry: Optional[str] = None  # ISO datetime string

class DealUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    discount: Optional[str] = None
    min_spend: Optional[float] = None
    minSpend: Optional[float] = None
    expiry: Optional[str] = None
    is_active: Optional[bool] = None

class SettingUpdate(BaseModel):
    value: Dict[str, Any]
    description: Optional[str] = None

class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str

router = APIRouter()

# ----- Timezone helper (client machine offset) -----
def _validate_offset(offset: Optional[int]) -> int:
    if offset is None:
        return 0
    if offset < -720 or offset > 840:  # outside plausible UTC-12..UTC+14 range
        return 0
    return offset

def _shift_iso(ts: Optional[str], offset_minutes: int) -> Optional[str]:
    if not ts:
        return None
    try:
        cleaned = ts.replace('Z', '+00:00')
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_local = dt + timedelta(minutes=offset_minutes)
        return dt_local.isoformat()
    except Exception:
        return ts  # fallback original

@router.get("/stats")
async def get_admin_stats(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """Aggregate admin dashboard statistics based on current schema."""
    try:
        admin_id = current_user.get("sub")
        # Verify admin role
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")

        # Students count
        students_resp = supabase.table("users").select("id", count="exact").eq("role", "student").execute()
        total_students = students_resp.count or 0

        # Approved vendors count
        approved_vendors_resp = supabase.table("vendor_profiles").select("id", count="exact").eq("approval_status", "approved").execute()
        total_vendors = approved_vendors_resp.count or 0

        # Pending vendors count
        pending_vendors_resp = supabase.table("vendor_profiles").select("id", count="exact").eq("approval_status", "pending").execute()
        pending_vendors = pending_vendors_resp.count or 0

        # Active orders count
        active_orders_resp = supabase.table("orders").select("id", count="exact").in_("status", ACTIVE_ORDER_STATUSES).execute()
        active_orders = active_orders_resp.count or 0

        # Revenue (sum of orders.total for final statuses)
        revenue_orders_resp = supabase.table("orders").select("total,status").in_("status", FINAL_REVENUE_STATUSES).execute()
        total_revenue = 0
        for o in revenue_orders_resp.data or []:
            try:
                total_revenue += float(o.get("total", 0) or 0)
            except (TypeError, ValueError):
                continue

        # Total meals (menu items count)
        menu_items_resp = supabase.table("menu_items").select("id", count="exact").execute()
        total_meals = menu_items_resp.count or 0

        offset = _validate_offset(tz_offset_minutes)
        generated_at = datetime.now(timezone.utc).isoformat()
        return {
            "totalStudents": total_students,
            "totalVendors": total_vendors,
            "activeOrders": active_orders,
            "totalRevenue": total_revenue,
            "pendingVendors": pending_vendors,
            "totalMeals": total_meals,
            "timezoneOffsetMinutes": offset,
            "generatedAt": generated_at,
            "generatedAtLocal": _shift_iso(generated_at, offset)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users")
async def get_all_users():
    """Get all users for admin management"""
    try:
        response = supabase.table("users").select("*").execute()
        return {"users": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/beneficiaries")
async def get_all_beneficiaries():
    """Get all beneficiaries"""
    try:
        response = supabase.table("beneficiaries").select("*").execute()
        return {"beneficiaries": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/programs")
async def get_all_programs():
    """Get all programs"""
    try:
        response = supabase.table("programs").select("*").execute()
        return {"programs": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pending-vendors")
async def get_pending_vendors(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """Return all vendor applications still pending approval.
    Combines vendor_profiles with related user record where role = 'pending_vendor' and approval_status='pending'."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        vp_resp = supabase.table("vendor_profiles").select("*").eq("approval_status", "pending").execute()
        pending = []
        offset = _validate_offset(tz_offset_minutes)
        for vp in vp_resp.data:
            user_id = vp.get("user_id")
            user_resp = supabase.table("users").select("id, full_name, email, role, organization, created_at").eq("id", user_id).eq("role", "pending_vendor").limit(1).execute()
            if user_resp.data:
                user_row = user_resp.data[0]
                combined = {
                    "vendor_profile": vp,
                    "vendor_profile_created_at_local": _shift_iso(vp.get("created_at"), offset),
                    "vendor_profile_updated_at_local": _shift_iso(vp.get("updated_at"), offset),
                    "user": user_row,
                    "user_created_at_local": _shift_iso(user_row.get("created_at"), offset)
                }
                pending.append(combined)
        return {"pending_vendors": pending, "timezoneOffsetMinutes": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/approve-vendor/{vendor_id}")
async def approve_vendor(vendor_id: str, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """Approve a vendor: set user role to 'vendor' and vendor_profile.approval_status='approved'."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        # Ensure vendor profile exists and is pending
        vp = supabase.table("vendor_profiles").select("id").eq("user_id", vendor_id).eq("approval_status", "pending").limit(1).execute()
        if not vp.data:
            raise HTTPException(status_code=404, detail="Pending vendor profile not found")
        # Update user role & status
        user_update = supabase.table("users").update({
            "role": "vendor",
            "status": "active"
        }).eq("id", vendor_id).eq("role", "pending_vendor").execute()
        if not user_update.data:
            raise HTTPException(status_code=404, detail="Pending vendor user not found")
        # Update vendor profile approval
        approved_at = datetime.now(timezone.utc).isoformat()
        supabase.table("vendor_profiles").update({
            "approval_status": "approved",
            "approved_at": approved_at,
            "approved_by": admin_id
        }).eq("user_id", vendor_id).execute()
        # Fetch updated profile for local timestamp conversion
        vp_updated = supabase.table("vendor_profiles").select("approved_at, updated_at, created_at").eq("user_id", vendor_id).limit(1).execute()
        offset = _validate_offset(tz_offset_minutes)
        vp_row = vp_updated.data[0] if vp_updated.data else {}
        return {
            "message": "Vendor approved",
            "user": user_update.data[0],
            "approved_at": approved_at,
            "approved_at_local": _shift_iso(approved_at, offset),
            "vendor_profile": vp_row,
            "vendor_profile_created_at_local": _shift_iso(vp_row.get("created_at"), offset),
            "vendor_profile_updated_at_local": _shift_iso(vp_row.get("updated_at"), offset),
            "timezoneOffsetMinutes": offset
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reject-vendor/{vendor_id}")
async def reject_vendor(vendor_id: str, body: RejectVendorBody, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """Reject a vendor application: mark vendor_profile rejected; deactivate user for safety."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        # Verify pending vendor
        vp = supabase.table("vendor_profiles").select("id").eq("user_id", vendor_id).eq("approval_status", "pending").limit(1).execute()
        if not vp.data:
            raise HTTPException(status_code=404, detail="Pending vendor profile not found")
        # Update vendor profile
        updated_at = datetime.now(timezone.utc).isoformat()
        supabase.table("vendor_profiles").update({
            "approval_status": "rejected",
            "updated_at": updated_at,
            "rejection_reason": body.reason
        }).eq("user_id", vendor_id).execute()
        # Deactivate user (keep record for audit)
        supabase.table("users").update({
            "status": "inactive"
        }).eq("id", vendor_id).eq("role", "pending_vendor").execute()
        vp_updated = supabase.table("vendor_profiles").select("updated_at, created_at").eq("user_id", vendor_id).limit(1).execute()
        offset = _validate_offset(tz_offset_minutes)
        vp_row = vp_updated.data[0] if vp_updated.data else {}
        return {
            "message": "Vendor application rejected",
            "reason": body.reason,
            "updated_at": updated_at,
            "updated_at_local": _shift_iso(updated_at, offset),
            "vendor_profile": vp_row,
            "vendor_profile_created_at_local": _shift_iso(vp_row.get("created_at"), offset),
            "vendor_profile_updated_at_local": _shift_iso(vp_row.get("updated_at"), offset),
            "timezoneOffsetMinutes": offset
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Vendors & Students =====================
@router.get("/vendors")
async def list_vendors(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """List approved vendors with profile + user info."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        profiles_resp = supabase.table("vendor_profiles").select("*").eq("approval_status", "approved").execute()
        vendors: List[Dict[str, Any]] = []
        offset = _validate_offset(tz_offset_minutes)
        for vp in profiles_resp.data or []:
            uid = vp.get("user_id")
            u_resp = supabase.table("users").select("id, full_name, email, organization, status, created_at, updated_at").eq("id", uid).limit(1).execute()
            if not u_resp.data:
                continue
            user = u_resp.data[0]
            # Count menu items for this vendor
            mi_count = supabase.table("menu_items").select("id", count="exact").eq("vendor_id", uid).execute()
            menu_items_count = mi_count.count or 0
            # Orders count for this vendor
            ord_count = supabase.table("orders").select("id", count="exact").eq("restaurant_id", uid).execute()
            orders_count = ord_count.count or 0
            vendors.append({
                "id": uid,
                "business_name": vp.get("business_name"),
                "full_name": user.get("full_name"),
                "email": user.get("email"),
                "status": user.get("status", "active"),
                "rating": float(vp.get("rating") or 0),
                "menu_items_count": menu_items_count,
                "orders_count": orders_count,
                "created_at": vp.get("created_at"),
                "updated_at": vp.get("updated_at"),
                "created_at_local": _shift_iso(vp.get("created_at"), offset),
                "updated_at_local": _shift_iso(vp.get("updated_at"), offset)
            })
        return {"vendors": vendors, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/students")
async def list_students(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """List student users with optional student profile."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        users_resp = supabase.table("users").select("id, full_name, email, organization, status, created_at").eq("role", "student").order("created_at", desc=True).execute()
        out: List[Dict[str, Any]] = []
        offset = _validate_offset(tz_offset_minutes)
        for u in users_resp.data or []:
            sid = u.get("id")
            sp_resp = supabase.table("student_profiles").select("wallet_balance, points").eq("user_id", sid).limit(1).execute()
            profile = sp_resp.data[0] if sp_resp.data else {}
            out.append({
                "id": sid,
                "full_name": u.get("full_name"),
                "email": u.get("email"),
                "organization": u.get("organization"),
                "status": u.get("status", "active"),
                "wallet_balance": float(profile.get("wallet_balance") or 0),
                "points": int(profile.get("points") or 0),
                "created_at": u.get("created_at"),
                "created_at_local": _shift_iso(u.get("created_at"), offset)
            })
        return {"students": out, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Delivery Staff =====================
@router.get("/delivery-staff")
async def list_delivery_staff(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    """List delivery staff with related user & vendor info; apply client timezone offset to timestamps."""
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        offset = _validate_offset(tz_offset_minutes)
        ds_resp = supabase.table("delivery_staff").select("*").order("created_at", desc=True).execute()
        staff_rows = ds_resp.data or []
        user_ids = [r.get("user_id") for r in staff_rows if r.get("user_id")]
        vendor_ids = [r.get("vendor_id") for r in staff_rows if r.get("vendor_id")]
        users_map: Dict[str, Any] = {}
        vendors_map: Dict[str, Any] = {}
        if user_ids:
            u_resp = supabase.table("users").select("id, full_name, email, created_at").in_("id", user_ids).execute()
            for u in u_resp.data or []:
                users_map[u.get("id")] = u
        if vendor_ids:
            v_resp = supabase.table("vendor_profiles").select("user_id, business_name").in_("user_id", vendor_ids).execute()
            for v in v_resp.data or []:
                vendors_map[v.get("user_id")] = v
        out: List[Dict[str, Any]] = []
        for r in staff_rows:
            uid = r.get("user_id")
            vid = r.get("vendor_id")
            out.append({
                "id": r.get("id"),
                "staff_id": r.get("staff_id"),
                "user_id": uid,
                "vendor_id": vid,
                "phone": r.get("phone"),
                "profile_photo_url": r.get("profile_photo_url"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "created_at_local": _shift_iso(r.get("created_at"), offset),
                "updated_at_local": _shift_iso(r.get("updated_at"), offset),
                "user": users_map.get(uid),
                "user_created_at_local": _shift_iso(users_map.get(uid, {}).get("created_at"), offset) if users_map.get(uid) else None,
                "vendor": vendors_map.get(vid)
            })
        return {"delivery_staff": out, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Deals Management =====================
@router.get("/deals")
async def admin_list_deals(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        res = supabase.table("deals").select("*").order("created_at", desc=True).execute()
        rows = res.data or []
        # Map vendor business_name
        out: List[Dict[str, Any]] = []
        offset = _validate_offset(tz_offset_minutes)
        for d in rows:
            vid = d.get("vendor_id")
            vp_resp = supabase.table("vendor_profiles").select("business_name").eq("user_id", vid).limit(1).execute()
            out.append({
                "id": d.get("id"),
                "vendor_id": vid,
                "vendor_business_name": (vp_resp.data[0].get("business_name") if vp_resp.data else None),
                "title": d.get("title"),
                "description": d.get("description"),
                "discount": d.get("discount"),
                "min_spend": float(d.get("min_spend", 0) or 0),
                "expiry": d.get("expiry"),
                "is_active": d.get("is_active", True),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
                "created_at_local": _shift_iso(d.get("created_at"), offset),
                "updated_at_local": _shift_iso(d.get("updated_at"), offset),
                "expiry_local": _shift_iso(d.get("expiry"), offset)
            })
        return {"deals": out, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/deals")
async def admin_create_deal(body: DealCreate, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        # Ensure vendor exists & approved
        vp_resp = supabase.table("vendor_profiles").select("id").eq("user_id", body.vendor_id).eq("approval_status", "approved").limit(1).execute()
        if not vp_resp.data:
            raise HTTPException(status_code=404, detail="Approved vendor not found")
        min_spend_value = body.min_spend if body.min_spend is not None else (body.minSpend or 0)
        created_at = datetime.now(timezone.utc).isoformat()
        row = {
            "vendor_id": body.vendor_id,
            "title": body.title,
            "description": body.description,
            "discount": body.discount,
            "min_spend": min_spend_value,
            "expiry": body.expiry,
            "created_at": created_at,
            "updated_at": created_at,
            "is_active": True
        }
        ins = supabase.table("deals").insert(row).execute()
        if not ins.data:
            raise HTTPException(status_code=500, detail="Failed to create deal")
        offset = _validate_offset(tz_offset_minutes)
        deal_row = ins.data[0]
        deal_row["created_at_local"] = _shift_iso(deal_row.get("created_at"), offset)
        deal_row["updated_at_local"] = _shift_iso(deal_row.get("updated_at"), offset)
        deal_row["expiry_local"] = _shift_iso(deal_row.get("expiry"), offset)
        return {"deal": deal_row, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/deals/{deal_id}")
async def admin_update_deal(deal_id: str, body: DealUpdate, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        update_payload = {"updated_at": datetime.now(timezone.utc).isoformat()}
        data = body.dict(exclude_unset=True)
        if "min_spend" in data and data["min_spend"] is not None:
            update_payload["min_spend"] = data["min_spend"]
        if "minSpend" in data and data["minSpend"] is not None:
            update_payload["min_spend"] = data["minSpend"]
        for field in ("title","description","discount","expiry","is_active"):
            if field in data and data[field] is not None:
                update_payload[field] = data[field]
        upd = supabase.table("deals").update(update_payload).eq("id", deal_id).execute()
        if not upd.data:
            raise HTTPException(status_code=404, detail="Deal not found")
        offset = _validate_offset(tz_offset_minutes)
        deal_row = upd.data[0]
        deal_row["updated_at_local"] = _shift_iso(deal_row.get("updated_at"), offset)
        deal_row["created_at_local"] = _shift_iso(deal_row.get("created_at"), offset)
        deal_row["expiry_local"] = _shift_iso(deal_row.get("expiry"), offset)
        return {"deal": deal_row, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/deals/{deal_id}")
async def admin_delete_deal(deal_id: str, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        # Soft delete -> set is_active false
        updated_at = datetime.now(timezone.utc).isoformat()
        upd = supabase.table("deals").update({"is_active": False, "updated_at": updated_at}).eq("id", deal_id).execute()
        if not upd.data:
            raise HTTPException(status_code=404, detail="Deal not found")
        offset = _validate_offset(tz_offset_minutes)
        return {"message": "Deal deactivated", "updated_at": updated_at, "updated_at_local": _shift_iso(updated_at, offset), "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Support PUT for updating deals to match frontend
@router.put("/deals/{deal_id}")
async def admin_put_deal(deal_id: str, body: DealUpdate, current_user = Depends(get_current_user)):
    return await admin_update_deal(deal_id, body, current_user)

# ===================== Orders & Transactions =====================
@router.get("/orders")
async def admin_list_orders(status_filter: Optional[str] = None, limit: int = 100, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        q = supabase.table("orders").select("id, order_code, user_id, restaurant_id, status, total, items, payment_method, created_at, updated_at, assigned_staff_id, proof_of_delivery_url")
        if status_filter:
            q = q.eq("status", status_filter)
        res = q.order("created_at", desc=True).limit(limit).execute()
        orders = res.data or []
        
        # Fetch related users in batch
        user_ids = list({o.get("user_id") for o in orders if o.get("user_id")})
        users_map: Dict[str, Dict[str, Any]] = {}
        if user_ids:
            users_resp = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
            for u in users_resp.data or []:
                users_map[u.get("id")] = u
        
        # Fetch vendor names
        vendor_ids = list({o.get("restaurant_id") for o in orders if o.get("restaurant_id")})
        vendors_map: Dict[str, str] = {}
        if vendor_ids:
            vp_resp = supabase.table("vendor_profiles").select("user_id, business_name").in_("user_id", vendor_ids).execute()
            for v in vp_resp.data or []:
                vendors_map[v.get("user_id")] = v.get("business_name", "Unknown Vendor")
        
        # Fetch delivery staff info
        staff_ids = list({o.get("assigned_staff_id") for o in orders if o.get("assigned_staff_id")})
        staff_map: Dict[str, Dict[str, Any]] = {}
        if staff_ids:
            ds_resp = supabase.table("delivery_staff").select("id, user_id, phone, profile_photo_url").in_("id", staff_ids).execute()
            ds_list = ds_resp.data or []
            staff_user_ids = [row.get("user_id") for row in ds_list if row.get("user_id")]
            user_map2: Dict[str, Dict] = {}
            if staff_user_ids:
                users_resp2 = supabase.table("users").select("id, full_name, email").in_("id", staff_user_ids).execute()
                user_map2 = {u["id"]: u for u in (users_resp2.data or [])}
            for row in ds_list:
                user_info = user_map2.get(row.get("user_id"), {})
                staff_map[row.get("id")] = {
                    "full_name": user_info.get("full_name"),
                    "email": user_info.get("email"),
                    "phone": row.get("phone"),
                    "profile_photo_url": row.get("profile_photo_url")
                }
        
        offset = _validate_offset(tz_offset_minutes)
        for o in orders:
            user_info = users_map.get(o.get("user_id"), {})
            o["customer_name"] = user_info.get("full_name", "Unknown")
            o["vendor_name"] = vendors_map.get(o.get("restaurant_id"), "Unknown Vendor")
            o["delivery_staff"] = staff_map.get(o.get("assigned_staff_id"))
            o["created_at_local"] = _shift_iso(o.get("created_at"), offset)
            o["updated_at_local"] = _shift_iso(o.get("updated_at"), offset)
        return {"orders": orders, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/transactions")
async def admin_list_transactions(type_filter: Optional[str] = None, status_filter: Optional[str] = None, limit: int = 100, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        q = supabase.table("transactions").select("id, wallet_id, user_id, type, amount, description, status, payment_method, transaction_date, created_at")
        if type_filter:
            q = q.eq("type", type_filter)
        if status_filter:
            q = q.eq("status", status_filter)
        res = q.order("transaction_date", desc=True).limit(limit).execute()
        rows = res.data or []
        user_ids = list({r.get("user_id") for r in rows if r.get("user_id")})
        users_map: Dict[str, Dict[str, Any]] = {}
        if user_ids:
            u_resp = supabase.table("users").select("id, full_name, email, role").in_("id", user_ids).execute()
            for u in u_resp.data or []:
                users_map[u.get("id")] = u
        offset = _validate_offset(tz_offset_minutes)
        for r in rows:
            r["user"] = users_map.get(r.get("user_id"))
            r["transaction_date_local"] = _shift_iso(r.get("transaction_date"), offset)
            r["created_at_local"] = _shift_iso(r.get("created_at"), offset)
        return {"transactions": rows, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== System Settings =====================
@router.get("/settings")
async def admin_list_settings(current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        res = supabase.table("system_settings").select("id, key, value, description, created_at, updated_at").order("key", desc=False).execute()
        offset = _validate_offset(tz_offset_minutes)
        rows = res.data or []
        for s in rows:
            s["created_at_local"] = _shift_iso(s.get("created_at"), offset)
            s["updated_at_local"] = _shift_iso(s.get("updated_at"), offset)
        return {"settings": rows, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/settings/{key}")
async def admin_update_setting(key: str, body: SettingUpdate, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        existing = supabase.table("system_settings").select("id").eq("key", key).limit(1).execute()
        payload = {
            "value": body.value,
            "description": body.description,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        offset = _validate_offset(tz_offset_minutes)
        if existing.data:
            upd = supabase.table("system_settings").update(payload).eq("key", key).execute()
            row = upd.data[0]
        else:
            payload["key"] = key
            created_at = datetime.now(timezone.utc).isoformat()
            payload["created_at"] = created_at
            ins = supabase.table("system_settings").insert(payload).execute()
            if not ins.data:
                raise HTTPException(status_code=500, detail="Failed to create setting")
            row = ins.data[0]
        row["created_at_local"] = _shift_iso(row.get("created_at"), offset)
        row["updated_at_local"] = _shift_iso(row.get("updated_at"), offset)
        return {"setting": row, "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Analytics =====================
@router.get("/analytics")
async def admin_analytics(
    current_user = Depends(get_current_user),
    days: int = 7,
    tz_offset_minutes: Optional[int] = None
):
    try:
        if days < 1 or days > 30:
            days = 7
        # Validate timezone offset (user machine time). Range covers UTC-12 to UTC+14 (~ -720 to +840 minutes)
        if tz_offset_minutes is not None:
            if tz_offset_minutes < -720 or tz_offset_minutes > 840:
                tz_offset_minutes = None
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("role").eq("id", admin_id).limit(1).execute()
        if not role_resp.data or role_resp.data[0].get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        # Orders by status (last N days)
        orders_resp = supabase.table("orders").select("status, total, created_at").order("created_at", desc=True).execute()
        status_counts: Dict[str, int] = {}
        daily_revenue: Dict[str, float] = {}
        now_utc = datetime.now(timezone.utc)
        offset_delta = timedelta(minutes=tz_offset_minutes or 0)
        local_now = now_utc + offset_delta
        cutoff = local_now - timedelta(days=days)
        for o in orders_resp.data or []:
            s = (o.get("status") or "UNKNOWN").strip()
            created_raw = o.get("created_at")
            dt = None
            if created_raw:
                try:
                    # Normalize timestamp string and ensure timezone awareness
                    cleaned = created_raw.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(cleaned)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = None
            if dt:
                dt_local = dt + offset_delta
            else:
                dt_local = None
            # Count orders by status within local window
            if dt_local and dt_local >= cutoff:
                status_counts[s] = status_counts.get(s, 0) + 1
                # Revenue only for final statuses within window
                if s in FINAL_REVENUE_STATUSES:
                    day_key = dt_local.strftime('%Y-%m-%d')
                    try:
                        amt = float(o.get("total") or 0)
                    except (TypeError, ValueError):
                        amt = 0.0
                    daily_revenue[day_key] = daily_revenue.get(day_key, 0.0) + amt
        # Fill missing days (ensure chronological order from oldest -> newest)
        for i in range(days - 1, -1, -1):
            dkey = (local_now - timedelta(days=i)).strftime('%Y-%m-%d')
            daily_revenue.setdefault(dkey, 0.0)
        # Sort daily revenue chronologically
        daily_rev_sorted = [{"date": k, "revenue": daily_revenue[k]} for k in sorted(daily_revenue.keys())]
        return {
            "ordersByStatus": status_counts,
            "dailyRevenue": daily_rev_sorted,
            "timezoneOffsetMinutes": tz_offset_minutes or 0,
            "generatedAt": now_utc.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Admin Change Password =====================
@router.post("/change-password")
async def admin_change_password(body: ChangePasswordBody, current_user = Depends(get_current_user), tz_offset_minutes: Optional[int] = None):
    try:
        admin_id = current_user.get("sub")
        role_resp = supabase.table("users").select("id, role, password_hash").eq("id", admin_id).limit(1).execute()
        if not role_resp.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user_row = role_resp.data[0]
        if user_row.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
        if not verify_password(body.current_password, user_row.get("password_hash") or ""):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
        # Enforce strong policy and prevent reuse
        if body.current_password == body.new_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be different from current password")
        if not body.new_password or len(body.new_password) < 8 or not re.search(r"[A-Z]", body.new_password) or not re.search(r"\d", body.new_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 8 characters and include an uppercase letter and a number")
        new_hash = get_password_hash(body.new_password)
        updated_at = datetime.now(timezone.utc).isoformat()
        upd = supabase.table("users").update({
            "password_hash": new_hash,
            "updated_at": updated_at
        }).eq("id", admin_id).execute()
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to update password")
        offset = _validate_offset(tz_offset_minutes)
        return {"message": "Password updated successfully", "updated_at": updated_at, "updated_at_local": _shift_iso(updated_at, offset), "timezoneOffsetMinutes": offset}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
