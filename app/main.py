from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import os
from fastapi.staticfiles import StaticFiles
from app.api.router import api_router

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

# Enable gzip compression for faster responses over the wire
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Lightweight caching for static uploads
class UploadsCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            if request.url.path.startswith("/uploads") and request.method == "GET" and 200 <= response.status_code < 300:
                # Cache uploaded assets for 7 days; adjust as needed
                response.headers.setdefault("Cache-Control", "public, max-age=604800, immutable")
        except Exception:
            # Never block the response on cache header logic
            pass
        return response

app.add_middleware(UploadsCacheMiddleware)

# Mount only the unified API router at /api
app.include_router(api_router, prefix="/api")

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