from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid
import os
from jose import jwt, JWTError
import time
import urllib.parse

try:
	from app.db.database import supabase
except Exception:
	supabase = None

router = APIRouter(prefix="/wallet", tags=["wallet"])


# ---------------- Helpers ----------------

def _client():
	return supabase


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _iso_minus(seconds: int) -> str:
	return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"


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


def _ensure_wallet(user_id: str) -> Dict[str, Any]:
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")
	try:
		res = sb.table("wallets").select("*").eq("user_id", user_id).order("created_at", desc=False).limit(1).execute()
		rows = getattr(res, "data", []) or []
		if rows:
			return rows[0]
	except Exception:
		pass
	# create wallet
	row = {"user_id": user_id, "balance": 0, "created_at": _now_iso(), "updated_at": _now_iso()}
	try:
		_client().table("wallets").insert(row).execute()
	except Exception:
		pass
	try:
		res2 = _client().table("wallets").select("*").eq("user_id", user_id).order("created_at", desc=False).limit(1).execute()
		rows2 = getattr(res2, "data", []) or []
		if rows2:
			return rows2[0]
	except Exception:
		pass
	return row


# ---------------- Routes ----------------


@router.get("")
def get_wallet(request: Request):
	user_id = _get_user_id(request)
	if not user_id:
		raise HTTPException(status_code=401, detail="Unauthorized")
	wallet = _ensure_wallet(user_id)
	return {"success": True, "wallet": {"id": wallet.get("id"), "balance": float(wallet.get("balance", 0) or 0)}}


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
	if not wallet_id:
		return {"success": True, "transactions": []}
	try:
		res = sb.table("transactions").select("*").eq("wallet_id", wallet_id).order("transaction_date", desc=True).limit(max(1, min(int(limit or 50), 200))).execute()
	except Exception:
		res = sb.table("transactions").select("*").eq("wallet_id", wallet_id).order("created_at", desc=True).limit(max(1, min(int(limit or 50), 200))).execute()
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


@router.post("/top-up")
def top_up(request: Request, payload: Dict[str, Any] = Body(default={})):  # type: ignore[no-redef]
	user_id = _get_user_id(request, payload)
	if not user_id:
		raise HTTPException(status_code=401, detail="Unauthorized")
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")

	amount_raw = payload.get("amount")
	try:
		amount = float(amount_raw)
	except Exception:
		raise HTTPException(status_code=400, detail="Invalid amount")
	if amount < 50 or amount > 10000:
		raise HTTPException(status_code=400, detail="Amount must be between 50 and 10000")

	payment_method = (payload.get("payment_method") or payload.get("paymentMethod") or "").lower()
	allowed_methods = {"gcash", "maya"}
	if payment_method not in allowed_methods:
		raise HTTPException(status_code=400, detail="Unsupported payment method. Use gcash or maya.")

	description = payload.get("description") or f"Top-up via {payment_method.upper()}"
	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")

	# Idempotency by Idempotency-Key header or payload key
	idem_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or payload.get("idempotency_key")
	if idem_key:
		try:
			existing = sb.table("transactions").select("*").eq("id", idem_key).eq("wallet_id", wallet_id).limit(1).execute()
			rows = getattr(existing, "data", []) or []
			if rows:
				# Return existing and skip creating a new one
				tx = rows[0]
				return {
					"success": True,
					"wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
					"transaction": {
						"id": tx.get("id"),
						"type": tx.get("type"),
						"amount": float(tx.get("amount", 0) or 0),
						"description": tx.get("description"),
						"payment_method": tx.get("payment_method"),
						"status": tx.get("status"),
						"date": (tx.get("transaction_date") or _now_iso())[:10]
					}
				}
		except Exception:
			pass

	# Create a pending transaction and return a redirect intent
	tx_id = idem_key or str(uuid.uuid4())
	reference = f"{payment_method.upper()}-{uuid.uuid4().hex[:10]}"
	tx_row = {
		"id": tx_id,
		"wallet_id": wallet_id,
		"type": "credit",
		"amount": amount,
		"description": description,
		"payment_method": payment_method,
		"status": "pending",
		"transaction_date": _now_iso(),
		"user_id": user_id,
		"transaction_reference": reference,
	}
	try:
		sb.table("transactions").insert(tx_row).execute()
	except Exception:
		# try fetch if id duplicate
		pass

	# Construct provider cashier URL (web) to handle redirect
	provider = payment_method
	if payment_method == "gcash":
		merchant_id = os.getenv("GCASH_MERCHANT_ID", "217020000072124123646")
		merchant_name = os.getenv("GCASH_MERCHANT_NAME", "BrightBite")
		pd_code = os.getenv("GCASH_PD_CODE", "51051000101000100001")
		timestamp = str(int(time.time() * 1000))
		order_amount = f"{amount:.2f}"
		# NOTE: 'sign' should be generated using GCASH algorithm; placeholder for demo
		sign = urllib.parse.quote_plus(reference)
		qrcode = f"{reference},{merchant_id}"
		params = {
			"bizNo": reference,
			"timestamp": timestamp,
			"sign": sign,
			"orderAmount": order_amount,
			"pdCode": pd_code,
			"merchantid": merchant_id,
			"queryInterval": "10000",
			"qrcode": qrcode,
			"merchantName": merchant_name,
			"expiryTime": "600",
		}
		base = "https://payments.gcash.com/gcash-cashier-web/1.2.1/index.html#/confirm"
		redirect_url = base + "?" + urllib.parse.urlencode(params)
		fallback_url = redirect_url
	else:  # maya
		maya_id = str(uuid.uuid4())
		redirect_url = f"https://payments.maya.ph/paymaya/payment?id={maya_id}"
		fallback_url = redirect_url

	return {
		"success": True,
		"wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
		"transaction": {
			"id": tx_row.get("id"),
			"type": tx_row.get("type"),
			"amount": float(tx_row.get("amount", 0) or 0),
			"description": tx_row.get("description"),
			"payment_method": tx_row.get("payment_method"),
			"status": tx_row.get("status"),
			"date": (tx_row.get("transaction_date") or _now_iso())[:10]
		},
		"gateway": {
			"provider": provider,
			"redirect_url": redirect_url,
			"fallback_url": fallback_url,
			"reference": reference,
		}
	}


@router.post("/confirm")
def confirm_top_up(request: Request, payload: Dict[str, Any] = Body(default={})):
	user_id = _get_user_id(request, payload)
	if not user_id:
		raise HTTPException(status_code=401, detail="Unauthorized")
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")

	tx_id = (payload.get("transaction_id") or payload.get("id") or "").strip()
	reference = (payload.get("reference") or payload.get("transaction_reference") or "").strip()
	if not tx_id and not reference:
		raise HTTPException(status_code=400, detail="Provide transaction_id or reference")

	# Get user's wallet
	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")

	# Fetch transaction
	try:
		q = sb.table("transactions").select("*")
		if tx_id:
			q = q.eq("id", tx_id)
		if reference:
			q = q.eq("transaction_reference", reference)
		q = q.eq("wallet_id", wallet_id).limit(1)
		res = q.execute()
		rows = getattr(res, "data", []) or []
	except Exception:
		rows = []

	if not rows:
		raise HTTPException(status_code=404, detail="Transaction not found")

	tx = rows[0]
	if (tx.get("user_id") and str(tx.get("user_id")) != str(user_id)):
		raise HTTPException(status_code=403, detail="Forbidden")

	if tx.get("type") != "credit":
		raise HTTPException(status_code=400, detail="Only credit transactions can be confirmed")

	status = (tx.get("status") or "").lower()
	if status == "completed":
		# Idempotent: already completed; return current wallet
		return {
			"success": True,
			"wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
			"transaction": {
				"id": tx.get("id"),
				"status": tx.get("status"),
				"payment_method": tx.get("payment_method"),
				"amount": float(tx.get("amount", 0) or 0),
			},
		}

	if status != "pending":
		raise HTTPException(status_code=400, detail="Only pending transactions can be confirmed")

	amount = float(tx.get("amount", 0) or 0)
	if amount <= 0:
		raise HTTPException(status_code=400, detail="Invalid transaction amount")

	# Mark transaction completed
	try:
		sb.table("transactions").update({"status": "completed"}).eq("id", tx.get("id")).execute()
	except Exception:
		pass

	# Credit wallet balance (do not credit when pending; only now on completion)
	try:
		current_balance = float(wallet.get("balance", 0) or 0)
		new_balance = current_balance + amount
		sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
		wallet["balance"] = new_balance
	except Exception:
		# If wallet update failed, try to revert transaction status to pending
		try:
			sb.table("transactions").update({"status": "pending"}).eq("id", tx.get("id")).execute()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail="Failed to update wallet balance")

	return {
		"success": True,
		"wallet": {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)},
		"transaction": {
			"id": tx.get("id"),
			"status": "completed",
			"payment_method": tx.get("payment_method"),
			"amount": amount,
		},
	}


@router.get("/status")
def get_transaction_status(request: Request, id: Optional[str] = None, reference: Optional[str] = None):
	user_id = _get_user_id(request)
	if not user_id:
		raise HTTPException(status_code=401, detail="Unauthorized")
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")

	if not id and not reference:
		raise HTTPException(status_code=400, detail="Provide id or reference")

	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")

	try:
		q = sb.table("transactions").select("*").eq("wallet_id", wallet_id)
		if id:
			q = q.eq("id", id)
		if reference:
			q = q.eq("transaction_reference", reference)
		q = q.limit(1)
		res = q.execute()
		rows = getattr(res, "data", []) or []
	except Exception:
		rows = []

	if not rows:
		raise HTTPException(status_code=404, detail="Transaction not found")

	tx = rows[0]
	return {
		"success": True,
		"transaction": {
			"id": tx.get("id"),
			"reference": tx.get("transaction_reference"),
			"status": tx.get("status"),
			"type": tx.get("type"),
			"amount": float(tx.get("amount", 0) or 0),
			"payment_method": tx.get("payment_method"),
		}
	}

