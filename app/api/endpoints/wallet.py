from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
import os
from jose import jwt, JWTError
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    from app.db.database import supabase
except Exception:
    supabase = None

router = APIRouter(prefix="/wallet", tags=["wallet"])

# ---------- Helpers ----------

def _client():
    return supabase

def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _iso_minus(seconds: int) -> str:
    return (datetime.utcnow() - timedelta(seconds=seconds)).isoformat()

# JWT settings (must match auth.py)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"

def _get_user_id(req: Request, payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    # Prefer JWT Authorization header
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
    # Fallback to header or body (for backward compatibility)
    if req.headers.get("x-user-id"):
        return req.headers.get("x-user-id")
    if payload and payload.get("userId"):
        return str(payload.get("userId"))
    return None

# Fetch or create wallet row
def _ensure_wallet(user_id: str) -> Dict[str, Any]:
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    try:
        # Fetch all rows for user_id and pick the earliest (dedupe safety)
        res = sb.table("wallets").select("*").eq("user_id", user_id).order("created_at", desc=False).execute()
        rows = getattr(res, "data", []) or []
        if rows:
            return rows[0]
    except Exception:
        # continue to attempt creation
        pass
    # Create new wallet row; if insert races, refetch
    row = {"user_id": user_id, "balance": 0}
    try:
        sb.table("wallets").insert(row).execute()
    except Exception:
        # ignore and refetch below
        pass
    try:
        res2 = sb.table("wallets").select("*").eq("user_id", user_id).order("created_at", desc=False).limit(1).execute()
        rows2 = getattr(res2, "data", []) or []
        if rows2:
            return rows2[0]
    except Exception:
        pass
    # Last resort minimal wallet object (no id)
    return row

# ---------- Routes ----------

@router.get("")
def get_wallet(request: Request):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    wallet = _ensure_wallet(user_id)
    return {"success": True, "wallet": {"id": wallet.get("id"), "balance": float(wallet.get("balance", 0))}}

@router.get("/transactions")
def list_transactions(request: Request, limit: int = 50):
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")
    wallet = _ensure_wallet(user_id)
    wallet_id = wallet.get("id")
    # If wallet doesn't have an id yet, return empty list gracefully
    if not wallet_id:
        return {"success": True, "transactions": []}

    # Clamp limit to a safe range
    try:
        limit_val = int(limit)
    except Exception:
        limit_val = 50
    limit_val = max(1, min(limit_val, 200))

    # Try ordering by transaction_date, fall back to created_at if needed
    try:
        q = (
            sb.table("transactions")
            .select("*")
            .eq("wallet_id", wallet_id)
            .order("transaction_date", desc=True)
            .limit(limit_val)
        )
        res = q.execute()
    except Exception:
        try:
            q = (
                sb.table("transactions")
                .select("*")
                .eq("wallet_id", wallet_id)
                .order("created_at", desc=True)
                .limit(limit_val)
            )
            res = q.execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list transactions: {e}")

    rows = getattr(res, "data", []) or []
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "type": r.get("type"),
            "amount": float(r.get("amount", 0) or 0),
            "description": r.get("description"),
            "payment_method": r.get("payment_method"),
            "status": r.get("status"),
            "date": (r.get("transaction_date") or r.get("created_at") or _now_iso())[:10]
        })
    return {"success": True, "transactions": out}

class TopUpPayload(Dict[str, Any]):
    pass

@router.post("/top-up")
def top_up(request: Request, payload: Dict[str, Any] = Body(default={})):
    user_id = _get_user_id(request, payload)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    amount_raw = payload.get("amount")
    payment_method = (payload.get("payment_method") or payload.get("paymentMethod") or "gcash").lower()
    allowed_methods = {"gcash", "bank", "card"}
    if payment_method not in allowed_methods:
        raise HTTPException(status_code=400, detail="Unsupported payment method")
    description = payload.get("description") or f"Top-up via {payment_method.title()}"
    try:
        amount_dec = Decimal(str(amount_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=400, detail="Invalid amount")
    if amount_dec < Decimal("50") or amount_dec > Decimal("10000"):
        raise HTTPException(status_code=400, detail="Amount must be between 50 and 10000")
    amount = float(amount_dec)

    sb = _client()
    if not sb:
        raise HTTPException(status_code=500, detail="Database client unavailable")

    wallet = _ensure_wallet(user_id)
    wallet_id = wallet.get("id")

    # Idempotency key support (optional)
    idem_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or payload.get("idempotency_key") or payload.get("client_tx_id")
    if idem_key:
        try:
            existing = sb.table("transactions").select("*").eq("id", idem_key).eq("wallet_id", wallet_id).limit(1).execute()
            rows = getattr(existing, "data", []) or []
            if rows:
                # Return current wallet and existing transaction without duplicating
                return {
                    "success": True,
                    "wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
                    "transaction": {
                        "id": rows[0].get("id"),
                        "type": rows[0].get("type"),
                        "amount": float(rows[0].get("amount", 0) or 0),
                        "description": rows[0].get("description"),
                        "payment_method": rows[0].get("payment_method"),
                        "status": rows[0].get("status"),
                        "date": (rows[0].get("transaction_date") or _now_iso())[:10]
                    }
                }
        except Exception:
            pass

    # Short-window duplicate guard (double-click protection)
    try:
        window_start = _iso_minus(5)
        dup_q = (
            sb.table("transactions")
            .select("*")
            .eq("wallet_id", wallet_id)
            .eq("type", "credit")
            .eq("description", description)
            .eq("status", "completed")
            .eq("amount", float(amount_dec))
            .gte("transaction_date", window_start)
            .order("transaction_date", desc=True)
            .limit(1)
        )
        dup_res = dup_q.execute()
        dup_rows = getattr(dup_res, "data", []) or []
        if dup_rows:
            return {
                "success": True,
                "wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
                "transaction": {
                    "id": dup_rows[0].get("id"),
                    "type": dup_rows[0].get("type"),
                    "amount": float(dup_rows[0].get("amount", 0) or 0),
                    "description": dup_rows[0].get("description"),
                    "payment_method": dup_rows[0].get("payment_method"),
                    "status": dup_rows[0].get("status"),
                    "date": (dup_rows[0].get("transaction_date") or _now_iso())[:10]
                }
            }
    except Exception:
        pass
    # Insert transaction
    tx_row = {
        "wallet_id": wallet_id,
        "type": "credit",
        "amount": amount,
        "description": description,
        "payment_method": payment_method,
        "status": "completed",
        "transaction_date": _now_iso(),
    }
    try:
        # Ensure a UUID exists even if DB doesn't return inserted row
        if idem_key:
            tx_row["id"] = str(idem_key)
        elif "id" not in tx_row or not tx_row.get("id"):
            tx_row["id"] = str(uuid.uuid4())
        ins = sb.table("transactions").insert(tx_row).execute()
        tx_created = (getattr(ins, "data", []) or [tx_row])[0]
        # Only after successful insert, update wallet balance atomically-ish
        current_balance = Decimal(str(wallet.get("balance", 0) or 0))
        new_balance_dec = (current_balance + amount_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        new_balance = float(new_balance_dec)
        try:
            sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
        except Exception:
            pass
    except Exception:
        # If idempotency key insert raced, fetch existing
        if idem_key:
            try:
                existing = sb.table("transactions").select("*").eq("id", idem_key).limit(1).execute()
                rows = getattr(existing, "data", []) or []
                if rows:
                    tx_created = rows[0]
                    # Duplicate insert; DO NOT update balance again
                    new_balance = float(wallet.get("balance", 0) or 0)
                else:
                    tx_created = tx_row
                    # Unknown failure; do not change balance
                    new_balance = float(wallet.get("balance", 0) or 0)
            except Exception:
                tx_created = tx_row
                new_balance = float(wallet.get("balance", 0) or 0)
        else:
            tx_created = tx_row
            new_balance = float(wallet.get("balance", 0) or 0)

    return {
        "success": True,
        "wallet": {"id": wallet_id, "balance": new_balance},
        "transaction": {
            "id": tx_created.get("id"),
            "type": tx_created.get("type"),
            "amount": float(tx_created.get("amount", 0) or 0),
            "description": tx_created.get("description"),
            "payment_method": tx_created.get("payment_method"),
            "status": tx_created.get("status"),
            "date": (tx_created.get("transaction_date") or _now_iso())[:10]
        }
    }
