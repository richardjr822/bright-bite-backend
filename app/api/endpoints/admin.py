from fastapi import APIRouter, HTTPException
from app.db.database import supabase

router = APIRouter()

@router.get("/stats")
async def get_admin_stats():
    """Get statistics for admin dashboard"""
    try:
        # Get total users count
        users_response = supabase.table("users").select("id", count="exact").execute()
        total_users = users_response.count if users_response.count else 0
        
        # Get total beneficiaries count
        beneficiaries_response = supabase.table("beneficiaries").select("id", count="exact").execute()
        total_beneficiaries = beneficiaries_response.count if beneficiaries_response.count else 0
        
        # Get total programs count
        programs_response = supabase.table("programs").select("id", count="exact").execute()
        total_programs = programs_response.count if programs_response.count else 0
        
        # Get total meal plans count
        meal_plans_response = supabase.table("meal_plans").select("id", count="exact").execute()
        total_meal_plans = meal_plans_response.count if meal_plans_response.count else 0
        
        return {
            "totalUsers": total_users,
            "totalBeneficiaries": total_beneficiaries,
            "totalPrograms": total_programs,
            "totalMealPlans": total_meal_plans
        }
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
async def get_pending_vendors():
    """Get all pending vendor registrations"""
    try:
        response = supabase.table("users").select("*").eq("role", "vendor").eq("is_approved", False).execute()
        return {"vendors": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/approve-vendor/{vendor_id}")
async def approve_vendor(vendor_id: str):
    """Approve a pending vendor"""
    try:
        # Update vendor to approved status
        response = supabase.table("users").update({
            "is_approved": True,
            "is_active": True
        }).eq("id", vendor_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Vendor not found")
        
        return {"message": "Vendor approved successfully", "vendor": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reject-vendor/{vendor_id}")
async def reject_vendor(vendor_id: str):
    """Reject a pending vendor"""
    try:
        # Delete the vendor registration
        response = supabase.table("users").delete().eq("id", vendor_id).execute()
        
        return {"message": "Vendor rejected and removed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
