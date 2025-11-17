from fastapi import APIRouter, HTTPException, status, Depends, Form, UploadFile, File
from pydantic import BaseModel
from app.db.database import supabase
from datetime import datetime, timezone
from typing import Optional, List
import sys
from app.core.security import get_current_user
from app.utils.file_upload import save_upload_file
from app.api.endpoints.realtime import broadcast_order_event

router = APIRouter()

# ==================== MODELS ====================

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None

class DeliveryStatusUpdate(BaseModel):
    status: str  # picked-up, delivered

# ==================== HELPERS ====================

STAFF_STATUS_MAP = {
    "pending": "PENDING_CONFIRMATION",
    "picked-up": "ON_THE_WAY",
    "delivered": "DELIVERED",
}

VALID_STATUS_TRANSITIONS = {
    "PENDING_CONFIRMATION": ["ON_THE_WAY"],
    "CONFIRMED": ["ON_THE_WAY"],
    "PREPARING": ["ON_THE_WAY"],
    "READY_FOR_PICKUP": ["ON_THE_WAY"],
    "ON_THE_WAY": ["DELIVERED"],
}

# ==================== STAFF PROFILE ====================

@router.get("/profile/{user_id}")
async def get_staff_profile(user_id: str, current=Depends(get_current_user)):
    """
    Get delivery staff profile information.
    Requires authentication. Staff can only view their own profile.
    """
    try:
        # Verify the authenticated user matches the requested profile
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id or auth_user_id != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized to view this profile")
        
        # Fetch user and delivery_staff data
        user_res = supabase.table("users") \
            .select("id, email, full_name, role, status, created_at") \
            .eq("id", user_id) \
            .eq("role", "delivery_staff") \
            .limit(1) \
            .execute()
        
        if not user_res.data:
            raise HTTPException(status_code=404, detail="Staff profile not found")
        
        user = user_res.data[0]
        
        # Fetch delivery_staff record
        staff_res = supabase.table("delivery_staff") \
            .select("id, staff_id, phone, profile_photo_url, vendor_id, created_at") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=404, detail="Delivery staff record not found")
        
        staff = staff_res.data[0]
        
        # Fetch vendor info
        vendor_res = supabase.table("vendor_profiles") \
            .select("business_name, contact_number") \
            .eq("user_id", staff.get("vendor_id")) \
            .limit(1) \
            .execute()
        
        vendor_info = vendor_res.data[0] if vendor_res.data else {}
        
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "full_name": user.get("full_name"),
            "staff_id": staff.get("staff_id"),
            "phone": staff.get("phone"),
            "profile_photo_url": staff.get("profile_photo_url"),
            "role": user.get("role"),
            "status": user.get("status"),
            "vendor": {
                "id": staff.get("vendor_id"),
                "business_name": vendor_info.get("business_name", "Unknown Vendor"),
                "contact_number": vendor_info.get("contact_number", ""),
            },
            "created_at": user.get("created_at"),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_staff_profile: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch staff profile: {str(e)}"
        )

@router.put("/profile/{user_id}")
async def update_staff_profile(
    user_id: str,
    full_name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    current=Depends(get_current_user),
):
    """
    Update delivery staff profile.
    Requires authentication. Staff can only update their own profile.
    """
    try:
        # Verify the authenticated user matches the requested profile
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id or auth_user_id != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized to update this profile")
        
        # Verify staff exists
        staff_res = supabase.table("delivery_staff") \
            .select("id") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=404, detail="Staff profile not found")
        
        # Prepare updates
        user_updates = {}
        staff_updates = {}
        
        if full_name:
            user_updates["full_name"] = full_name
        
        if phone:
            staff_updates["phone"] = phone
        
        # Handle profile photo upload
        if profile_photo is not None:
            try:
                photo_url = await save_upload_file(profile_photo, subfolder="staff")
                staff_updates["profile_photo_url"] = photo_url
            except Exception as e:
                print(f"Profile photo save failed: {e}", file=sys.stderr)
                raise HTTPException(status_code=500, detail="Failed to save profile photo")
        
        # Update users table if needed
        if user_updates:
            user_updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            supabase.table("users").update(user_updates).eq("id", user_id).execute()
        
        # Update delivery_staff table if needed
        if staff_updates:
            staff_updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            supabase.table("delivery_staff").update(staff_updates).eq("user_id", user_id).execute()
        
        # Return updated profile
        return await get_staff_profile(user_id, current)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in update_staff_profile: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update staff profile: {str(e)}"
        )

# ==================== DELIVERIES ====================

@router.get("/deliveries/{user_id}")
async def get_staff_deliveries(user_id: str, current=Depends(get_current_user)):
    """
    Get active deliveries assigned to this delivery staff.
    Returns orders with status: PENDING_CONFIRMATION, CONFIRMED, PREPARING, READY_FOR_PICKUP, ON_THE_WAY
    """
    try:
        # Verify authentication
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id or auth_user_id != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Get staff's delivery_staff record
        staff_res = supabase.table("delivery_staff") \
            .select("id, vendor_id") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=404, detail="Staff record not found")
        
        vendor_id = staff_res.data[0].get("vendor_id")
        staff_id = staff_res.data[0].get("id")
        
        # Fetch active orders assigned to this staff only (exclude pickup orders)
        active_statuses = [
            "PENDING_CONFIRMATION",
            "CONFIRMED", 
            "PAYMENT_PROCESSING",
            "PREPARING",
            "READY_FOR_PICKUP",
            "ON_THE_WAY",
        ]
        
        assigned_res = supabase.table("orders") \
            .select("id, order_code, user_id, items, total, status, created_at, updated_at, assigned_staff_id") \
            .eq("restaurant_id", vendor_id) \
            .eq("assigned_staff_id", staff_id) \
            .in_("status", active_statuses) \
            .order("created_at", desc=False) \
            .execute()
        assigned_orders = assigned_res.data or []

        # Fetch available unassigned deliveries (READY_FOR_PICKUP and unassigned) for same vendor
        available_res = supabase.table("orders") \
            .select("id, order_code, user_id, items, total, status, created_at, updated_at, assigned_staff_id") \
            .eq("restaurant_id", vendor_id) \
            .is_("assigned_staff_id", None) \
            .eq("status", "READY_FOR_PICKUP") \
            .order("created_at", desc=False) \
            .execute()
        available_orders = available_res.data or []
        
        # Fetch customer info
        user_ids = list({o.get("user_id") for o in (assigned_orders + available_orders) if o.get("user_id")})
        users_map = {}
        if user_ids:
            users_res = supabase.table("users").select("id, full_name, email").in_("id", user_ids).execute()
            users_map = {u["id"]: u for u in (users_res.data or [])}
        
        # Also fetch student profiles for delivery addresses (if exists)
        students_map = {}
        if user_ids:
            try:
                students_res = supabase.table("student_profiles") \
                    .select("user_id, organization_name") \
                    .in_("user_id", user_ids) \
                    .execute()
                students_map = {s["user_id"]: s for s in (students_res.data or [])}
            except Exception:
                pass  # Student profiles might not exist for all users
        
        # Format deliveries
        deliveries = []
        deliveries = []
        for order in assigned_orders:
            user = users_map.get(order.get("user_id"), {})
            student = students_map.get(order.get("user_id"), {})
            
            # Map DB status to frontend status
            db_status = order.get("status", "PENDING_CONFIRMATION")
            if db_status in ["PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_PROCESSING", "PREPARING", "READY_FOR_PICKUP"]:
                frontend_status = "pending"
            elif db_status == "ON_THE_WAY":
                frontend_status = "in-progress"
            else:
                frontend_status = "pending"
            
            deliveries.append({
                "id": order.get("id"),
                "order_code": order.get("order_code"),
                "customer_name": user.get("full_name", "Customer"),
                "customer_email": user.get("email", ""),
                "delivery_address": student.get("organization_name", "Campus Location"),
                "items": order.get("items", []),
                "total": order.get("total", 0),
                "status": frontend_status,
                "created_at": order.get("created_at"),
                "updated_at": order.get("updated_at"),
                "available": False,
            })

        # Add available unassigned deliveries
        for order in available_orders:
            user = users_map.get(order.get("user_id"), {})
            student = students_map.get(order.get("user_id"), {})
            deliveries.append({
                "id": order.get("id"),
                "order_code": order.get("order_code"),
                "customer_name": user.get("full_name", "Customer"),
                "customer_email": user.get("email", ""),
                "delivery_address": student.get("organization_name", "Campus Location"),
                "items": order.get("items", []),
                "total": order.get("total", 0),
                "status": "pending",
                "created_at": order.get("created_at"),
                "updated_at": order.get("updated_at"),
                "available": True,
            })

        return {"deliveries": deliveries}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_staff_deliveries: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch deliveries: {str(e)}"
        )

@router.get("/history/{user_id}")
async def get_delivery_history(user_id: str, current=Depends(get_current_user)):
    """
    Get completed delivery history for this staff member.
    Returns orders with status: COMPLETED, DELIVERED
    """
    try:
        # Verify authentication
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id or auth_user_id != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Get staff's delivery_staff record
        staff_res = supabase.table("delivery_staff") \
            .select("id, vendor_id") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=404, detail="Staff record not found")
        
        vendor_id = staff_res.data[0].get("vendor_id")
        staff_id = staff_res.data[0].get("id")
        
        # Fetch completed orders
        completed_statuses = ["COMPLETED", "DELIVERED", "RATING_PENDING"]
        
        orders_res = supabase.table("orders") \
            .select("id, order_code, user_id, items, total, rating, status, updated_at, assigned_staff_id") \
            .eq("restaurant_id", vendor_id) \
            .eq("assigned_staff_id", staff_id) \
            .in_("status", completed_statuses) \
            .order("updated_at", desc=True) \
            .limit(50) \
            .execute()
        
        orders = orders_res.data or []
        
        # Fetch customer info
        user_ids = list({o.get("user_id") for o in orders if o.get("user_id")})
        users_map = {}
        if user_ids:
            users_res = supabase.table("users").select("id, full_name").in_("id", user_ids).execute()
            users_map = {u["id"]: u.get("full_name", "Customer") for u in (users_res.data or [])}
        
        # Format history
        history = []
        for order in orders:
            history.append({
                "id": order.get("id"),
                "order_code": order.get("order_code"),
                "customer_name": users_map.get(order.get("user_id"), "Customer"),
                "delivered_at": order.get("updated_at"),
                "rating": order.get("rating"),
                "total": order.get("total", 0),
            })
        
        return {"history": history}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_delivery_history: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch delivery history: {str(e)}"
        )

@router.put("/deliveries/{order_id}/status")
async def update_delivery_status(
    order_id: str,
    status_update: DeliveryStatusUpdate,
    current=Depends(get_current_user),
):
    """
    Update delivery status for an order.
    Valid transitions:
    - pending -> picked-up (status changes to ON_THE_WAY)
    - picked-up -> delivered (status changes to DELIVERED)
    """
    try:
        # Verify authentication
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # Get staff's vendor_id and staff_id
        staff_res = supabase.table("delivery_staff") \
            .select("id, vendor_id, phone") \
            .eq("user_id", auth_user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=403, detail="Staff record not found")
        
        staff_row = staff_res.data[0]
        vendor_id = staff_row.get("vendor_id")
        staff_id = staff_row.get("id")
        
        # Fetch the order
        order_res = supabase.table("orders") \
            .select("id, status, restaurant_id, user_id, assigned_staff_id") \
            .eq("id", order_id) \
            .limit(1) \
            .execute()
        
        if not order_res.data:
            raise HTTPException(status_code=404, detail="Order not found")
        
        order = order_res.data[0]
        
        # Verify order belongs to staff's vendor
        if order.get("restaurant_id") != vendor_id:
            raise HTTPException(status_code=403, detail="Order does not belong to your vendor")
        
        current_status = order.get("status", "PENDING_CONFIRMATION")
        new_frontend_status = status_update.status.lower().strip()
        
        # Map frontend status to DB status
        if new_frontend_status == "picked-up":
            new_db_status = "ON_THE_WAY"
        elif new_frontend_status == "delivered":
            new_db_status = "DELIVERED"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_frontend_status}'. Use 'picked-up' or 'delivered'"
            )
        
        # Validate transition
        allowed_next = VALID_STATUS_TRANSITIONS.get(current_status, [])
        if new_db_status not in allowed_next:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status transition from {current_status} to {new_db_status}"
            )
        
        # Update order status
        update_payload = {
            "status": new_db_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # If picking up and order is unassigned, claim it
        if new_db_status == "ON_THE_WAY" and not order.get("assigned_staff_id"):
            update_payload["assigned_staff_id"] = staff_id
        
        result = supabase.table("orders").update(update_payload).eq("id", order_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update order status")
        
        # Create notification for customer
        try:
            notification_title = "Order Update"
            if new_db_status == "ON_THE_WAY":
                notification_body = "Your order is on the way!"
            elif new_db_status == "DELIVERED":
                notification_body = "Your order has been delivered. Enjoy your meal!"
            else:
                notification_body = f"Your order status: {new_db_status}"
            
            supabase.table("notifications").insert({
                "user_id": order.get("user_id"),
                "role": "student",
                "type": "order_update",
                "title": notification_title,
                "body": notification_body,
                "data": {"order_id": order_id, "status": new_db_status},
                "is_read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            # Don't fail the status update if notification fails
            print(f"Failed to create notification: {e}", file=sys.stderr)

        # Broadcast realtime event to vendor, student, and staff
        try:
            await broadcast_order_event({
                "type": "order_status",
                "order_id": order_id,
                "db_status": new_db_status,
                "vendor_id": vendor_id,
                "user_id": order.get("user_id"),
                "staff_user_id": auth_user_id,
                # include staff info for student UI
                "staff": {
                    "full_name": (supabase.table("users").select("full_name").eq("id", auth_user_id).limit(1).execute().data or [{}])[0].get("full_name"),
                    "phone": staff_row.get("phone"),
                }
            })
        except Exception as be:
            print(f"Broadcast failed (staff order_status): {be}", file=sys.stderr)

        # Award promo points on delivered (basic rule: 1 point per ₱100)
        if new_db_status == "DELIVERED":
            try:
                # get order total for points calculation
                o2 = supabase.table("orders").select("total").eq("id", order_id).limit(1).execute()
                total_amt = float((o2.data[0].get("total") if (o2.data and o2.data[0]) else 0) or 0)
                reward_points = int(total_amt // 100) if total_amt > 0 else 0
                if reward_points > 0:
                    # increment student_profiles.points
                    prof = supabase.table("student_profiles").select("points").eq("user_id", order.get("user_id")).limit(1).execute()
                    current_pts = int((prof.data[0].get("points") if (prof.data and prof.data[0]) else 0) or 0)
                    supabase.table("student_profiles").update({
                        "points": current_pts + reward_points,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("user_id", order.get("user_id")).execute()
                    # broadcast points awarded
                    try:
                        await broadcast_order_event({
                            "type": "points_awarded",
                            "order_id": order_id,
                            "reward_points": reward_points,
                            "vendor_id": vendor_id,
                            "user_id": order.get("user_id"),
                            "staff_user_id": auth_user_id,
                        })
                    except Exception:
                        pass
            except Exception as pe:
                print(f"Failed to award points: {pe}", file=sys.stderr)
        
        return {
            "message": "Delivery status updated successfully",
            "order": result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in update_delivery_status: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update delivery status: {str(e)}"
        )

# ==================== STATS ====================

@router.get("/stats/{user_id}")
async def get_staff_stats(user_id: str, current=Depends(get_current_user)):
    """
    Get delivery statistics for staff dashboard overview.
    """
    try:
        # Verify authentication
        auth_user_id = current.get("sub") if isinstance(current, dict) else None
        if not auth_user_id or auth_user_id != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Get staff's vendor_id
        staff_res = supabase.table("delivery_staff") \
            .select("vendor_id") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not staff_res.data:
            raise HTTPException(status_code=404, detail="Staff record not found")
        
        vendor_id = staff_res.data[0].get("vendor_id")
        
        # Get today's date
        today = datetime.now(timezone.utc).date()
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        
        # Total completed deliveries (all time)
        total_res = supabase.table("orders") \
            .select("id", count="exact") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .execute()
        
        total_deliveries = total_res.count if hasattr(total_res, 'count') else 0
        
        # Completed today
        today_res = supabase.table("orders") \
            .select("id", count="exact") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["COMPLETED", "DELIVERED"]) \
            .gte("updated_at", today_start.isoformat()) \
            .lte("updated_at", today_end.isoformat()) \
            .execute()
        
        completed_today = today_res.count if hasattr(today_res, 'count') else 0
        
        # Active orders
        active_res = supabase.table("orders") \
            .select("id", count="exact") \
            .eq("restaurant_id", vendor_id) \
            .in_("status", ["PENDING_CONFIRMATION", "CONFIRMED", "PREPARING", "READY_FOR_PICKUP", "ON_THE_WAY"]) \
            .execute()
        
        active_orders = active_res.count if hasattr(active_res, 'count') else 0
        
        return {
            "total_deliveries": total_deliveries,
            "completed_today": completed_today,
            "active_orders": active_orders,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_staff_stats: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch staff stats: {str(e)}"
        )
