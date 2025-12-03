from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from app.db.database import supabase
from datetime import datetime

router = APIRouter()

class UserBase(BaseModel):
    full_name: str
    email: EmailStr
    role: Optional[str] = "user"
    organization: Optional[str] = None
    agreed_to_terms: Optional[bool] = False

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: str
    created_at: Optional[str] = None

class AgreeTermsRequest(BaseModel):
    agreed_to_terms: bool

@router.get("", response_model=List[UserResponse])
async def get_users():
    try:
        response = supabase.table("users").select("*").order("created_at", desc=False).execute()
        if not response.data:
            return []
        return [UserResponse(
            id=user["id"],
            full_name=user["full_name"],
            email=user["email"],
            role=user.get("role", "user"),
            organization=user.get("organization"),
            agreed_to_terms=user.get("agreed_to_terms", False),
            created_at=user.get("created_at")
        ) for user in response.data]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@router.post("", response_model=UserResponse)
async def create_user(user: UserCreate):
    try:
        # For demo: store password as password_hash (should hash in production)
        data = {
            "full_name": user.full_name,
            "email": user.email,
            "password_hash": user.password,  # Hash in production!
            "role": user.role,
            "organization": user.organization,
            "agreed_to_terms": user.agreed_to_terms,
            "created_at": datetime.now().isoformat()
        }
        result = supabase.table("users").insert(data).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=500, detail="Failed to create user")
        u = result.data[0]
        return UserResponse(
            id=u["id"],
            full_name=u["full_name"],
            email=u["email"],
            role=u.get("role", "user"),
            organization=u.get("organization"),
            agreed_to_terms=u.get("agreed_to_terms", False),
            created_at=u.get("created_at")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

@router.patch("/{user_id}/agree-terms")
async def update_terms_agreement(user_id: str, request: AgreeTermsRequest):
    try:
        data = {
            "agreed_to_terms": request.agreed_to_terms,
            "updated_at": datetime.now().isoformat()
        }
        
        result = supabase.table("users").update(data).eq("id", user_id).execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found"
            )
        
        return {
            "success": True,
            "message": "Terms agreement updated successfully",
            "agreed_to_terms": request.agreed_to_terms
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating terms agreement: {str(e)}"
        )