from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from fastapi.staticfiles import StaticFiles
from app.api.router import api_router
from app.meal_plans.router import router as meal_plans_router

app = FastAPI()


origins_env = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
allowed_origins = [o.strip() for o in origins_env.split(",") if o.strip()] or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://bright-bite-frontend.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router with /api prefix
app.include_router(api_router, prefix="/api")
app.include_router(meal_plans_router, prefix="/api")

# Serve uploaded assets
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
@app.get("/")
async def root():
    return {
        "message": "BrightBite API",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "auth": "/api/auth",
            "beneficiaries": "/api/beneficiaries",
            "programs": "/api/programs",
            "meal_plans": "/api/meal-plans",
            "users": "/api/users"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5000, reload=True)