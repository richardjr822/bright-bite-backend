from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form, Request, Depends, BackgroundTasks
from pydantic import BaseModel
from app.db.database import supabase
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import sys
import asyncio
from app.utils.file_upload import save_upload_file
from app.api.endpoints.realtime import broadcast_order_event
from app.core.security import get_password_hash, get_current_user
import secrets
import string
import os
import requests
import time
import resend

router = APIRouter()

# Configure Resend SDK
try:
    resend.api_key = os.getenv("RESEND_API_KEY", "")
except Exception:
    pass

# ==================== MODELS ====================

class MenuItem(BaseModel):
    name: str
    description: str
    price: float
    category: str
    image_url: Optional[str] = None
    is_available: bool = True

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    image_url: Optional[str] = None
    is_available: Optional[bool] = None

class OrderStatusUpdate(BaseModel):
    status: str  # pending, preparing, ready, completed, cancelled

class AssignOrderBody(BaseModel):
    staff_user_id: Optional[str] = None
    staff_id: Optional[str] = None


# ==================== HELPERS ====================

UI_TO_DB_STATUS: Dict[str, str] = {
    "pending": "PENDING_CONFIRMATION",
    "preparing": "PREPARING",
    "ready": "READY_FOR_PICKUP",
    "completed": "COMPLETED",
    "cancelled": "REJECTED",
}

DB_TO_UI_STATUS: Dict[str, str] = {
    "PENDING_CONFIRMATION": "pending",
    "CONFIRMED": "pending",
    "PAYMENT_PROCESSING": "pending",
    "PREPARING": "preparing",
    "READY_FOR_PICKUP": "ready",
    "COMPLETED": "completed",
    "RATING_PENDING": "completed",
    "REJECTED": "cancelled",
}

PENDING_DB_STATUSES = [
    "PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_PROCESSING", "PREPARING", "READY_FOR_PICKUP"
]

# ==================== VENDOR LISTING ====================

@router.get("/list")
async def list_vendors():
    """
    Return basic list of vendors (users with role='vendor').
    """
    try:
        # Fetch vendors (users table)
        res = supabase.table("users").select("id, full_name, organization, email").eq("role", "vendor").execute()
        rows = res.data or []
        vendor_ids = [r.get("id") for r in rows if r.get("id")]

        # Attempt to fetch vendor_profiles for logos in one batch
        logos_map = {}
        try:
            if vendor_ids:
                vpres = supabase.table("vendor_profiles").select("user_id, logo_url, business_name, business_address").in_("user_id", vendor_ids).execute()
                for vp in (vpres.data or []):
                    logos_map[vp.get("user_id")] = {
                        "logo_url": vp.get("logo_url"),
                        "business_address": vp.get("business_address") or "",
                    }
        except Exception:
            logos_map = {}

        vendors = []
        for v in rows:
            vid = v.get("id")
            vp = logos_map.get(vid) or {}
            vendors.append({
                "id": vid,
                "name": v.get("organization") or v.get("full_name") or "Vendor",
                "description": v.get("full_name") or "",
                "rating": 4.7,
                "reviews": 0,
                "location": vp.get("business_address") or "Campus",
                "type": "campus_canteen",
                "isOpen": True,
                "prepTime": "10-15 min",
                # expose logo url (relative path like /uploads/vendor-logos/.. or absolute)
                "logoUrl": vp.get("logo_url"),
            })
        return {"vendors": vendors}
    except Exception as e:
        print(f"Error in list_vendors: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to list vendors")

# ==================== NOTIFICATIONS ====================

@router.get("/notifications/{vendor_id}")
async def get_vendor_notifications(vendor_id: str):
    """
    Get notifications for a vendor. Returns empty list if table doesn't exist.
    """
    try:
        res = supabase.table("notifications") \
            .select("id, vendor_id, type, title, message, created_at, read, redirect_to, order_id, color") \
            .eq("vendor_id", vendor_id) \
            .order("created_at", desc=True) \
            .execute()

        return {"notifications": res.data or []}
    except Exception as e:
        # If table is missing or any other issue, return empty list gracefully
        print(f"Error in get_vendor_notifications: {str(e)}", file=sys.stderr)
        return {"notifications": []}


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    try:
        res = supabase.table("notifications").update({
            "read": True,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", notification_id).execute()

        if not res.data:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"message": "Marked as read"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in mark_notification_read: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to mark notification read")


@router.put("/notifications/{vendor_id}/read-all")
async def mark_all_notifications_read(vendor_id: str):
    try:
        supabase.table("notifications").update({
            "read": True,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("vendor_id", vendor_id).execute()
        return {"message": "All notifications marked as read"}
    except Exception as e:
        print(f"Error in mark_all_notifications_read: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to mark all notifications read")


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    try:
        supabase.table("notifications").delete().eq("id", notification_id).execute()
        return {"message": "Notification deleted"}
    except Exception as e:
        print(f"Error in delete_notification: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to delete notification")

# ==================== VENDOR PROFILE / LOGO ====================

@router.post("/profile/logo")
async def upload_vendor_logo(logo: UploadFile = File(...), current=Depends(get_current_user)):
    """Upload vendor logo image and persist its URL on vendor_profiles when available.
    Returns the public URL to the uploaded logo.
    """
    try:
        vendor_id = current.get("sub") if isinstance(current, dict) else None
        if not vendor_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Save file to uploads/vendor-logos
        logo_url = await save_upload_file(logo, subfolder="vendor-logos")

        # Best-effort update vendor_profiles.logo_url if column exists
        try:
            supabase.table("vendor_profiles").update({
                "logo_url": logo_url,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", vendor_id).execute()
        except Exception as e:
            # Non-fatal if column missing
            print(f"upload_vendor_logo: vendor_profiles update skipped: {e}", file=sys.stderr)

        return {"logo_url": logo_url}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in upload_vendor_logo: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to upload logo")

# ==================== VENDOR DASHBOARD ====================

@router.get("/dashboard/{vendor_id}")
async def get_vendor_dashboard(vendor_id: str):
    """
    Get vendor dashboard overview data
    """
    try:
        # Get vendor info
        vendor = supabase.table("users").select("id, full_name, organization").eq("id", vendor_id).eq("role", "vendor").execute()
        
        if not vendor.data:
            raise HTTPException(status_code=404, detail="Vendor not found")
        
        vendor_data = vendor.data[0]

        # Fetch vendor profile for additional info like logo_url
        logo_url = None
        try:
            vp = supabase.table("vendor_profiles").select("logo_url, business_name").eq("user_id", vendor_id).limit(1).execute()
            if vp.data:
                logo_url = (vp.data[0] or {}).get("logo_url")
        except Exception:
            logo_url = None
        
        # Get today's date
        today = datetime.now(timezone.utc).date()
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        
        # Get orders for today
        today_orders = supabase.table("orders") \
            .select("id") \
            .eq("restaurant_id", vendor_id) \
            .gte("created_at", today_start.isoformat()) \
            .lte("created_at", today_end.isoformat()) \
            .execute()
        
        # Get pending orders
        pending_orders = supabase.table("orders") \
            .select("id, status") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", PENDING_DB_STATUSES) \
            .execute()
        
        # Get all orders for the vendor
        all_orders_res = supabase.table("orders") \
            .select("id, user_id, items, total, status, created_at, updated_at") \
            .eq("restaurant_id", vendor_id) \
            .order("created_at", desc=True) \
            .limit(100) \
            .execute()
        
        # Calculate weekly earnings (last 7 days)
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        weekly_orders = supabase.table("orders") \
            .select("total, status, created_at") \
            .eq("restaurant_id", vendor_id) \
            .gte("created_at", week_ago) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .execute()

        weekly_earnings = sum(order.get("total", 0) for order in (weekly_orders.data or []))
        
        # Get menu items count
        menu_items = supabase.table("menu_items") \
            .select("id, is_available") \
            .eq("vendor_id", vendor_id) \
            .eq("is_available", True) \
            .execute()
        
        # Get recent orders (last 5)
        recent_orders = supabase.table("orders") \
            .select("id, user_id, items, total, status, created_at") \
            .eq("restaurant_id", vendor_id) \
            .order("created_at", desc=True) \
            .limit(5) \
            .execute()
        
        # Format recent orders
        # Batch fetch customer names
        user_ids = list({o.get("user_id") for o in (recent_orders.data or []) if o.get("user_id")})
        users_map = {}
        if user_ids:
            users_res = supabase.table("users").select("id, full_name").in_("id", user_ids).execute()
            users_map = {u["id"]: u.get("full_name") for u in (users_res.data or [])}

        formatted_recent_orders = []
        for order in (recent_orders.data or []):
            items = order.get("items") or []
            item_count = len(items) if isinstance(items, list) else 0
            db_status = order.get("status", "PENDING_CONFIRMATION")
            formatted_recent_orders.append({
                "id": order.get("id"),
                "customerName": users_map.get(order.get("user_id"), "Unknown"),
                "items": items,
                "itemCount": item_count,
                "total": order.get("total", 0),
                "status": DB_TO_UI_STATUS.get(db_status, db_status.lower()),
                "time": order.get("created_at")
            })

        # all orders transformed for sidebar badge
        all_orders = []
        for order in (all_orders_res.data or []):
            db_status = order.get("status", "PENDING_CONFIRMATION")
            all_orders.append({
                "id": order.get("id"),
                "status": DB_TO_UI_STATUS.get(db_status, db_status.lower()),
                "created_at": order.get("created_at"),
            })
        
        return {
            "businessInfo": {
                "name": vendor_data.get("organization") or vendor_data.get("full_name") or "Vendor",
                "description": vendor_data.get("full_name", ""),
                "logoUrl": logo_url,
            },
            "todayOrders": len(today_orders.data) if today_orders.data else 0,
            "pendingOrders": len(pending_orders.data) if pending_orders.data else 0,
            "weeklyEarnings": float(weekly_earnings or 0),
            "menuItems": [m.get("id") for m in (menu_items.data or [])],
            "recentOrders": formatted_recent_orders,
            "allOrders": all_orders,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_vendor_dashboard: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch vendor dashboard data: {str(e)}"
        )

# ==================== ORDERS ====================

@router.get("/orders/{vendor_id}")
async def get_vendor_orders(vendor_id: str, status_filter: Optional[str] = None):
    """
    Get all orders for a vendor with optional status filter
    """
    try:
        query = supabase.table("orders") \
            .select("id, user_id, items, total, status, promos, created_at, updated_at, assigned_staff_id, proof_of_delivery_url") \
            .eq("restaurant_id", vendor_id) \
            .order("created_at", desc=True)

        # Map UI filter to DB statuses
        if status_filter and status_filter != "all":
            db_status = UI_TO_DB_STATUS.get(status_filter)
            if db_status:
                query = query.eq("status", db_status)

        orders_res = query.execute()

        orders = orders_res.data or []

        # Batch fetch user info
        user_ids = list({o.get("user_id") for o in orders if o.get("user_id")})
        users_map = {}
        if user_ids:
            users_res = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
            users_map = {u["id"]: {"full_name": u.get("full_name"), "email": u.get("email")} for u in (users_res.data or [])}

        # Batch fetch staff info with phone and profile photo
        staff_ids = list({o.get("assigned_staff_id") for o in orders if o.get("assigned_staff_id")})
        staff_users_map: Dict[str, Dict] = {}
        if staff_ids:
            try:
                ds_res = supabase.table("delivery_staff").select("id, user_id, phone, profile_photo_url").in_("id", staff_ids).execute()
                ds_list = ds_res.data or []
                staff_user_ids = [row.get("user_id") for row in ds_list if row.get("user_id")]
                user_map2: Dict[str, Dict] = {}
                if staff_user_ids:
                    users_res2 = supabase.table("users").select("id, full_name, email").in_("id", staff_user_ids).execute()
                    user_map2 = {u["id"]: {"full_name": u.get("full_name"), "email": u.get("email")} for u in (users_res2.data or [])}
                for row in ds_list:
                    user_info = user_map2.get(row.get("user_id"), {})
                    staff_users_map[row.get("id")] = {
                        "full_name": user_info.get("full_name"),
                        "email": user_info.get("email"),
                        "phone": row.get("phone"),
                        "profile_photo_url": row.get("profile_photo_url")
                    }
            except Exception as e:
                print(f"Failed to build staff map: {e}", file=sys.stderr)

        transformed = []
        for o in orders:
            db_status = o.get("status") or "PENDING_CONFIRMATION"
            promos = o.get("promos")
            assigned_staff_id = o.get("assigned_staff_id")
            # Determine fulfillment: prefer explicit promos.fulfillment set at order creation; fallback to staff assignment
            fulfillment = None
            try:
                if isinstance(promos, dict):
                    f = promos.get("fulfillment") or promos.get("serviceType")
                    if isinstance(f, str) and f.lower() in {"delivery", "pickup"}:
                        fulfillment = f.lower()
            except Exception:
                pass
            if not fulfillment:
                fulfillment = "delivery" if assigned_staff_id else "pickup"
            transformed.append({
                "id": o.get("id"),
                "created_at": o.get("created_at"),
                "updated_at": o.get("updated_at"),
                "status": DB_TO_UI_STATUS.get(db_status, db_status.lower()),
                "order_items": o.get("items") or [],
                "total_amount": float(o.get("total", 0)),
                "users": users_map.get(o.get("user_id"), {}),
                "promos": promos or None,
                "fulfillment": fulfillment,
                "assigned_staff": staff_users_map.get(assigned_staff_id, None),
                "proof_of_delivery_url": o.get("proof_of_delivery_url"),
            })

        return {"orders": transformed}
        
    except Exception as e:
        print(f"Error in get_vendor_orders: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch orders: {str(e)}"
        )

@router.put("/orders/{order_id}/status")
async def update_order_status(order_id: str, status_update: OrderStatusUpdate):
    """
    Update order status
    """
    try:
        valid_statuses = set(UI_TO_DB_STATUS.keys())
        incoming = (status_update.status or "").strip().lower()

        if incoming not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status '{incoming}'. Allowed: {sorted(valid_statuses)}")

        # Fetch order first to ensure existence
        existing_res = supabase.table("orders").select("id, status, updated_at, assigned_staff_id, restaurant_id, user_id, items, total").eq("id", order_id).limit(1).execute()
        if hasattr(existing_res, "error") and existing_res.error:
            raise HTTPException(status_code=500, detail=f"Order fetch failed: {getattr(existing_res.error, 'message', existing_res.error)}")
        if not existing_res.data:
            raise HTTPException(status_code=404, detail="Order not found")

        row = existing_res.data[0]
        current = row.get("status") or "PENDING_CONFIRMATION"
        target_db = UI_TO_DB_STATUS[incoming]

        # No-op if already target
        if current == target_db:
            return {"message": "Status unchanged (already set)", "order": existing_res.data[0]}

        # Enforce: if delivery (assigned to staff), vendor cannot progress to logistics-completion statuses
        is_delivery = bool(row.get("assigned_staff_id"))
        if is_delivery and target_db in {"ON_THE_WAY", "DELIVERED", "COMPLETED"}:
            raise HTTPException(status_code=403, detail="Delivery orders must be progressed by delivery staff")

        update_payload = {
            "status": target_db,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        result = supabase.table("orders").update(update_payload).eq("id", order_id).execute()

        debug_info = {
            "incoming": incoming,
            "target_db": target_db,
            "order_id": order_id,
            "has_error_attr": hasattr(result, "error"),
        }
        print(f"update_order_status debug: {debug_info}", file=sys.stderr)

        if hasattr(result, "error") and result.error:
            err_obj = result.error
            err_msg = getattr(err_obj, 'message', None) or (isinstance(err_obj, dict) and err_obj.get('message')) or str(err_obj)
            raise HTTPException(status_code=400, detail=f"Update failed: {err_msg}")

        if not result.data:
            # Re-fetch to see if row exists but no change applied
            post_res = supabase.table("orders").select("id, status, updated_at").eq("id", order_id).limit(1).execute()
            if post_res.data:
                return {"message": "No changes applied (possibly identical status)", "order": post_res.data[0]}
            raise HTTPException(status_code=404, detail="Order not found after update attempt")

        updated = result.data[0]

        # Broadcast event with both db and ui statuses & minimal order snapshot
        db_status_after = updated.get("status")
        ui_status = DB_TO_UI_STATUS.get(db_status_after, db_status_after.lower())
        # Determine staff user id if order assigned
        staff_user_id = None
        try:
            staff_id = row.get("assigned_staff_id")
            if staff_id:
                ds_res = supabase.table("delivery_staff").select("id, user_id").eq("id", staff_id).limit(1).execute()
                if ds_res.data:
                    staff_user_id = ds_res.data[0].get("user_id")
        except Exception as _e:
            staff_user_id = None

        try:
            await broadcast_order_event({
                "type": "order_status",
                "order_id": updated.get("id"),
                "db_status": db_status_after,
                "ui_status": ui_status,
                "vendor_id": updated.get("restaurant_id") or row.get("restaurant_id"),
                "user_id": row.get("user_id"),
                "staff_user_id": staff_user_id,
                "order": {
                    "items": row.get("items") or [],
                    "total": float(row.get("total", 0) or 0),
                }
            })
        except Exception as be:
            print(f"Broadcast failed (order_status): {be}", file=sys.stderr)

        return {"message": "Order status updated successfully", "order": updated}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in update_order_status: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update order status: {str(e)}"
        )

# ==================== DELIVERY STAFF ====================

def _generate_staff_id() -> str:
    # DS-YYMMDD-XXXXXX
    suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"DS-{datetime.now(timezone.utc).strftime('%y%m%d')}-{suffix}"

def _generate_password(length: int = 12) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$%"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@router.post("/delivery-staff")
async def create_delivery_staff(
    request: Request,
    background_tasks: BackgroundTasks,
    firstName: str = Form(...),
    lastName: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    profilePhoto: Optional[UploadFile] = File(None),
    current=Depends(get_current_user),
):
    """
    Create a delivery staff account for the authenticated vendor.
    - Generates a secure initial password and a unique staff_id
    - Creates a user with role 'delivery_staff'
    - Links it in delivery_staff table with vendor_id = current vendor
    - Saves optional profile photo and returns its URL
    Returns created identifiers and initial password for vendor to share.
    """
    try:
        vendor_id = current.get("sub") if isinstance(current, dict) else None
        if not vendor_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Ensure vendor exists in vendor_profiles (FK requirement)
        vp = supabase.table("vendor_profiles").select("user_id").eq("user_id", vendor_id).limit(1).execute()
        if not (vp.data and len(vp.data) > 0):
            raise HTTPException(status_code=403, detail="Vendor profile not found or not approved")

        # Check for existing user by email
        existing = supabase.table("users").select("id").eq("email", email).limit(1).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail="Email already in use")

        # Save profile photo if present
        profile_photo_url = None
        if profilePhoto is not None:
            try:
                profile_photo_url = await save_upload_file(profilePhoto, subfolder="staff")
            except Exception as e:
                print(f"Profile photo save failed: {e}", file=sys.stderr)
                raise HTTPException(status_code=500, detail="Failed to save profile photo")

        # Generate credentials
        initial_password = _generate_password()
        password_hash = get_password_hash(initial_password)
        full_name = f"{firstName.strip()} {lastName.strip()}".strip()

        # Create user
        user_payload = {
            "email": email,
            "password_hash": password_hash,
            "full_name": full_name or "Delivery Staff",
            "role": "delivery_staff",
            "status": "active",
            "email_verified": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        user_res = supabase.table("users").insert(user_payload).execute()
        if hasattr(user_res, "error") and user_res.error:
            msg = getattr(user_res.error, 'message', str(user_res.error))
            raise HTTPException(status_code=400, detail=f"Failed to create user: {msg}")
        if not user_res.data:
            raise HTTPException(status_code=500, detail="User creation returned no data")
        new_user_id = user_res.data[0].get("id")

        # Generate a staff_id and ensure uniqueness (retry a few times)
        staff_id = _generate_staff_id()
        for _ in range(3):
            check = supabase.table("delivery_staff").select("id").eq("staff_id", staff_id).limit(1).execute()
            if not check.data:
                break
            staff_id = _generate_staff_id()

        ds_payload = {
            "user_id": new_user_id,
            "vendor_id": vendor_id,
            "staff_id": staff_id,
            "phone": phone,
            "profile_photo_url": profile_photo_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        ds_res = supabase.table("delivery_staff").insert(ds_payload).execute()
        if hasattr(ds_res, "error") and ds_res.error:
            # Rollback user if delivery_staff insert fails
            try:
                supabase.table("users").delete().eq("id", new_user_id).execute()
            except Exception:
                pass
            msg = getattr(ds_res.error, 'message', str(ds_res.error))
            raise HTTPException(status_code=400, detail=f"Failed to create delivery staff record: {msg}")

        # Queue welcome email (non-blocking)
        try:
            background_tasks.add_task(
                _send_delivery_staff_welcome_email,
                to_email=email,
                staff_name=full_name or "Delivery Staff",
                staff_id=staff_id,
                initial_password=initial_password,
            )
            email_queued = True
        except Exception as e:
            print(f"Email queue failed: {e}", file=sys.stderr)
            email_queued = False

        return {
            "message": "Delivery staff created",
            "user_id": new_user_id,
            "staff_id": staff_id,
            "initial_password": initial_password,
            "profile_photo_url": profile_photo_url,
            "email_queued": email_queued,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in create_delivery_staff: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to create delivery staff: {str(e)}")


# ================ LIST VENDOR DELIVERY STAFF ==================

@router.get("/delivery-staff")
async def list_delivery_staff(current=Depends(get_current_user)):
    """
    List delivery staff for the authenticated vendor. Returns basic info for assignment UI.
    """
    try:
        vendor_id = current.get("sub") if isinstance(current, dict) else None
        if not vendor_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Ensure vendor exists (optional soft check)
        try:
            vp = supabase.table("vendor_profiles").select("user_id").eq("user_id", vendor_id).limit(1).execute()
            if not (vp.data and len(vp.data) > 0):
                # Not fatal, but likely means vendor isn't approved/registered correctly
                pass
        except Exception:
            pass

        ds_res = supabase.table("delivery_staff").select("id, user_id, staff_id, profile_photo_url, phone").eq("vendor_id", vendor_id).order("created_at", desc=True).execute()
        ds_list = ds_res.data or []

        user_ids = [row.get("user_id") for row in ds_list if row.get("user_id")]
        users_map: Dict[str, Dict] = {}
        if user_ids:
            users_res = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
            users_map = {u["id"]: {"full_name": u.get("full_name"), "email": u.get("email")} for u in (users_res.data or [])}

        result = []
        for row in ds_list:
            u = users_map.get(row.get("user_id"), {})
            result.append({
                "id": row.get("id"),
                "staff_id": row.get("staff_id"),
                "user_id": row.get("user_id"),
                "full_name": u.get("full_name"),
                "email": u.get("email"),
                "profile_photo_url": row.get("profile_photo_url"),
                "phone": row.get("phone"),
            })

        return {"staff": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in list_delivery_staff: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to list delivery staff: {str(e)}")

# ================ ASSIGN ORDERS TO STAFF ==================

@router.post("/orders/{order_id}/assign")
async def assign_order_to_staff(order_id: str, body: AssignOrderBody, current=Depends(get_current_user)):
    """
    Assign an order to a delivery staff member. Only the vendor who owns the order can assign.
    Provide either staff_user_id or staff_id.
    """
    try:
        vendor_id = current.get("sub") if isinstance(current, dict) else None
        if not vendor_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        ord_res = supabase.table("orders").select("id, restaurant_id").eq("id", order_id).limit(1).execute()
        if not ord_res.data:
            raise HTTPException(status_code=404, detail="Order not found")
        order_row = ord_res.data[0]
        if order_row.get("restaurant_id") != vendor_id:
            raise HTTPException(status_code=403, detail="You do not own this order")

        if not (body.staff_user_id or body.staff_id):
            raise HTTPException(status_code=400, detail="staff_user_id or staff_id is required")

        q = supabase.table("delivery_staff").select("id, user_id").eq("vendor_id", vendor_id)
        if body.staff_user_id:
            q = q.eq("user_id", body.staff_user_id)
        if body.staff_id:
            q = q.eq("staff_id", body.staff_id)
        ds_res = q.limit(1).execute()
        if not ds_res.data:
            raise HTTPException(status_code=404, detail="Delivery staff not found for this vendor")
        ds = ds_res.data[0]

        upd = supabase.table("orders").update({
            "assigned_staff_id": ds.get("id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", order_id).execute()
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to assign order")

        # Notify staff
        try:
            supabase.table("notifications").insert({
                "user_id": ds.get("user_id"),
                "role": "delivery_staff",
                "type": "order_update",
                "title": "New Delivery Assigned",
                "body": "You have been assigned a new delivery order.",
                "data": {"order_id": order_id},
                "is_read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as ne:
            print(f"Notification insert failed: {ne}", file=sys.stderr)

        return {"message": "Order assigned", "order": upd.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in assign_order_to_staff: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to assign order: {str(e)}")


def _send_delivery_staff_welcome_email(
    to_email: str,
    staff_name: str,
    staff_id: str,
    initial_password: str,
):
    """Send delivery staff welcome email via Resend (SMTP removed)."""
    try:
        RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
        RESEND_FROM = os.getenv("RESEND_FROM", "BrightBite <no-reply@brightbite.com>")
        if not RESEND_API_KEY:
            print("Resend API key missing; skipping welcome email", file=sys.stderr)
            return False
        subject = "Welcome to BrightBite Delivery"
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>
        <style>body {{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f7f7f8;padding:20px;}}.card {{max-width:640px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,0.08);}}.header {{background:linear-gradient(135deg,#14b8a6,#0ea5e9);color:#fff;padding:28px 24px;}}.title {{margin:0;font-size:22px;font-weight:800;}}.content {{padding:24px;color:#111827;}}.muted {{color:#4b5563;}}.box {{background:#f8fafc;border:1px dashed #cbd5e1;border-radius:12px;padding:16px;margin:16px 0;}}.code {{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;}}.footer {{background:#f9fafb;padding:16px;text-align:center;color:#6b7280;font-size:12px;}}</style>
        </head>
        <body>
        <div class='card'>
          <div class='header'><h1 class='title'>Welcome to BrightBite Delivery</h1></div>
          <div class='content'>
            <p>Hi {staff_name},</p>
            <p class='muted'>Your delivery staff account has been created by your vendor. Use the credentials below to sign in and you will be asked to change your password on first login.</p>
            <div class='box'>
              <p><strong>Staff ID</strong>: <span class='code'>{staff_id}</span></p>
              <p><strong>Login Email</strong>: <span class='code'>{to_email}</span></p>
              <p><strong>Temporary Password</strong>: <span class='code'>{initial_password}</span></p>
            </div>
            <p class='muted'>Sign in at: http://localhost:5173/login</p>
            <p class='muted'>If you did not expect this account, notify your vendor immediately.</p>
          </div>
          <div class='footer'>© {datetime.now(timezone.utc).year} BrightBite. All rights reserved.</div>
        </div>
        </body></html>
        """
        for attempt in range(1, 3):  # 2 attempts
            try:
                resp = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={"from": RESEND_FROM, "to": to_email, "subject": subject, "html": html},
                    timeout=10,
                )
                if resp.status_code in (200, 201):
                    print(f"✅ Welcome email sent to {to_email}", file=sys.stderr)
                    return True
                print(f"❌ Resend welcome email error attempt {attempt} {resp.status_code}: {resp.text}", file=sys.stderr)
            except Exception as e:
                print(f"❌ Resend welcome email exception attempt {attempt}: {e}", file=sys.stderr)
            time.sleep(0.5)
        return False
    except Exception as e:
        print(f"❌ Failed to send welcome email to {to_email}: {e}", file=sys.stderr)
        return False

# ==================== MENU MANAGEMENT ====================

@router.get("/menu/{vendor_id}")
async def get_vendor_menu(vendor_id: str):
    """
    Get all menu items for a vendor
    """
    try:
        result = supabase.table("menu_items") \
            .select("*") \
            .eq("vendor_id", vendor_id) \
            .order("category", desc=False) \
            .order("name", desc=False) \
            .execute()
        
        return {"menu_items": result.data or []}
        
    except Exception as e:
        print(f"Error in get_vendor_menu: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch menu items: {str(e)}"
        )

@router.post("/menu/{vendor_id}")
async def create_menu_item(
    vendor_id: str,
    request: Request,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    category: Optional[str] = Form(None),
    is_available: Optional[bool] = Form(True),
    has_discount: Optional[bool] = Form(False),
    discount_percentage: Optional[int] = Form(0),
    image: Optional[UploadFile] = File(None),
    calories: Optional[float] = Form(None),
    protein: Optional[float] = Form(None),
    carbs: Optional[float] = Form(None),
    fiber: Optional[float] = Form(None),
):
    """Create a new menu item. Supports multipart form with file upload, or JSON body fallback."""
    try:
        payload = None
        if (request.headers.get("content-type") or "").startswith("application/json"):
            # JSON fallback for backward compatibility
            payload = await request.json()
            name = payload.get("name")
            description = payload.get("description")
            price = payload.get("price")
            category = payload.get("category")
            is_available = payload.get("is_available", True)
            has_discount = payload.get("has_discount", False)
            discount_percentage = payload.get("discount_percentage", 0)
            image_url = payload.get("image_url")
            calories = payload.get("calories")
            protein = payload.get("protein")
            carbs = payload.get("carbs")
            fiber = payload.get("fiber")
        else:
            image_url = None

        if not name or price is None or not category or not description:
            raise HTTPException(status_code=400, detail="name, description, price, category are required")

        # Save image if provided
        if image is not None:
            try:
                image_url = await save_upload_file(image, subfolder="menu")
            except Exception as e:
                print(f"Image save failed: {e}", file=sys.stderr)
                raise HTTPException(status_code=500, detail="Failed to save image")

        menu_item_data = {
            "vendor_id": vendor_id,
            "name": name,
            "description": description,
            "price": price,
            "category": category,
            "image_url": image_url,
            "is_available": bool(is_available) if is_available is not None else True,
            "has_discount": bool(has_discount) if has_discount is not None else False,
            "discount_percentage": int(discount_percentage or 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if calories is not None:
            try:
                menu_item_data["calories"] = float(calories)
            except Exception:
                pass
        if protein is not None:
            try:
                menu_item_data["protein"] = float(protein)
            except Exception:
                pass
        if carbs is not None:
            try:
                menu_item_data["carbs"] = float(carbs)
            except Exception:
                pass
        if fiber is not None:
            try:
                menu_item_data["fiber"] = float(fiber)
            except Exception:
                pass

        try:
            result = supabase.table("menu_items").insert(menu_item_data).execute()
        except Exception as e:
            try:
                for k in ["calories","protein","carbs","fiber"]:
                    menu_item_data.pop(k, None)
                result = supabase.table("menu_items").insert(menu_item_data).execute()
            except Exception:
                raise e
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create menu item")
        return {"message": "Menu item created successfully", "item": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in create_menu_item: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to create menu item: {str(e)}")

@router.put("/menu/{item_id}")
async def update_menu_item(
    item_id: str,
    request: Request,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    category: Optional[str] = Form(None),
    is_available: Optional[bool] = Form(None),
    has_discount: Optional[bool] = Form(None),
    discount_percentage: Optional[int] = Form(None),
    image: Optional[UploadFile] = File(None),
    calories: Optional[float] = Form(None),
    protein: Optional[float] = Form(None),
    carbs: Optional[float] = Form(None),
    fiber: Optional[float] = Form(None),
):
    """Update a menu item. Supports JSON body or multipart form with optional new image."""
    try:
        # If JSON request, use original logic for compatibility
        if (request.headers.get("content-type") or "").startswith("application/json"):
            payload = await request.json()
            update_data = {k: v for k, v in payload.items() if k in {"name","description","price","category","image_url","is_available","has_discount","discount_percentage","calories","protein","carbs","fiber"}}
            update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result = supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
            except Exception as e:
                try:
                    for k in ["calories","protein","carbs","fiber"]:
                        update_data.pop(k, None)
                    result = supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
                except Exception:
                    raise e
            if not result.data:
                raise HTTPException(status_code=404, detail="Menu item not found")
            return {"message": "Menu item updated successfully", "item": result.data[0]}

        # Multipart form handling
        update_data: Dict[str, object] = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if price is not None:
            update_data["price"] = price
        if category is not None:
            update_data["category"] = category
        if is_available is not None:
            update_data["is_available"] = bool(is_available)
        if has_discount is not None:
            update_data["has_discount"] = bool(has_discount)
        if discount_percentage is not None:
            update_data["discount_percentage"] = int(discount_percentage)
        if calories is not None:
            try:
                update_data["calories"] = float(calories)
            except Exception:
                pass
        if protein is not None:
            try:
                update_data["protein"] = float(protein)
            except Exception:
                pass
        if carbs is not None:
            try:
                update_data["carbs"] = float(carbs)
            except Exception:
                pass
        if fiber is not None:
            try:
                update_data["fiber"] = float(fiber)
            except Exception:
                pass

        if image is not None:
            try:
                image_url = await save_upload_file(image, subfolder="menu")
                update_data["image_url"] = image_url
            except Exception as e:
                print(f"Image save failed: {e}", file=sys.stderr)
                raise HTTPException(status_code=500, detail="Failed to save image")

        if not update_data:
            return {"message": "No changes"}

        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            result = supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
        except Exception as e:
            try:
                for k in ["calories","protein","carbs","fiber"]:
                    update_data.pop(k, None)
                result = supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
            except Exception:
                raise e
        if not result.data:
            raise HTTPException(status_code=404, detail="Menu item not found")
        return {"message": "Menu item updated successfully", "item": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in update_menu_item: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to update menu item: {str(e)}")

@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str):
    """
    Delete a menu item
    """
    try:
        result = supabase.table("menu_items") \
            .delete() \
            .eq("id", item_id) \
            .execute()
        
        return {"message": "Menu item deleted successfully"}
        
    except Exception as e:
        print(f"Error in delete_menu_item: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete menu item: {str(e)}"
        )

@router.patch("/menu/{item_id}/promote")
async def toggle_menu_promotion(item_id: str, request: Request):
    """
    Promote or unpromote a menu item. Only one item per vendor can be promoted.
    """
    try:
        payload = await request.json()
        is_promoted = payload.get("is_promoted", False)
        
        # Get the menu item to find vendor_id
        item_result = supabase.table("menu_items").select("vendor_id").eq("id", item_id).execute()
        if not item_result.data:
            raise HTTPException(status_code=404, detail="Menu item not found")
        
        vendor_id = item_result.data[0]["vendor_id"]
        
        # If promoting, unpromote all other items from this vendor first
        if is_promoted:
            supabase.table("menu_items").update({"is_promoted": False}).eq("vendor_id", vendor_id).execute()
        
        # Update the target item
        update_data = {
            "is_promoted": is_promoted,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Menu item not found")
        
        return {
            "message": f"Menu item {'promoted' if is_promoted else 'unpromoted'} successfully",
            "item": result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in toggle_menu_promotion: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update promotion status: {str(e)}"
        )

@router.get("/ai/recommendations/{vendor_id}")
async def ai_menu_recommendations(vendor_id: str, limit: int = 5):
    """
    Generate AI menu suggestions based on REAL student insights data.
    Analyzes: goals, macro preferences, dietary restrictions, allergies, budgets, calorie targets.
    """
    try:
        # Fetch ALL student preferences, ordered by most recent
        prefs_res = supabase.table("meal_preferences").select(
            "user_id, goal, macro_preference, meals_per_day, daily_budget, "
            "dietary_preference, allergies, calorie_target, health_conditions"
        ).order("updated_at", desc=True).execute()
        all_prefs = prefs_res.data or []
        
        # DEDUPLICATE: Keep only the latest preference per user_id
        seen_users = set()
        prefs = []
        for p in all_prefs:
            user_id = p.get("user_id")
            if user_id and user_id not in seen_users:
                seen_users.add(user_id)
                prefs.append(p)
        
        total_students = len(prefs)
        if total_students == 0:
            return {"recommendations": [], "insights": {"message": "No student data available yet"}}

        # Aggregate student insights
        goals = {}
        macros = {}
        dietary_prefs = {}
        allergies_count = {}
        budgets = []
        calorie_targets = []
        meals_per_day_list = []
        
        for p in prefs:
            # Goals distribution
            g = (p.get("goal") or "maintain").lower()
            goals[g] = goals.get(g, 0) + 1
            
            # Macro preferences
            m = (p.get("macro_preference") or "balanced").lower()
            macros[m] = macros.get(m, 0) + 1
            
            # Dietary preferences (vegan, vegetarian, keto, etc.)
            diets = p.get("dietary_preference") or []
            for d in diets:
                if d:
                    dietary_prefs[d.lower()] = dietary_prefs.get(d.lower(), 0) + 1
            
            # Allergies to avoid
            allergies = p.get("allergies") or []
            for a in allergies:
                if a:
                    allergies_count[a.lower()] = allergies_count.get(a.lower(), 0) + 1
            
            # Budgets
            try:
                if p.get("daily_budget"):
                    budgets.append(float(p.get("daily_budget")))
            except Exception:
                pass
            
            # Calorie targets
            try:
                if p.get("calorie_target"):
                    calorie_targets.append(int(p.get("calorie_target")))
            except Exception:
                pass
            
            # Meals per day
            try:
                if p.get("meals_per_day"):
                    meals_per_day_list.append(int(p.get("meals_per_day")))
            except Exception:
                pass

        # Calculate insights
        def top_items(d: Dict[str, int], n: int = 3) -> list:
            return sorted(d.items(), key=lambda x: x[1], reverse=True)[:n]
        
        def top_key(d: Dict[str, int], default: str) -> str:
            return max(d.items(), key=lambda x: x[1])[0] if d else default

        top_goal = top_key(goals, "maintain")
        top_macro = top_key(macros, "balanced")
        top_diets = top_items(dietary_prefs, 3)
        top_allergies = top_items(allergies_count, 5)
        
        avg_meals = int(sum(meals_per_day_list) / len(meals_per_day_list)) if meals_per_day_list else 3
        avg_budget = float(sum(budgets) / len(budgets)) if budgets else 180.0
        avg_calories = int(sum(calorie_targets) / len(calorie_targets)) if calorie_targets else 2000
        per_meal_calories = avg_calories // avg_meals
        price_target = max(50.0, round(avg_budget / max(1, avg_meals), 0))
        
        # Build allergy exclusion set
        avoid_ingredients = set()
        for allergy, count in allergies_count.items():
            # If more than 10% of students have this allergy, suggest avoiding it
            if count >= max(1, total_students * 0.1):
                avoid_ingredients.add(allergy)

        # Get existing menu items to avoid duplicates
        existing_res = supabase.table("menu_items").select("name").eq("vendor_id", vendor_id).execute()
        existing_names = {(r.get("name") or "").strip().lower() for r in (existing_res.data or [])}

        # Build recommendation pool based on ACTUAL student data
        base_pool = []
        
        # High-protein items (for gain goal or high-protein macro preference)
        if top_goal == "gain" or top_macro == "high-protein":
            base_pool += [
                {"name": "Grilled Chicken Power Bowl", "category": "Main", "calories": 550, "protein": 45, "carbs": 45, "fiber": 7,
                 "reason": f"High protein ({45}g) supports {goals.get('gain', 0)} students with weight gain goals"},
                {"name": "Tuna Quinoa Salad", "category": "Salad", "calories": 480, "protein": 40, "carbs": 35, "fiber": 8,
                 "reason": f"Protein-rich option for {macros.get('high-protein', 0)} high-protein preference students"},
                {"name": "Beef Salpicao Rice Bowl", "category": "Main", "calories": 620, "protein": 42, "carbs": 55, "fiber": 4,
                 "reason": "Filipino-inspired high-protein dish perfect for muscle building"},
            ]
        
        # Low-carb items (for lose goal or low-carb macro preference)
        if top_goal == "lose" or top_macro == "low-carb":
            base_pool += [
                {"name": "Grilled Fish with Veggie Medley", "category": "Main", "calories": 380, "protein": 35, "carbs": 18, "fiber": 8,
                 "reason": f"Low-carb option for {goals.get('lose', 0)} students with weight loss goals"},
                {"name": "Chicken Lettuce Wraps", "category": "Main", "calories": 320, "protein": 32, "carbs": 12, "fiber": 5,
                 "reason": f"Only {12}g carbs - ideal for {macros.get('low-carb', 0)} low-carb preference students"},
                {"name": "Cauliflower Fried Rice", "category": "Main", "calories": 350, "protein": 28, "carbs": 20, "fiber": 6,
                 "reason": "Low-carb rice alternative with high satisfaction"},
            ]
        
        # Vegetarian/Vegan options (based on dietary preferences)
        vegan_count = dietary_prefs.get("vegan", 0)
        vegetarian_count = dietary_prefs.get("vegetarian", 0)
        if vegan_count > 0 or vegetarian_count > 0:
            base_pool += [
                {"name": "Buddha Bowl with Tahini", "category": "Main", "calories": 480, "protein": 18, "carbs": 55, "fiber": 12,
                 "reason": f"Vegan-friendly for {vegan_count} vegan + {vegetarian_count} vegetarian students"},
                {"name": "Tofu Sisig", "category": "Main", "calories": 420, "protein": 24, "carbs": 35, "fiber": 6,
                 "reason": "Plant-based Filipino classic - appeals to vegetarian students"},
                {"name": "Mushroom Adobo Rice Bowl", "category": "Main", "calories": 450, "protein": 14, "carbs": 60, "fiber": 8,
                 "reason": "Hearty vegetarian option with authentic Filipino flavors"},
            ]
        
        # Balanced/maintain options (most common)
        if top_goal == "maintain" or top_macro == "balanced":
            base_pool += [
                {"name": "Chicken Inasal with Brown Rice", "category": "Main", "calories": per_meal_calories, "protein": 35, "carbs": 50, "fiber": 5,
                 "reason": f"Balanced macros matching avg target of {per_meal_calories} kcal/meal"},
                {"name": "Pork Sinigang Bowl", "category": "Main", "calories": 480, "protein": 28, "carbs": 45, "fiber": 6,
                 "reason": f"Filipino comfort food for {goals.get('maintain', 0)} maintain-goal students"},
                {"name": "Bangus Belly with Ensalada", "category": "Main", "calories": 520, "protein": 32, "carbs": 40, "fiber": 7,
                 "reason": "Omega-3 rich local fish - nutritious and satisfying"},
            ]
        
        # Budget-friendly options (if avg budget is low)
        if avg_budget < 150:
            base_pool += [
                {"name": "Arroz Caldo", "category": "Soup", "calories": 380, "protein": 22, "carbs": 50, "fiber": 3,
                 "reason": f"Budget-friendly at ₱{price_target:.0f} - fits avg budget of ₱{avg_budget:.0f}/day"},
                {"name": "Tortang Talong", "category": "Main", "calories": 320, "protein": 18, "carbs": 25, "fiber": 6,
                 "reason": "Affordable vegetable-based option for budget-conscious students"},
            ]
        
        # Keto options
        if dietary_prefs.get("keto", 0) > 0:
            base_pool += [
                {"name": "Keto Lechon Kawali Salad", "category": "Salad", "calories": 450, "protein": 35, "carbs": 8, "fiber": 4,
                 "reason": f"Keto-friendly for {dietary_prefs.get('keto', 0)} keto diet students"},
            ]
        
        # Always add some universally appealing items
        base_pool += [
            {"name": "Garlic Butter Shrimp Rice Bowl", "category": "Main", "calories": 580, "protein": 32, "carbs": 55, "fiber": 4,
             "reason": "Popular choice with balanced nutrition profile"},
            {"name": "Chicken Teriyaki Bowl", "category": "Main", "calories": 520, "protein": 38, "carbs": 48, "fiber": 5,
             "reason": "Student favorite with good protein content"},
        ]

        # Filter out items with common allergens if significant allergy counts
        def has_allergen(item_name: str, allergens: set) -> bool:
            name_lower = item_name.lower()
            allergen_keywords = {
                "shellfish": ["shrimp", "crab", "lobster", "shellfish"],
                "fish": ["fish", "tuna", "salmon", "bangus"],
                "gluten": ["pasta", "bread", "wheat"],
                "dairy": ["cheese", "milk", "cream", "butter"],
                "eggs": ["egg", "tortang"],
                "peanuts": ["peanut"],
                "soy": ["tofu", "soy"],
            }
            for allergen in allergens:
                keywords = allergen_keywords.get(allergen, [allergen])
                if any(kw in name_lower for kw in keywords):
                    return True
            return False

        # Build final recommendations
        recs = []
        for r in base_pool:
            if len(recs) >= max(1, min(limit, 10)):
                break
            
            item_name = r["name"].strip()
            key = item_name.lower()
            
            # Skip if already exists
            if key in existing_names:
                continue
            
            # Skip if contains common allergens
            if has_allergen(item_name, avoid_ingredients):
                continue
            
            recs.append({
                "name": item_name,
                "description": r.get("reason", "Matched to student preferences"),
                "category": r["category"],
                "price": float(price_target),
                "calories": r["calories"],
                "protein": r["protein"],
                "carbs": r["carbs"],
                "fiber": r["fiber"],
            })

        # Include insights summary for the vendor
        insights_summary = {
            "total_students_analyzed": total_students,
            "top_goal": f"{top_goal} ({goals.get(top_goal, 0)} students, {round(goals.get(top_goal, 0)/total_students*100)}%)",
            "top_macro": f"{top_macro} ({macros.get(top_macro, 0)} students)",
            "avg_calorie_target": avg_calories,
            "avg_budget": round(avg_budget, 2),
            "suggested_price": price_target,
            "common_dietary_prefs": [f"{d[0]} ({d[1]})" for d in top_diets],
            "allergies_to_avoid": [f"{a[0]} ({a[1]} students)" for a in top_allergies if a[1] >= max(1, total_students * 0.1)],
        }

        return {
            "recommendations": recs,
            "insights": insights_summary
        }
    except Exception as e:
        print(f"ai_menu_recommendations error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")

# ==================== ANALYTICS ====================

@router.get("/analytics/{vendor_id}")
async def get_vendor_analytics(vendor_id: str, days: int = 30):
    """
    Get vendor analytics for the specified number of days
    """
    try:
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        
        # Get all completed orders in the date range
        orders = supabase.table("orders") \
            .select("items, total, created_at") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .gte("created_at", start_date.isoformat()) \
            .lte("created_at", end_date.isoformat()) \
            .execute()
        
        # Calculate metrics
        total_orders = len(orders.data) if orders.data else 0
        total_revenue = sum(o.get("total", 0) for o in (orders.data or []))
        
        # Get popular items
        item_counts = {}
        for order in (orders.data or []):
            for item in (order.get("items") or []):
                item_name = item.get("item_name") or item.get("name") or "Unknown"
                item_counts[item_name] = item_counts.get(item_name, 0) + (item.get("quantity", 0) or 0)
        
        popular_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Daily sales data
        daily_sales = {}
        for order in (orders.data or []):
            created = order.get("created_at")
            if not created:
                continue
            order_date = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
            date_str = order_date.isoformat()
            daily_sales[date_str] = daily_sales.get(date_str, 0) + (order.get("total", 0) or 0)
        
        return {
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "average_order_value": total_revenue / total_orders if total_orders > 0 else 0,
            "popular_items": [{"name": name, "count": count} for name, count in popular_items],
            "daily_sales": [{"date": date, "amount": amount} for date, amount in sorted(daily_sales.items())]
        }
        
    except Exception as e:
        print(f"Error in get_vendor_analytics: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analytics: {str(e)}"
        )

# ==================== EARNINGS ====================

@router.get("/earnings/{vendor_id}")
async def get_vendor_earnings(vendor_id: str):
    """
    Get vendor earnings breakdown
    """
    try:
        # Get all completed orders
        orders = supabase.table("orders") \
            .select("id, total, created_at, status, payment_method") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .order("created_at", desc=True) \
            .execute()
        
        # Calculate totals
        total_earnings = sum(o.get("total", 0) for o in (orders.data or []))
        
        # Calculate this month's earnings
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        monthly_orders = supabase.table("orders") \
            .select("total, status, created_at") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .gte("created_at", month_start.isoformat()) \
            .execute()
        
        monthly_earnings = sum(o.get("total", 0) for o in (monthly_orders.data or []))
        
        # Get earnings by month for the last 6 months
        monthly_breakdown = {}
        for i in range(6):
            month_date = now - timedelta(days=30 * i)
            month_key = month_date.strftime("%Y-%m")
            month_start_date = month_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            if i == 0:
                month_end_date = now
            else:
                month_end_date = (now - timedelta(days=30 * (i - 1))).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            month_orders = supabase.table("orders") \
                .select("total, status, created_at") \
                .eq("restaurant_id", vendor_id) \
                .in_("status", ["COMPLETED", "DELIVERED"]) \
                .gte("created_at", month_start_date.isoformat()) \
                .lt("created_at", month_end_date.isoformat()) \
                .execute()
            
            monthly_breakdown[month_key] = sum(o.get("total", 0) for o in (month_orders.data or []))
        
        wallet_earnings = 0.0
        cash_earnings = 0.0
        for o in (orders.data or []):
            pm = (o.get("payment_method") or "").lower()
            amt = float(o.get("total", 0) or 0)
            if pm == "wallet":
                wallet_earnings += amt
            elif pm == "cash":
                cash_earnings += amt
        return {
            "total_earnings": total_earnings,
            "monthly_earnings": monthly_earnings,
            "total_orders": len(orders.data) if orders.data else 0,
            "monthly_breakdown": [{"month": month, "amount": amount} for month, amount in sorted(monthly_breakdown.items(), reverse=True)],
            "recent_transactions": orders.data[:10] if orders.data else [],
            "wallet_earnings": wallet_earnings,
            "cash_earnings": cash_earnings,
        }
        
    except Exception as e:
        print(f"Error in get_vendor_earnings: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch earnings: {str(e)}"
        )

# ==================== REVIEWS ====================

@router.get("/reviews/{vendor_id}")
async def get_vendor_reviews(vendor_id: str):
    """
    Return reviews for a vendor from vendor_reviews table, including customer info.
    Fallback to order ratings when vendor_reviews is unavailable.
    """
    try:
        # Preferred source: vendor_reviews
        try:
            vr_res = supabase.table("vendor_reviews") \
                .select("id, vendor_id, user_id, order_id, rating, comment, created_at") \
                .eq("vendor_id", vendor_id) \
                .order("created_at", desc=True) \
                .execute()
            vr_list = vr_res.data or []
            user_ids = list({r.get("user_id") for r in vr_list if r.get("user_id")})
            users_map = {}
            if user_ids:
                users_res = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
                users_map = {u["id"]: u for u in (users_res.data or [])}
            reviews = []
            for r in vr_list:
                u = users_map.get(r.get("user_id"), {})
                reviews.append({
                    "id": r.get("id"),
                    "order_id": r.get("order_id"),
                    "customer_name": u.get("full_name", "Customer"),
                    "customer_email": u.get("email", ""),
                    "rating": r.get("rating", 0),
                    "comment": r.get("comment") or "",
                    "vendor_response": None,
                    "responded_at": None,
                    "created_at": r.get("created_at"),
                })
            return {"reviews": reviews}
        except Exception:
            # Fall back to order ratings only
            orders_res = supabase.table("orders") \
                .select("id, user_id, rating, created_at") \
                .eq("restaurant_id", vendor_id) \
                .not_.is_("rating", None) \
                .order("created_at", desc=True) \
                .execute()
            orders = orders_res.data or []
            user_ids = list({o.get("user_id") for o in orders if o.get("user_id")})
            users_map = {}
            if user_ids:
                users_res = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
                users_map = {u["id"]: u for u in (users_res.data or [])}
            reviews = []
            for o in orders:
                u = users_map.get(o.get("user_id"), {})
                reviews.append({
                    "id": o.get("id"),
                    "order_id": o.get("id"),
                    "customer_name": u.get("full_name", "Customer"),
                    "customer_email": u.get("email", ""),
                    "rating": o.get("rating", 0),
                    "comment": "",
                    "vendor_response": None,
                    "responded_at": None,
                    "created_at": o.get("created_at"),
                })
            return {"reviews": reviews}
    except Exception as e:
        print(f"Error in get_vendor_reviews: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Failed to fetch reviews")


class ReviewResponse(BaseModel):
    response: str


@router.post("/reviews/{review_id}/respond")
async def respond_review(review_id: str, body: ReviewResponse):
    """
    Placeholder for responding to a review. Since schema doesn't persist responses,
    acknowledge the request. Extend DB to store response if needed.
    """
    return {"message": "Response received", "review_id": review_id, "response": body.response}
