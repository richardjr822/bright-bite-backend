from fastapi import APIRouter, HTTPException, Request, Body
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import os
from jose import jwt, JWTError
import sys
import uuid
import asyncio

try:
    from app.db.database import supabase
except Exception:
    supabase = None

try:
    from app.api.endpoints.realtime import broadcast_order_event
except Exception:
    broadcast_order_event = None

router = APIRouter(prefix="/student", tags=["student"])

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
    row = {"user_id": user_id, "organization_name": "", "wallet_balance": 0, "points": 0, "created_at": _now_iso(), "updated_at": _now_iso()}
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


@router.get("/profile")
def get_profile(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    # Fetch user
    try:
        ures = sb.table("users").select("*").eq("id", user_id).limit(1).execute()
        urows = getattr(ures, "data", []) or []
        user = urows[0] if urows else {}
    except Exception:
        user = {}

    profile = _ensure_student_profile(user_id)

    return {
        "success": True,
        "user": {
            "id": user.get("id") or user_id,
            "full_name": user.get("full_name") or "",
            "email": user.get("email") or "",
            "organization": user.get("organization") or profile.get("organization_name") or "",
            "phone": user.get("phone") or "",
        },
        "profile": {
            "organization_name": profile.get("organization_name") or user.get("organization") or "",
            "wallet_balance": float(profile.get("wallet_balance", 0) or 0),
            "points": int(profile.get("points", 0) or 0),
        },
    }


@router.put("/profile")
def update_profile(request: Request, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    full_name = (payload.get("fullName") or payload.get("full_name") or "").strip()
    organization = (payload.get("organization") or payload.get("organization_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    # Optional basic normalization: remove spaces
    if phone:
        phone = phone.replace(" ", "")

    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    # Update users table (full_name, organization)
    try:
        update_user: Dict[str, Any] = {}
        if full_name:
            update_user["full_name"] = full_name
        if organization != "":
            update_user["organization"] = organization
        if phone != "":
            update_user["phone"] = phone
        if update_user:
            sb.table("users").update(update_user).eq("id", user_id).execute()
    except Exception:
        pass

    # Ensure profile and update organization_name
    prof = _ensure_student_profile(user_id)
    try:
        if organization != "":
            sb.table("student_profiles").update({"organization_name": organization, "updated_at": _now_iso()}).eq("user_id", user_id).execute()
    except Exception:
        pass

    # Return latest
    try:
        ures = sb.table("users").select("*").eq("id", user_id).limit(1).execute()
        urows = getattr(ures, "data", []) or []
        user = urows[0] if urows else {}
    except Exception:
        user = {}
    try:
        pres = sb.table("student_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        prows = getattr(pres, "data", []) or []
        prof = prows[0] if prows else prof
    except Exception:
        pass

    return {
        "success": True,
        "user": {
            "id": user.get("id") or user_id,
            "full_name": user.get("full_name") or full_name,
            "email": user.get("email") or "",
            "organization": user.get("organization") or organization,
            "phone": user.get("phone") or phone,
        },
        "profile": {
            "organization_name": prof.get("organization_name") or organization,
            "wallet_balance": float(prof.get("wallet_balance", 0) or 0),
            "points": int(prof.get("points", 0) or 0),
        },
    }


# ==================== ORDERS: Create / Get / List / Cancel / Rate ====================

ORDER_STATUS = {
    "PENDING_CONFIRMATION": "PENDING_CONFIRMATION",
    "REJECTED": "REJECTED",
    "CONFIRMED": "CONFIRMED",
    "PAYMENT_PROCESSING": "PAYMENT_PROCESSING",
    "PREPARING": "PREPARING",
    "READY_FOR_PICKUP": "READY_FOR_PICKUP",
    "ON_THE_WAY": "ON_THE_WAY",
    "ARRIVING_SOON": "ARRIVING_SOON",
    "DELIVERED": "DELIVERED",
    "COMPLETED": "COMPLETED",
    "RATING_PENDING": "RATING_PENDING",
}


@router.post("/orders")
async def create_order(request: Request, payload: Dict[str, Any] = Body(default={})):  # type: ignore[no-redef]
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    restaurant_id = str(payload.get("restaurantId") or payload.get("vendorId") or "").strip()
    if not restaurant_id:
        raise HTTPException(status_code=400, detail="restaurantId is required")

    items: List[Dict[str, Any]] = payload.get("items") or []
    # Normalize item shape for DB compatibility
    norm_items = []
    for it in items:
        norm_items.append({
            "item_id": it.get("id"),
            "item_name": it.get("name"),
            "quantity": it.get("quantity", 1),
            "price": float(it.get("price", 0)),
            "customizations": it.get("customizations") or None,
        })

    total = float(payload.get("total", 0))
    payment_method = (payload.get("paymentMethod") or "cash").lower()
    # Accept wallet (platform wallet), cash, and common online methods
    if payment_method not in {"cash", "wallet", "card", "paypal", "gcash", "maya"}:
        payment_method = "cash"
    # Candidate values to satisfy strict DB check constraints
    wallet_candidates = ["WALLET", "E_WALLET", "EWALLET", "PLATFORM_WALLET", "ONLINE_WALLET"]
    cash_candidates = ["CASH", "CASH_ON_PICKUP", "COP"]
    # Generate a unique, human-friendly order code
    order_code = f"BB-{uuid.uuid4().hex[:8].upper()}"

    # Promotions metadata (deal/voucher) stored directly on order (rider removed)
    applied_deal_id = payload.get("appliedDealId") or None
    discount_amount = float(payload.get("discountAmount") or 0)
    voucher_code = payload.get("voucherCode") or None
    # Fulfillment: persist requested service type to fix vendor pickup/delivery mismatch
    service_type = (payload.get("serviceType") or payload.get("fulfillment") or "").strip().lower()
    if service_type not in {"delivery", "pickup"}:
        service_type = None
    promos = None
    try:
        original_subtotal = float(total + discount_amount)
    except Exception:
        original_subtotal = float(total)
    # Always include promos when we need to carry metadata like fulfillment
    if applied_deal_id or voucher_code or discount_amount > 0 or service_type:
        promos = {
            "appliedDealId": applied_deal_id,
            "voucherCode": voucher_code,
            "discountAmount": discount_amount,
            "originalSubtotal": original_subtotal,
        }
        if service_type:
            promos["fulfillment"] = service_type
        # Record original client payment method for audit
        promos["paymentMethod"] = payment_method

    row = {
        "user_id": user_id,
        "restaurant_id": restaurant_id,
        "items": norm_items,
        "total": total,
        # temporary placeholder; will be set in insert attempt loop below
        "payment_method": None,
        "order_code": order_code,
        "status": ORDER_STATUS["PENDING_CONFIRMATION"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    # Only include promos key if column exists (avoid 500 when column missing)
    if promos:
        row["promos"] = promos

    try:
        # Try inserting with candidate payment_method values until one passes the DB check
        candidates = wallet_candidates if payment_method == "wallet" else (cash_candidates if payment_method == "cash" else [payment_method.upper()])
        last_err = None
        created = None
        for cand in candidates:
            try:
                row["payment_method"] = cand
                res = sb.table("orders").insert(row).execute()
                data = getattr(res, "data", []) or []
                if data:
                    created = data[0]
                    break
                # If no data, capture potential error payload and continue
                last_err = getattr(res, "error", None) or "Insert returned no data"
            except Exception as ie:
                last_err = str(ie)
                # Retry only for payment_method check constraint violations
                if "orders_payment_method_check" in last_err:
                    continue
                else:
                    raise
        if not created:
            print(f"create_order insert error (candidates exhausted): {last_err}", file=sys.stderr)
            raise HTTPException(status_code=500, detail=f"Failed to create order: {last_err}")

        # Broadcast order creation with snapshot
        if broadcast_order_event:
            try:
                await broadcast_order_event({
                    "type": "order_created",
                    "order_id": created.get("id"),
                    "db_status": created.get("status"),
                    "vendor_id": restaurant_id,
                    "user_id": user_id,
                    "order": {
                        "items": created.get("items") or [],
                        "total": float(created.get("total", 0) or 0),
                        "created_at": created.get("created_at"),
                    }
                })
            except Exception as be:
                print(f"Broadcast create_order failed: {be}", file=sys.stderr)

        return {
            "success": True,
            "order": {
                "id": created.get("id"),
                "status": created.get("status"),
                "items": created.get("items") or [],
                "total": float(created.get("total", 0)),
                "created_at": created.get("created_at"),
                # Echo serviceType back for immediate UI context
                "serviceType": service_type or None,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"create_order exception: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to create order: {e}")


@router.get("/orders")
def list_my_orders(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").select("id, items, total, status, restaurant_id, rating, payment_method, created_at, updated_at").eq("user_id", user_id).order("created_at", desc=True).execute()
        return {"orders": getattr(res, "data", []) or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch orders: {e}")


@router.get("/orders/{order_id}")
def get_order(request: Request, order_id: str):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").select("id, items, total, status, restaurant_id, payment_method, created_at, updated_at, assigned_staff_id").eq("id", order_id).eq("user_id", user_id).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if not rows:
            raise HTTPException(status_code=404, detail="Order not found")
        order = rows[0]
        # attach delivery staff info if assigned
        if order.get("assigned_staff_id"):
            try:
                ds_res = sb.table("delivery_staff").select("id, user_id, phone, profile_photo_url").eq("id", order.get("assigned_staff_id")).limit(1).execute()
                if ds_res.data:
                    ds = ds_res.data[0]
                    ures = sb.table("users").select("full_name").eq("id", ds.get("user_id")).limit(1).execute()
                    order["delivery_staff"] = {
                        "full_name": (ures.data or [{}])[0].get("full_name"),
                        "phone": ds.get("phone"),
                        "profile_photo_url": ds.get("profile_photo_url"),
                    }
            except Exception:
                pass
        return {"order": order}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch order: {e}")


@router.put("/orders/{order_id}/cancel")
def cancel_order(request: Request, order_id: str):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        res = sb.table("orders").update({
            "status": ORDER_STATUS["REJECTED"],
            "updated_at": _now_iso()
        }).eq("id", order_id).eq("user_id", user_id).execute()
        data = getattr(res, "data", []) or []
        if not data:
            raise HTTPException(status_code=404, detail="Order not found or cannot cancel")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {e}")


@router.post("/orders/{order_id}/rate")
def rate_order(request: Request, order_id: str, payload: Dict[str, Any] = Body(default={})): 
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    rating = int(payload.get("rating", 0) or 0)
    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be 1..5")
    comment: str = str(payload.get("comment") or "").strip()
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        # Fetch the order to validate ownership and get vendor id
        get_res = sb.table("orders").select("id, restaurant_id, user_id, status").eq("id", order_id).eq("user_id", user_id).limit(1).execute()
        order_rows = getattr(get_res, "data", []) or []
        if not order_rows:
            raise HTTPException(status_code=404, detail="Order not found or cannot rate")
        order_row = order_rows[0]
        vendor_id = order_row.get("restaurant_id")

        # Update order rating
        res = sb.table("orders").update({
            "rating": rating,
            "updated_at": _now_iso()
        }).eq("id", order_id).eq("user_id", user_id).execute()
        data = getattr(res, "data", []) or []
        if not data:
            raise HTTPException(status_code=404, detail="Order not found or cannot rate")

        # Persist vendor review with optional comment when available
        try:
            review_payload = {
                "vendor_id": vendor_id,
                "user_id": user_id,
                "order_id": order_id,
                "rating": rating,
                "comment": comment or None,
                "created_at": _now_iso(),
            }
            sb.table("vendor_reviews").insert(review_payload).execute()
        except Exception as e:
            # Non-fatal; continue even if review table isn't available
            print(f"rate_order: vendor_reviews insert failed: {e}", file=sys.stderr)

        # Notify vendor about new review (best-effort)
        try:
            notif = {
                "user_id": vendor_id,
                "role": "vendor",
                "type": "review",
                "title": "New review received",
                "body": f"A customer rated their order {rating}/5",
                "data": {"order_id": order_id, "rating": rating},
                "is_read": False,
                "created_at": _now_iso(),
            }
            sb.table("notifications").insert(notif).execute()
        except Exception as e:
            print(f"rate_order: notification insert failed: {e}", file=sys.stderr)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rate order: {e}")

@router.post("/orders/{order_id}/refunds")
def request_refund(request: Request, order_id: str, payload: Dict[str, Any] = Body(default={})):  # type: ignore[no-redef]
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        ores = sb.table("orders").select("id, user_id, restaurant_id, items, total, status, payment_method, created_at, updated_at").eq("id", order_id).eq("user_id", user_id).limit(1).execute()
        rows = getattr(ores, "data", []) or []
        if not rows:
            raise HTTPException(status_code=404, detail="Order not found")
        order = rows[0]

        pm = str(order.get("payment_method") or "").lower()
        if pm != "wallet":
            raise HTTPException(status_code=400, detail="Only orders paid via wallet are refundable at this time")

        issue = (payload.get("issue") or payload.get("reason") or "").upper()
        desc = (payload.get("description") or "").strip()
        delay_mins = int(payload.get("delayMinutes") or payload.get("delay") or 0)
        evidence = payload.get("evidence") or []
        items_claim = payload.get("items") or []

        base_total = float(order.get("total", 0) or 0)
        db_status = (order.get("status") or "").upper()

        def is_delivered(s: str) -> bool:
            return s in {"DELIVERED", "COMPLETED", "RATING_PENDING"}

        approved_amount = 0.0
        refund_type = "partial"
        auto_approve = False

        if issue in {"NOT_DELIVERED", "ORDER_NOT_DELIVERED", "NO_DELIVERY"}:
            if not is_delivered(db_status):
                approved_amount = base_total
                refund_type = "full"
                auto_approve = True
        elif issue in {"LATE", "DELIVERED_LATE", "DELAY"}:
            if delay_mins >= 60:
                approved_amount = base_total
                refund_type = "full"
                auto_approve = True
            elif delay_mins >= 30:
                approved_amount = round(base_total * 0.3, 2)
                refund_type = "partial"
                auto_approve = True
            elif delay_mins >= 15:
                approved_amount = 0.0
                refund_type = "voucher"
                auto_approve = False
        elif issue in {"WRONG_ITEMS", "WRONG_ITEM", "MISSING_ITEMS", "MISSING_ITEM"}:
            try:
                items = order.get("items") or []
                names = set([str(x).strip().lower() for x in (items_claim or [])])
                amt = 0.0
                for it in items:
                    nm = (it.get("item_name") or it.get("name") or "").strip().lower()
                    if nm and (not names or nm in names):
                        price = float(it.get("price", 0) or 0)
                        qty = int(it.get("quantity", 1) or 1)
                        amt += price * qty
                amt = min(amt, base_total)
                if amt > 0:
                    approved_amount = round(amt, 2)
                    refund_type = "partial"
                    auto_approve = True
            except Exception:
                pass
        elif issue in {"QUALITY", "FOOD_QUALITY", "NOT_EDIBLE"}:
            has_evidence = bool(evidence)
            if has_evidence:
                approved_amount = round(base_total * 0.5, 2)
                refund_type = "partial"
                auto_approve = True
        elif issue in {"CANCELLED", "CANCELED"}:
            initiator = (payload.get("initiatedBy") or "").lower()
            if initiator in {"restaurant", "vendor", "rider", "delivery"}:
                approved_amount = base_total
                refund_type = "full"
                auto_approve = True
            elif initiator in {"customer", "user"}:
                if db_status in {"PENDING_CONFIRMATION", "CONFIRMED"}:
                    approved_amount = base_total
                    refund_type = "full"
                    auto_approve = True

        refund_status = "PENDING"
        processed_by = None
        credited = False

        if auto_approve and approved_amount > 0:
            try:
                wsel = sb.table("wallets").select("id, balance").eq("user_id", user_id).limit(1).execute()
                wrows = getattr(wsel, "data", []) or []
                wallet_id = None
                balance = 0.0
                if wrows:
                    wallet_id = wrows[0].get("id")
                    balance = float(wrows[0].get("balance", 0) or 0)
                else:
                    wrow = {"user_id": user_id, "balance": 0, "created_at": _now_iso(), "updated_at": _now_iso()}
                    sb.table("wallets").insert(wrow).execute()
                    w2 = sb.table("wallets").select("id, balance").eq("user_id", user_id).limit(1).execute()
                    wallet_id = (getattr(w2, "data", []) or [{}])[0].get("id")
                    balance = 0.0
                if not wallet_id:
                    raise HTTPException(status_code=500, detail="Wallet unavailable")
                tx = {
                    "id": str(uuid.uuid4()),
                    "wallet_id": wallet_id,
                    "type": "credit",
                    "amount": float(approved_amount),
                    "description": f"Refund: {issue or 'Order refund'}",
                    "payment_method": "refund",
                    "status": "completed",
                    "transaction_date": _now_iso(),
                    "user_id": user_id,
                    "transaction_reference": f"REFUND-{uuid.uuid4().hex[:10]}",
                    "order_id": order_id,
                }
                sb.table("transactions").insert(tx).execute()
                new_balance = balance + float(approved_amount)
                sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
                credited = True
                refund_status = "APPROVED"
                processed_by = "system"
            except Exception as e:
                print(f"refund auto-credit failed: {e}", file=sys.stderr)

        refund_row = {
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "user_id": user_id,
            "vendor_id": order.get("restaurant_id"),
            "reason": issue or None,
            "amount": float(approved_amount),
            "refund_type": refund_type,
            "status": refund_status,
            "evidence": evidence or None,
            "processed_by": processed_by,
            "description": desc or None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        try:
            sb.table("refunds").insert(refund_row).execute()
        except Exception:
            pass

        return {
            "success": True,
            "status": refund_status,
            "approved_amount": float(approved_amount),
            "method": "wallet" if credited else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to request refund: {e}")


@router.get("/orders/{order_id}/refunds")
def list_refunds(request: Request, order_id: str):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        try:
            res = sb.table("refunds").select("id, order_id, user_id, vendor_id, amount, refund_type, status, created_at, updated_at, reason, description").eq("order_id", order_id).eq("user_id", user_id).order("created_at", desc=True).execute()
            data = getattr(res, "data", []) or []
            if data:
                return {"refunds": data}
        except Exception:
            pass

        try:
            tres = sb.table("transactions").select("id, amount, payment_method, type, transaction_date, description, order_id").eq("type", "credit").eq("payment_method", "refund").eq("order_id", order_id).order("transaction_date", desc=True).execute()
            txs = getattr(tres, "data", []) or []
            refunds = [{
                "id": t.get("id"),
                "order_id": order_id,
                "amount": float(t.get("amount", 0) or 0),
                "status": "APPROVED",
                "refund_type": "partial",
                "created_at": t.get("transaction_date"),
                "reason": "refund",
                "description": t.get("description"),
            } for t in txs]
            return {"refunds": refunds}
        except Exception:
            return {"refunds": []}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch refunds: {e}")
