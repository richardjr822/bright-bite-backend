from fastapi import APIRouter, HTTPException, status, Request, UploadFile, File, Form, Body, Depends

from pydantic import BaseModel, EmailStr

from app.db.database import supabase
from datetime import datetime, timedelta, timezone
from jose import jwt
from typing import Optional, List
import re

import os
import sys

from app.utils.file_upload import save_upload_file
from app.core.security import get_current_user, verify_password, get_password_hash

router = APIRouter()

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7


# ===== MODELS =====
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    organization: Optional[str] = None

class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    message: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class VendorApplicationRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    businessName: str
    businessAddress: str
    contactNumber: str
    businessDescription: str

# ===== JWT FUNCTIONS =====
def create_access_token(data: dict):
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ===== AUTH ENDPOINTS =====

@router.post("/login", response_model=LoginResponse)
async def login(request: Request, payload: Optional[dict] = Body(default=None)):
    try:
        print(f"\n=== LOGIN REQUEST ===", file=sys.stderr)
        email = (payload or {}).get("email")
        password = (payload or {}).get("password")

        # Fallback to form parsing if JSON missing
        if not email or not password:
            try:
                form = await request.form()
                email = email or form.get("email")
                password = password or form.get("password")
            except Exception:
                pass

        if not email or not password:
            raise HTTPException(status_code=422, detail="email and password are required")

        print(f"Email: {email}", file=sys.stderr)

        response = supabase.table("users").select("*").eq("email", email).execute()
        user_data = response.data[0] if response.data else None
        
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        # Block login if vendor application still pending
        if user_data.get("role") == "pending_vendor":
            raise HTTPException(status_code=403, detail="Vendor application pending admin approval")

        if not verify_password(password, user_data["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        # Generate JWT token
        token_data = {
            "sub": user_data["id"],
            "email": user_data["email"],
            "role": user_data.get("role", "student")
        }
        access_token = create_access_token(token_data)
        
        user_response = UserResponse(
            id=user_data["id"],
            email=user_data["email"],
            full_name=user_data["full_name"],
            role=user_data.get("role", "student"),
            organization=user_data.get("organization")
        )
        
        print(f"✅ Login successful for {email}", file=sys.stderr)
        
        return LoginResponse(
            token=access_token,
            user=user_response,
            message="Login successful"
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/vendor-application")
async def vendor_application(
    name: str = Form(...),
    email: EmailStr = Form(...),
    password: str = Form(...),
    businessName: str = Form(...),
    businessAddress: str = Form(...),
    contactNumber: str = Form(...),
    businessDescription: str = Form(...),
    businessPermit: UploadFile = File(...)
):
    """Handle vendor application submission (creates user + vendor profile)."""
    try:
        # Basic password policy: min 8 chars, at least 1 uppercase and 1 digit
        if not password or len(password) < 8 or not re.search(r"[A-Z]", password) or not re.search(r"\d", password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters and include an uppercase letter and a number")
        # Check if email already exists
        user_check = supabase.table('users').select('id').eq('email', email).limit(1).execute()
        if user_check.data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        
        # Hash password
        hashed_password = get_password_hash(password)

        # Save business permit file locally (or could be cloud later)
        permit_path = await save_upload_file(businessPermit, subfolder="business_permits")

        # Insert user with pending_vendor role
        new_user = {
            'email': email,
            'password_hash': hashed_password,
            'full_name': name,
            'role': 'pending_vendor',
            'organization': businessName,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        user_result = supabase.table('users').insert(new_user).execute()
        if not user_result.data:
            raise HTTPException(status_code=500, detail="Failed to create user")
        user_id = user_result.data[0]['id']

        # Insert vendor profile (pending approval)
        vendor_profile = {
            'user_id': user_id,
            'business_name': businessName,
            'business_address': businessAddress,
            'contact_number': contactNumber,
            'business_description': businessDescription,
            'business_permit_url': permit_path,
            'approval_status': 'pending',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        vp_result = supabase.table('vendor_profiles').insert(vendor_profile).execute()
        if not vp_result.data:
            # Rollback user if profile fails (best-effort)
            supabase.table('users').delete().eq('id', user_id).execute()
            raise HTTPException(status_code=500, detail="Failed to create vendor profile")

        return {
            "message": "Vendor application submitted successfully. Await admin approval.",
            "status": "pending_approval",
            "vendor_profile_id": vp_result.data[0].get('id'),
            "permit_path": permit_path
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in vendor_application: {str(e)}", file=sys.stderr)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process vendor application")

@router.get("/pending-vendors", response_model=List[dict])
async def get_pending_vendors(req: Request):
    """
    Get all pending vendor applications (admin only)
    """
    try:
        # Verify admin access
        auth_header = req.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization token"
            )
        
        token = auth_header.replace("Bearer ", "")
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("role") != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )
        except jwt.JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        # Get pending vendors
        result = supabase.table("users") \
            .select("*") \
            .eq("role", "pending_vendor") \
            .order("created_at", desc=True) \
            .execute()
        
        return result.data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching pending vendors: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch pending vendors"
        )

@router.post("/approve-vendor/{user_id}")
async def approve_vendor(user_id: str, req: Request):
    """
    Approve a vendor application (admin only)
    """
    try:
        # Verify admin access
        auth_header = req.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization token"
            )
        
        token = auth_header.replace("Bearer ", "")
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("role") != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )
        except jwt.JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        # Update user role to vendor
        result = supabase.table("users") \
            .update({"role": "vendor", "is_active": True}) \
            .eq("id", user_id) \
            .eq("role", "pending_vendor") \
            .execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending vendor not found"
            )
        
        # In a real application, you would send an approval email here
        
        return {"message": "Vendor approved successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error approving vendor: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve vendor"
        )

@router.post("/reject-vendor/{user_id}")
async def reject_vendor(user_id: str, req: Request):
    """
    Reject a vendor application (admin only)
    """
    try:
        # Verify admin access (same as approve_vendor)
        auth_header = req.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization token"
            )
        
        token = auth_header.replace("Bearer ", "")
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("role") != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )
        except jwt.JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        # Get user data before deleting (for email notification)
        user_data = supabase.table("users") \
            .select("*") \
            .eq("id", user_id) \
            .eq("role", "pending_vendor") \
            .execute()
        
        if not user_data.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending vendor not found"
            )
        
        # Delete the pending vendor
        supabase.table("users").delete().eq("id", user_id).execute()
        
        # In a real application, you would send a rejection email here
        
        return {"message": "Vendor application rejected"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error rejecting vendor: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject vendor application"
        )

@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, current_user = Depends(get_current_user)):
    """Change password for the currently authenticated user using their current password."""
    try:
        user_id = current_user.get("sub") if isinstance(current_user, dict) else None
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Fetch user row
        user_res = supabase.table("users").select("id, password_hash").eq("id", user_id).limit(1).execute()
        if not user_res.data:
            raise HTTPException(status_code=404, detail="User not found")
        row = user_res.data[0]
        if not verify_password(body.current_password, row.get("password_hash") or ""):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        # Enforce password policy and prevent reuse of current password
        if body.current_password == body.new_password:
            raise HTTPException(status_code=400, detail="New password must be different from current password")
        if not body.new_password or len(body.new_password) < 8 or not re.search(r"[A-Z]", body.new_password) or not re.search(r"\d", body.new_password):
            raise HTTPException(status_code=400, detail="New password must be at least 8 characters and include an uppercase letter and a number")
        new_hash = get_password_hash(body.new_password)
        upd = supabase.table("users").update({
            "password_hash": new_hash,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", user_id).execute()
        
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to update password")
        return {"message": "Password updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/logout")
async def logout(req: Request):
    """
    Handle user logout
    While JWT is stateless and can't be truly invalidated without a blacklist,
    this endpoint serves as a logging point and can be extended for token blacklisting
    """
    try:
        # Get token from Authorization header
        auth_header = req.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                user_id = payload.get("sub")
                email = payload.get("email")
                
                # Log the logout activity
                print(f"✅ User logged out: {email} (ID: {user_id})", file=sys.stderr)
                
                # Update last activity timestamp
                supabase.table("users").update({
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", user_id).execute()
                
            except jwt.JWTError:
                pass  # Invalid token, but still allow logout
        
        return {"message": "Logout successful"}
        
    except Exception as e:
        print(f"❌ Logout error: {str(e)}", file=sys.stderr)
        # Even if there's an error, return success to allow client-side cleanup
        return {"message": "Logout successful"}