from fastapi import APIRouter
from app.api.endpoints import auth, beneficiaries, meal_plans, programs, users, nutrition, wallet, rewards, vouchers, deals, feedback, student, vendor, staff, admin, realtime

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(beneficiaries.router, prefix="/beneficiaries", tags=["beneficiaries"])
api_router.include_router(meal_plans.router, prefix="/meal-plans", tags=["meal-plans"])
api_router.include_router(programs.router, prefix="/programs", tags=["programs"])
api_router.include_router(nutrition.router, tags=["nutrition"])
api_router.include_router(wallet.router, tags=["wallet"])
api_router.include_router(rewards.router, tags=["rewards"])
api_router.include_router(vouchers.router, tags=["vouchers"])
api_router.include_router(deals.router, tags=["deals"]) 
api_router.include_router(feedback.router, tags=["feedback"]) 
api_router.include_router(student.router, tags=["student"]) 
api_router.include_router(vendor.router, prefix="/vendor", tags=["vendor"])
api_router.include_router(staff.router, prefix="/staff", tags=["staff"]) 
api_router.include_router(admin.router, prefix="/admin", tags=["admin"]) 
api_router.include_router(realtime.router, tags=["realtime"]) 

