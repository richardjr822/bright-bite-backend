from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid
import os
from jose import jwt, JWTError
import time
import urllib.parse
import hmac
import hashlib
import json

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


def _hmac_hex(secret: str, data: bytes) -> str:
	try:
		return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
	except Exception:
		return ""


def _verify_signature(secret: str, raw_body: bytes, provided: Optional[str]) -> bool:
	"""Verify HMAC SHA256 signature in hex. Accepts plain hex or values prefixed with 'sha256='."""
	if not secret:
		return False
	if not provided:
		return False
	provided = provided.strip()
	if provided.lower().startswith("sha256="):
		provided = provided.split("=", 1)[1]
	expected = _hmac_hex(secret, raw_body)
	try:
		return hmac.compare_digest(expected, provided)
	except Exception:
		return expected == provided


def _complete_pending_credit(sb, wallet_id: str, tx: Dict[str, Any]) -> Dict[str, Any]:
	"""Mark a credit transaction as completed only if currently pending, then credit wallet once.
	Returns the latest wallet dict and transaction status.
	"""
	# Attempt to flip status from pending->completed atomically by filtering on status
	try:
		upd = sb.table("transactions").update({"status": "completed"}) \
			.eq("id", tx.get("id")).eq("wallet_id", wallet_id).eq("status", "pending").execute()
		updated_rows = getattr(upd, "data", []) or []
	except Exception:
		updated_rows = []

	if not updated_rows:
		# Already processed (completed or failed) or not found; fetch and return current wallet without crediting again
		try:
			cur = _client().table("wallets").select("*").eq("id", wallet_id).limit(1).execute()
			wrows = getattr(cur, "data", []) or []
			wallet = wrows[0] if wrows else {"id": wallet_id, "balance": 0}
		except Exception:
			wallet = {"id": wallet_id, "balance": 0}
		return {"wallet": wallet, "status": tx.get("status") or "completed"}

	# Only the first successful updater gets here; now credit wallet
	amount = float(tx.get("amount", 0) or 0)
	if amount <= 0:
		raise HTTPException(status_code=400, detail="Invalid transaction amount")

	try:
		wsel = sb.table("wallets").select("*").eq("id", wallet_id).limit(1).execute()
		wrows = getattr(wsel, "data", []) or []
		wallet = wrows[0] if wrows else {"id": wallet_id, "balance": 0}
		current_balance = float(wallet.get("balance", 0) or 0)
		new_balance = current_balance + amount
		sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
		wallet["balance"] = new_balance
	except Exception:
		# If wallet update fails, best-effort revert transaction back to pending
		try:
			sb.table("transactions").update({"status": "pending"}).eq("id", tx.get("id")).execute()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail="Failed to update wallet balance")

	return {"wallet": wallet, "status": "completed"}


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

	# Mark transaction completed if still pending, then credit wallet exactly once
	result = _complete_pending_credit(sb, wallet_id, tx)
	wallet = result.get("wallet", {"id": wallet_id, "balance": float(wallet.get("balance", 0) or 0)})
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


# ---------------- Webhooks (Production) ----------------


@router.post("/webhook/maya")
async def maya_webhook(request: Request):
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")

	raw = await request.body()
	sig = request.headers.get("X-Signature") or request.headers.get("X-Webhook-Signature") or request.headers.get("X-PayMaya-Signature")
	secret = os.getenv("MAYA_WEBHOOK_SECRET", "")
	if not _verify_signature(secret, raw, sig):
		raise HTTPException(status_code=401, detail="Invalid signature")

	try:
		payload = json.loads(raw.decode("utf-8") or "{}")
	except Exception:
		payload = {}

	# Extract reference and status (supports multiple possible payload shapes)
	reference = (
		payload.get("reference")
		or payload.get("requestReferenceNumber")
		or payload.get("transactionReference")
		or payload.get("id")
	)
	status = (payload.get("status") or payload.get("paymentStatus") or "").lower()
	paid_statuses = {"paid", "success", "succeeded", "completed"}
	failed_statuses = {"failed", "cancelled", "canceled", "expired"}

	if not reference:
		raise HTTPException(status_code=400, detail="Missing reference")

	# Lookup transaction by our stored reference
	try:
		q = sb.table("transactions").select("*").eq("transaction_reference", reference).limit(1)
		res = q.execute()
		rows = getattr(res, "data", []) or []
	except Exception:
		rows = []
	if not rows:
		raise HTTPException(status_code=404, detail="Transaction not found")
	tx = rows[0]
	wallet_id = tx.get("wallet_id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")

	# Optional: validate amount matches
	try:
		event_amount = float((payload.get("amount") or {}).get("value") if isinstance(payload.get("amount"), dict) else payload.get("amount") or 0)
	except Exception:
		event_amount = None
	if event_amount is not None:
		try:
			tx_amount = float(tx.get("amount", 0) or 0)
			if abs(tx_amount - event_amount) > 0.009:
				raise HTTPException(status_code=400, detail="Amount mismatch")
		except Exception:
			pass

	if status in paid_statuses:
		result = _complete_pending_credit(sb, wallet_id, tx)
		wallet = result.get("wallet", {"id": wallet_id, "balance": 0})
		return {"success": True, "reference": reference, "status": "completed", "balance": float(wallet.get("balance", 0) or 0)}
	elif status in failed_statuses:
		try:
			sb.table("transactions").update({"status": "failed"}).eq("id", tx.get("id")).eq("status", "pending").execute()
		except Exception:
			pass
		return {"success": True, "reference": reference, "status": "failed"}

	# Unknown status: accept but do nothing
	return {"success": True, "reference": reference, "status": status or "unknown"}


@router.post("/webhook/gcash")
async def gcash_webhook(request: Request):
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")

	raw = await request.body()
	sig = request.headers.get("X-Signature") or request.headers.get("X-Webhook-Signature") or request.headers.get("X-Gcash-Signature")
	secret = os.getenv("GCASH_WEBHOOK_SECRET", "")
	if not _verify_signature(secret, raw, sig):
		raise HTTPException(status_code=401, detail="Invalid signature")

	try:
		payload = json.loads(raw.decode("utf-8") or "{}")
	except Exception:
		payload = {}

	# Extract reference and status; support common aggregator formats
	reference = (
		payload.get("reference")
		or payload.get("bizNo")
		or payload.get("transactionReference")
		or payload.get("id")
	)
	status = (payload.get("status") or payload.get("paymentStatus") or "").lower()
	paid_statuses = {"paid", "success", "succeeded", "completed"}
	failed_statuses = {"failed", "cancelled", "canceled", "expired"}

	if not reference:
		raise HTTPException(status_code=400, detail="Missing reference")

	try:
		q = sb.table("transactions").select("*").eq("transaction_reference", reference).limit(1)
		res = q.execute()
		rows = getattr(res, "data", []) or []
	except Exception:
		rows = []
	if not rows:
		raise HTTPException(status_code=404, detail="Transaction not found")
	tx = rows[0]
	wallet_id = tx.get("wallet_id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")

	# Optional: amount consistency check
	try:
		event_amount = float(payload.get("orderAmount") or payload.get("amount") or 0)
	except Exception:
		event_amount = None
	if event_amount is not None:
		try:
			tx_amount = float(tx.get("amount", 0) or 0)
			if abs(tx_amount - event_amount) > 0.009:
				raise HTTPException(status_code=400, detail="Amount mismatch")
		except Exception:
			pass

	if status in paid_statuses:
		result = _complete_pending_credit(sb, wallet_id, tx)
		wallet = result.get("wallet", {"id": wallet_id, "balance": 0})
		return {"success": True, "reference": reference, "status": "completed", "balance": float(wallet.get("balance", 0) or 0)}
	elif status in failed_statuses:
		try:
			sb.table("transactions").update({"status": "failed"}).eq("id", tx.get("id")).eq("status", "pending").execute()
		except Exception:
			pass
		return {"success": True, "reference": reference, "status": "failed"}

	return {"success": True, "reference": reference, "status": status or "unknown"}


# ---------------- Sandbox/Test Mode ----------------
# Enable sandbox mode for testing (set via environment variable)
SANDBOX_MODE = os.getenv("WALLET_SANDBOX_MODE", "true").lower() in ("true", "1", "yes")
SANDBOX_PIN = os.getenv("WALLET_SANDBOX_PIN", "1234")  # Simple PIN for sandbox confirmations


@router.post("/sandbox/top-up")
def sandbox_top_up(request: Request, payload: Dict[str, Any] = Body(default={})):
	"""
	Sandbox top-up endpoint for testing. Instantly credits wallet without real payment.
	Only works when WALLET_SANDBOX_MODE=true (default for development).
	Requires a PIN for security even in test mode.
	"""
	if not SANDBOX_MODE:
		raise HTTPException(status_code=403, detail="Sandbox mode is disabled in production")
	
	user_id = _get_user_id(request, payload)
	if not user_id:
		raise HTTPException(status_code=401, detail="Unauthorized")
	
	sb = _client()
	if not sb:
		raise HTTPException(status_code=500, detail="Database client unavailable")
	
	# Verify sandbox PIN
	provided_pin = str(payload.get("pin", "")).strip()
	if provided_pin != SANDBOX_PIN:
		raise HTTPException(status_code=403, detail="Invalid sandbox PIN")
	
	amount_raw = payload.get("amount")
	try:
		amount = float(amount_raw)
	except Exception:
		raise HTTPException(status_code=400, detail="Invalid amount")
	
	if amount < 1 or amount > 100000:
		raise HTTPException(status_code=400, detail="Amount must be between 1 and 100,000 (sandbox)")
	
	description = payload.get("description") or "Sandbox top-up (test)"
	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")
	
	# Create completed transaction directly
	tx_id = str(uuid.uuid4())
	reference = f"SANDBOX-{uuid.uuid4().hex[:10]}"
	tx_row = {
		"id": tx_id,
		"wallet_id": wallet_id,
		"type": "credit",
		"amount": amount,
		"description": description,
		"payment_method": "sandbox",
		"status": "completed",
		"transaction_date": _now_iso(),
		"user_id": user_id,
		"transaction_reference": reference,
	}
	
	try:
		sb.table("transactions").insert(tx_row).execute()
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Failed to create transaction: {str(e)}")
	
	# Credit wallet immediately
	current_balance = float(wallet.get("balance", 0) or 0)
	new_balance = current_balance + amount
	
	try:
		sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
	except Exception:
		# Rollback transaction
		try:
			sb.table("transactions").update({"status": "failed"}).eq("id", tx_id).execute()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail="Failed to update wallet balance")
	
	return {
		"success": True,
		"message": "Sandbox top-up completed successfully",
		"wallet": {"id": wallet_id, "balance": new_balance},
		"transaction": {
			"id": tx_id,
			"type": "credit",
			"amount": amount,
			"description": description,
			"payment_method": "sandbox",
			"status": "completed",
			"reference": reference,
			"date": _now_iso()[:10]
		}
	}


# ---------------- Debit/Spending ----------------


@router.post("/debit")
def debit_wallet(request: Request, payload: Dict[str, Any] = Body(default={})):
	"""
	Debit/spend from wallet for order payments.
	Validates sufficient balance before deducting.
	"""
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
	
	if amount <= 0:
		raise HTTPException(status_code=400, detail="Amount must be positive")
	
	if amount > 50000:
		raise HTTPException(status_code=400, detail="Single transaction limit is 50,000")
	
	description = payload.get("description") or "Wallet payment"
	order_id = payload.get("order_id")
	
	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")
	
	current_balance = float(wallet.get("balance", 0) or 0)
	
	# Check sufficient balance
	if current_balance < amount:
		raise HTTPException(
			status_code=400, 
			detail=f"Insufficient balance. Available: ₱{current_balance:.2f}, Required: ₱{amount:.2f}"
		)
	
	# Create debit transaction
	tx_id = str(uuid.uuid4())
	reference = f"DEBIT-{uuid.uuid4().hex[:10]}"
	tx_row = {
		"id": tx_id,
		"wallet_id": wallet_id,
		"type": "debit",
		"amount": amount,
		"description": description,
		"payment_method": "wallet",
		"status": "completed",
		"transaction_date": _now_iso(),
		"user_id": user_id,
		"transaction_reference": reference,
	}
	
	if order_id:
		tx_row["order_id"] = order_id
	
	try:
		sb.table("transactions").insert(tx_row).execute()
	except Exception as e:
		msg = str(e)
		# If the DB doesn't have an order_id column, retry without it
		if ("order_id" in msg) and ("schema cache" in msg or "column" in msg or "PGRST204" in msg):
			try:
				tx_row_fallback = {k: v for k, v in tx_row.items() if k != "order_id"}
				sb.table("transactions").insert(tx_row_fallback).execute()
			except Exception as e2:
				raise HTTPException(status_code=500, detail=f"Failed to create transaction: {str(e2)}")
		else:
			raise HTTPException(status_code=500, detail=f"Failed to create transaction: {str(e)}")
	
	# Deduct from wallet
	new_balance = current_balance - amount
	
	try:
		sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
	except Exception:
		# Rollback transaction
		try:
			sb.table("transactions").update({"status": "failed"}).eq("id", tx_id).execute()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail="Failed to update wallet balance")
	
	return {
		"success": True,
		"message": "Payment successful",
		"wallet": {"id": wallet_id, "balance": new_balance},
		"transaction": {
			"id": tx_id,
			"type": "debit",
			"amount": amount,
			"description": description,
			"status": "completed",
			"reference": reference,
			"date": _now_iso()[:10]
		}
	}


@router.post("/refund")
def refund_wallet(request: Request, payload: Dict[str, Any] = Body(default={})):
	"""
	Refund to wallet (for cancelled orders, etc).
	"""
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
	
	if amount <= 0:
		raise HTTPException(status_code=400, detail="Amount must be positive")
	
	original_reference = payload.get("original_reference")
	order_id = payload.get("order_id")
	reason = payload.get("reason") or "Order refund"
	
	wallet = _ensure_wallet(user_id)
	wallet_id = wallet.get("id")
	if not wallet_id:
		raise HTTPException(status_code=500, detail="Wallet unavailable")
	
	# Create refund transaction
	tx_id = str(uuid.uuid4())
	reference = f"REFUND-{uuid.uuid4().hex[:10]}"
	description = f"Refund: {reason}"
	if original_reference:
		description += f" (ref: {original_reference})"
	
	tx_row = {
		"id": tx_id,
		"wallet_id": wallet_id,
		"type": "credit",
		"amount": amount,
		"description": description,
		"payment_method": "refund",
		"status": "completed",
		"transaction_date": _now_iso(),
		"user_id": user_id,
		"transaction_reference": reference,
	}
	
	if order_id:
		tx_row["order_id"] = order_id
	
	try:
		sb.table("transactions").insert(tx_row).execute()
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Failed to create transaction: {str(e)}")
	
	# Credit wallet
	current_balance = float(wallet.get("balance", 0) or 0)
	new_balance = current_balance + amount
	
	try:
		sb.table("wallets").update({"balance": new_balance, "updated_at": _now_iso()}).eq("id", wallet_id).execute()
	except Exception:
		try:
			sb.table("transactions").update({"status": "failed"}).eq("id", tx_id).execute()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail="Failed to update wallet balance")
	
	return {
		"success": True,
		"message": "Refund processed successfully",
		"wallet": {"id": wallet_id, "balance": new_balance},
		"transaction": {
			"id": tx_id,
			"type": "credit",
			"amount": amount,
			"description": description,
			"status": "completed",
			"reference": reference,
			"date": _now_iso()[:10]
		}
	}


# ---------------- Sandbox Info Endpoint ----------------


@router.get("/sandbox/status")
def sandbox_status():
	"""Check if sandbox mode is enabled."""
	return {
		"sandbox_mode": SANDBOX_MODE,
		"message": "Sandbox mode allows instant test top-ups" if SANDBOX_MODE else "Production mode - real payments only"
	}
