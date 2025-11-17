import json
import asyncio
from typing import Dict, Set, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_vendor_connections: Dict[str, Set[WebSocket]] = {}
_student_connections: Dict[str, Set[WebSocket]] = {}
_staff_connections: Dict[str, Set[WebSocket]] = {}

# Mapping copied to avoid circular import
DB_TO_UI_STATUS = {
    "PENDING_CONFIRMATION": "pending",
    "CONFIRMED": "pending",
    "PAYMENT_PROCESSING": "pending",
    "PREPARING": "preparing",
    "READY_FOR_PICKUP": "ready",
    "COMPLETED": "completed",
    "RATING_PENDING": "completed",
    "REJECTED": "cancelled",
    "DELIVERED": "completed",
    "ON_THE_WAY": "preparing",  # if added later, map sensibly
    "ARRIVING_SOON": "preparing",
}

async def broadcast_order_event(event: Dict[str, Any]):
    """Broadcast an order-related event.
    Expected keys: type, order_id, db_status(optional), ui_status(optional), vendor_id, user_id, order(optional snapshot)
    """
    db_status = event.get("db_status") or event.get("status")
    ui_status = event.get("ui_status") or (DB_TO_UI_STATUS.get(db_status) if db_status else None)
    event_payload = {
        "type": event.get("type"),
        "order_id": event.get("order_id"),
        "db_status": db_status,
        "ui_status": ui_status,
        "vendor_id": event.get("vendor_id") or event.get("restaurant_id"),
        "user_id": event.get("user_id"),
        "staff_user_id": event.get("staff_user_id"),
        "reward_points": event.get("reward_points"),
    }
    if event.get("order"):
        event_payload["order"] = event.get("order")
    data = json.dumps(event_payload)

    vendor_id = event_payload.get("vendor_id")
    if vendor_id and vendor_id in _vendor_connections:
        send_tasks = []
        for ws in list(_vendor_connections[vendor_id]):
            send_tasks.append(_safe_send(ws, data, _vendor_connections[vendor_id], vendor_id))
        await asyncio.gather(*send_tasks, return_exceptions=True)

    user_id = event_payload.get("user_id")
    if user_id and user_id in _student_connections:
        send_tasks = []
        for ws in list(_student_connections[user_id]):
            send_tasks.append(_safe_send(ws, data, _student_connections[user_id], user_id))
        await asyncio.gather(*send_tasks, return_exceptions=True)

    staff_user_id = event_payload.get("staff_user_id")
    if staff_user_id and staff_user_id in _staff_connections:
        send_tasks = []
        for ws in list(_staff_connections[staff_user_id]):
            send_tasks.append(_safe_send(ws, data, _staff_connections[staff_user_id], staff_user_id))
        await asyncio.gather(*send_tasks, return_exceptions=True)

async def _safe_send(ws: WebSocket, data: str, pool: Set[WebSocket], key: str):
    try:
        await ws.send_text(data)
    except Exception:
        pool.discard(ws)
        if not pool:
            if key in _vendor_connections:
                _vendor_connections.pop(key, None)
            if key in _student_connections:
                _student_connections.pop(key, None)

@router.websocket("/ws/orders")
async def orders_ws(websocket: WebSocket):
    """Bi-directional websocket for order events.
    Query params: vendorId or userId. Sends periodic pings.
    """
    await websocket.accept()
    vendor_id = websocket.query_params.get("vendorId")
    user_id = websocket.query_params.get("userId")
    staff_user_id = websocket.query_params.get("staffUserId")
    if not vendor_id and not user_id:
        await websocket.close(code=1008)
        return
    if vendor_id:
        _vendor_connections.setdefault(vendor_id, set()).add(websocket)
    if user_id:
        _student_connections.setdefault(user_id, set()).add(websocket)
    if staff_user_id:
        _staff_connections.setdefault(staff_user_id, set()).add(websocket)

    async def _ping_loop():
        while True:
            try:
                await asyncio.sleep(25)
                await websocket.send_text(json.dumps({"type": "ping"}))
            except asyncio.CancelledError:
                break
            except Exception:
                break

    ping_task = asyncio.create_task(_ping_loop())
    try:
        while True:
            # Accept any message to keep connection active
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)
    finally:
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        if vendor_id and vendor_id in _vendor_connections:
            _vendor_connections[vendor_id].discard(websocket)
            if not _vendor_connections[vendor_id]:
                _vendor_connections.pop(vendor_id, None)
        if user_id and user_id in _student_connections:
            _student_connections[user_id].discard(websocket)
            if not _student_connections[user_id]:
                _student_connections.pop(user_id, None)
        if staff_user_id and staff_user_id in _staff_connections:
            _staff_connections[staff_user_id].discard(websocket)
            if not _staff_connections[staff_user_id]:
                _staff_connections.pop(staff_user_id, None)
