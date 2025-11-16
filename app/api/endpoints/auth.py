from fastapi import APIRouter, HTTPException, status, Request, BackgroundTasks, UploadFile, File, Form, Body
from pydantic import BaseModel, EmailStr, Field
from app.db.database import supabase
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from jose import jwt
from typing import Optional, List
import requests
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import sys
import uuid

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "brightbite.gc@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "tmty tdqn ynsb lurr")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "brightbite.gc@gmail.com")

# ===== MODELS =====
class SendOTPRequest(BaseModel):
    email: EmailStr
    name: str

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str

class CompleteRegistrationRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    organization: str
    role: str = "student"

class OTPResponse(BaseModel):
    message: str
    email: str
    expires_at: str

class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    organization_name: str
    agree: bool

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class GoogleAuthRequest(BaseModel):
    token: str
    email: EmailStr
    name: str
    picture: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    organization: Optional[str] = None
    picture: Optional[str] = None

class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    message: str

# Add this new model after GoogleAuthRequest
class CompleteProfileRequest(BaseModel):
    role: str
    agreed_to_terms: bool
    organization_name: str

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

# ===== EMAIL TEMPLATES =====
async def send_registration_otp_email(email: str, name: str, otp: str):
    """Send OTP email for registration"""
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = "🎉 Welcome to BrightBite - Verify Your Email"
        message["From"] = f"BrightBite <{SENDER_EMAIL}>"
        message["To"] = email

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap');
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: 'Montserrat', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                    background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background: #ffffff;
                    border-radius: 20px;
                    overflow: hidden;
                    box-shadow: 0 25px 50px rgba(0, 0, 0, 0.4);
                }}
                .header {{
                    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                    padding: 50px 30px;
                    text-align: center;
                }}
                .logo {{ font-size: 48px; margin-bottom: 10px; }}
                .brand {{ font-size: 42px; font-weight: 800; color: #ffffff; letter-spacing: -1px; }}
                .tagline {{ color: rgba(255,255,255,0.95); font-size: 15px; font-weight: 500; margin-top: 5px; }}
                .content {{ padding: 40px 30px; }}
                .greeting {{ font-size: 26px; font-weight: 700; color: #1f2937; margin-bottom: 15px; }}
                .text {{ font-size: 16px; color: #4b5563; line-height: 1.6; margin-bottom: 25px; }}
                .otp-box {{
                    background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
                    border: 2px dashed #10b981;
                    border-radius: 16px;
                    padding: 30px;
                    text-align: center;
                    margin: 30px 0;
                }}
                .otp-label {{ font-size: 14px; font-weight: 600; color: #059669; text-transform: uppercase; margin-bottom: 10px; }}
                .otp-code {{ font-size: 42px; font-weight: 800; color: #059669; letter-spacing: 12px; margin: 15px 0; }}
                .otp-expire {{ font-size: 13px; color: #059669; font-weight: 500; }}
                .warning {{
                    background: #fef2f2;
                    border-left: 4px solid #ef4444;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                    font-size: 14px;
                    color: #991b1b;
                }}
                .footer {{
                    background: #f9fafb;
                    padding: 25px;
                    text-align: center;
                    font-size: 13px;
                    color: #6b7280;
                }}
                .footer-brand {{ color: #10b981; font-weight: 700; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">🍽️</div>
                    <div class="brand">BrightBite</div>
                    <div class="tagline">Smart Food Management System</div>
                </div>
                
                <div class="content">
                    <h1 class="greeting">Welcome, {name}! 👋</h1>
                    <p class="text">
                        We're excited to have you join <strong>BrightBite</strong>! Enter this code to verify your email and complete your registration.
                    </p>
                    
                    <div class="otp-box">
                        <div class="otp-label">Verification Code</div>
                        <div class="otp-code">{otp}</div>
                        <div class="otp-expire">⏰ Valid for 10 minutes</div>
                    </div>
                    
                    <div class="warning">
                        ⚠️ <strong>Security Notice:</strong> If you didn't request this code, please ignore this email.
                    </div>
                </div>
                
                <div class="footer">
                    <p>© 2025 <span class="footer-brand">BrightBite</span>. All rights reserved.</p>
                    <p style="margin-top: 10px; font-size: 12px;">This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """

        part = MIMEText(html, "html")
        message.attach(part)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
        
        print(f"✅ Registration OTP sent to {email}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"❌ Failed to send registration OTP: {e}", file=sys.stderr)
        return False

async def send_password_reset_otp_email(email: str, name: str, otp: str):
    """Send OTP email for password reset"""
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = "🔐 BrightBite Password Reset"
        message["From"] = f"BrightBite <{SENDER_EMAIL}>"
        message["To"] = email

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap');
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: 'Montserrat', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                    background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background: #ffffff;
                    border-radius: 20px;
                    overflow: hidden;
                    box-shadow: 0 25px 50px rgba(0, 0, 0, 0.4);
                }}
                .header {{
                    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                    padding: 50px 30px;
                    text-align: center;
                    position: relative;
                    overflow: hidden;
                }}
                .header::before {{
                    content: '';
                    position: absolute;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background: url('data:image/svg+xml,%3Csvg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"%3E%3Cg fill="none" fill-rule="evenodd"%3E%3Cg fill="%23ffffff" fill-opacity="0.1"%3E%3Cpath d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zM36 0V4h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/%3E%3C/g%3E%3C/g%3E%3C/svg%3E');
                    opacity: 0.3;
                }}
                .logo {{ font-size: 48px; margin-bottom: 10px; position: relative; z-index: 1; }}
                .brand {{ font-size: 42px; font-weight: 800; color: #ffffff; letter-spacing: -1px; position: relative, z-index: 1; }}
                .tagline {{ color: rgba(255,255,255,0.95); font-size: 15px; font-weight: 500; margin-top: 5px; position: relative; z-index: 1; }}
                .content {{ padding: 40px 30px; }}
                .greeting {{ font-size: 26px; font-weight: 700; color: #1f2937; margin-bottom: 15px; }}
                .text {{ font-size: 16px; color: #4b5563; line-height: 1.6; margin-bottom: 25px; }}
                .otp-box {{
                    background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
                    border: 2px dashed #10b981;
                    border-radius: 16px;
                    padding: 30px;
                    text-align: center;
                    margin: 30px 0;
                }}
                .otp-label {{ font-size: 14px; font-weight: 600; color: #059669; text-transform: uppercase; margin-bottom: 10px; }}
                .otp-code {{ font-size: 42px; font-weight: 800; color: #059669; letter-spacing: 12px; margin: 15px 0; }}
                .otp-expire {{ font-size: 13px; color: #059669; font-weight: 500; }}
                .warning {{
                    background: #fef2f2;
                    border-left: 4px solid #ef4444;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                    font-size: 14px;
                    color: #991b1b;
                    line-height: 1.6;
                }}
                .footer {{
                    background: #f9fafb;
                    padding: 25px;
                    text-align: center;
                    font-size: 13px;
                    color: #6b7280;
                }}
                .footer-brand {{ color: #10b981; font-weight: 700; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">🔐</div>
                    <div class="brand">BrightBite</div>
                    <div class="tagline">Password Reset Request</div>
                </div>
                
                <div class="content">
                    <h1 class="greeting">Hello, {name}!</h1>
                    <p class="text">
                        We received a request to reset your <strong>BrightBite</strong> password. Use the code below to proceed.
                    </p>
                    
                    <div class="otp-box">
                        <div class="otp-label">Password Reset Code</div>
                        <div class="otp-code">{otp}</div>
                        <div class="otp-expire">⏰ Valid for 10 minutes</div>
                    </div>
                    
                    <div class="warning">
                        <strong>⚠️ Important:</strong> If you did NOT request this password reset, please ignore this email. Your password will remain unchanged.
                    </div>
                </div>
                
                <div class="footer">
                    <p>© 2025 <span class="footer-brand">BrightBite</span>. All rights reserved.</p>
                    <p style="margin-top: 10px; font-size: 12px;">This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """

        part = MIMEText(html, "html")
        message.attach(part)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
        
        print(f"✅ Password reset OTP sent to {email}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"❌ Failed to send password reset OTP: {e}", file=sys.stderr)
        return False

# ===== OTP ENDPOINTS =====
@router.post("/send-otp", response_model=OTPResponse)
async def send_otp(request: SendOTPRequest, background_tasks: BackgroundTasks):
    try:
        existing_user = supabase.table("users").select("email").eq("email", request.email).execute()
        if existing_user.data:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        
        otp_data = {
            "email": request.email,
            "otp": otp,
            "expires_at": expires_at.isoformat(),
            "verified": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        supabase.table("otp_codes").delete().eq("email", request.email).execute()
        supabase.table("otp_codes").insert(otp_data).execute()
        
        background_tasks.add_task(send_registration_otp_email, request.email, request.name, otp)
        
        print(f"🔐 OTP: {otp}", file=sys.stderr)
        
        return OTPResponse(
            message="Verification code sent to your email",
            email=request.email,
            expires_at=expires_at.isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-otp")
async def verify_otp(request: VerifyOTPRequest):
    try:
        otp_record = supabase.table("otp_codes")\
            .select("*")\
            .eq("email", request.email)\
            .eq("otp", request.otp)\
            .eq("verified", False)\
            .execute()
        
        if not otp_record.data:
            raise HTTPException(status_code=400, detail="Invalid or expired code")
        
        otp_data = otp_record.data[0]
        expires_at_str = otp_data["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="Code has expired")
        
        supabase.table("otp_codes").update({"verified": True}).eq("id", otp_data["id"]).execute()
        
        return {"message": "Email verified successfully", "verified": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/complete-registration")
async def complete_registration(request: CompleteRegistrationRequest):
    try:
        otp_record = supabase.table("otp_codes")\
            .select("*")\
            .eq("email", request.email)\
            .eq("verified", True)\
            .execute()
        
        if not otp_record.data:
            raise HTTPException(status_code=400, detail="Email not verified")
        
        existing_user = supabase.table("users").select("email").eq("email", request.email).execute()
        if existing_user.data:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        password_hash = pwd_context.hash(request.password)
        
        user_data = {
            "email": request.email,
            "password_hash": password_hash,
            "full_name": request.name,
            "organization": request.organization,
            "role": request.role,
            "agreed_to_terms": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = supabase.table("users").insert(user_data).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create account")
        
        supabase.table("otp_codes").delete().eq("email", request.email).execute()
        
        return {
            "message": "Registration completed successfully",
            "user": {
                "email": result.data[0]["email"],
                "name": result.data[0]["full_name"],
                "organization": result.data[0]["organization"],
                "role": result.data[0]["role"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===== AUTH ENDPOINTS =====
@router.post("/register")
async def register(user: UserRegister):
    try:
        existing = supabase.table("users").select("*").eq("email", user.email).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail="Email already registered")
        
        password_hash = pwd_context.hash(user.password)
        
        result = supabase.table("users").insert({
            "email": user.email,
            "full_name": user.name,
            "password_hash": password_hash,
            "organization": user.organization_name,
            "agreed_to_terms": user.agree
        }).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Registration failed")
        
        return {"message": "Registration successful"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        
        if not pwd_context.verify(password, user_data["password_hash"]):
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

@router.post("/google", response_model=LoginResponse)
async def google_login(payload: GoogleAuthRequest):
    """
    Authenticate user with Google OAuth
    Only for students - vendors must use vendor application process
    """
    try:
        print(f"\n=== GOOGLE AUTH REQUEST ===", file=sys.stderr)
        print(f"Email: {payload.email}", file=sys.stderr)
        
        # Verify Google token
        google_resp = requests.get(
            f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={payload.token}",
            timeout=10
        )
        
        if google_resp.status_code != 200:
            print(f"❌ Invalid Google token", file=sys.stderr)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google token"
            )
        
        google_data = google_resp.json()
        
        # Verify email matches
        if google_data.get("email") != payload.email:
            print(f"❌ Email mismatch", file=sys.stderr)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email mismatch"
            )
        
        # Check if user exists
        user_check = supabase.table("users").select("*").eq("email", payload.email).execute()
        
        if user_check.data:
            # User exists - login
            user_data = user_check.data[0]
            
            # Update last login
            supabase.table("users").update({
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", user_data["id"]).execute()
            
            print(f"✅ Existing user logged in: {payload.email}", file=sys.stderr)
            
        else:
            # Create new user (only students can register via Google)
            print(f"📝 Creating new user: {payload.email}", file=sys.stderr)
            
            new_user_data = {
                "email": payload.email,
                "full_name": payload.name,
                "password_hash": pwd_context.hash(os.urandom(32).hex()),  # Random password
                "role": "student",
                "organization": None,  # Changed from "Google Account" to None
                "agreed_to_terms": False,  # Changed from True to False
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            user_result = supabase.table("users").insert(new_user_data).execute()
            
            if not user_result.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create user"
                )
            
            user_data = user_result.data[0]
            print(f"✅ New user created: {payload.email}", file=sys.stderr)
        
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
            organization=user_data.get("organization"),
            picture=payload.picture
        )
        
        print(f"✅ Google auth successful for {payload.email}", file=sys.stderr)
        
        return LoginResponse(
            token=access_token,
            user=user_response,
            message="Google login successful"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Google auth error: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}"
        )

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
    """
    Handle vendor application submission
    """
    try:
        # Check if email already exists
        user_check = supabase.table('users').select('*').eq('email', email).execute()
        if user_check.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # Generate a unique filename for the business permit
        file_extension = os.path.splitext(businessPermit.filename)[1]
        unique_filename = f"business_permits/{uuid.uuid4()}{file_extension}"
        
        # In a real application, you would upload the file to a storage service here
        # For now, we'll just store the filename
        
        # Hash the password
        hashed_password = pwd_context.hash(password)
        
        # Create user with 'pending_vendor' role
        # Store additional vendor info in organization field as JSON for now
        vendor_info = {
            'businessName': businessName,
            'businessAddress': businessAddress,
            'contactNumber': contactNumber,
            'businessDescription': businessDescription,
            'businessPermitPath': unique_filename
        }
        
        new_user = {
            'email': email,
            'password_hash': hashed_password,
            'full_name': name,
            'role': 'pending_vendor',
            'organization': businessName,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Insert the new user
        result = supabase.table('users').insert(new_user).execute()
        
        # In a real application, you would send an email to the admin here
        # and another email to the vendor confirming receipt of their application
        
        return {
            "message": "Vendor application submitted successfully. Please wait for admin approval.",
            "status": "pending_approval"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in vendor_application: {str(e)}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process vendor application"
        )

@router.post("/complete-profile")
async def complete_profile(request: CompleteProfileRequest, req: Request):
    """
    Complete user profile after Google login
    Updates organization and agreed_to_terms fields
    """
    try:
        # Get token from Authorization header
        auth_header = req.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization token"
            )
        
        token = auth_header.replace("Bearer ", "")
        
        # Decode JWT token
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            email = payload.get("email")
            
            if not user_id or not email:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token"
                )
        except jwt.JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        print(f"\n=== COMPLETE PROFILE REQUEST ===", file=sys.stderr)
        print(f"User ID: {user_id}", file=sys.stderr)
        print(f"Email: {email}", file=sys.stderr)
        print(f"Organization: {request.organization_name}", file=sys.stderr)
        
        # Update user profile
        update_data = {
            "organization": request.organization_name,
            "agreed_to_terms": request.agreed_to_terms,
            "role": request.role,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = supabase.table("users").update(update_data).eq("id", user_id).execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update profile"
            )
        
        user_data = result.data[0]
        
        # If student, create student profile
        if request.role == "student":
            student_profile_check = supabase.table("student_profiles").select("*").eq("user_id", user_id).execute()
            
            if not student_profile_check.data:
                student_profile_data = {
                    "user_id": user_id,
                    "organization_name": request.organization_name,
                    "wallet_balance": 0.00,
                    "points": 0,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                
                supabase.table("student_profiles").insert(student_profile_data).execute()
                print(f"✅ Student profile created for {email}", file=sys.stderr)
        
        user_response = UserResponse(
            id=user_data["id"],
            email=user_data["email"],
            full_name=user_data["full_name"],
            role=user_data.get("role", "student"),
            organization=user_data.get("organization")
        )
        
        print(f"✅ Profile completed for {email}", file=sys.stderr)
        
        return {
            "message": "Profile completed successfully",
            "user": user_response
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Complete profile error: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete profile: {str(e)}"
        )

# ===== PASSWORD RESET ENDPOINTS =====
@router.post("/check-email")
async def check_email(request: dict):
    try:
        email = request.get("email")
        user = supabase.table("users").select("email").eq("email", email).execute()
        
        if not user.data:
            raise HTTPException(status_code=404, detail="Email not found")
        
        return {"message": "Email exists", "email": email}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/send-reset-otp")
async def send_reset_otp(request: SendOTPRequest, background_tasks: BackgroundTasks):
    try:
        user = supabase.table("users").select("email, full_name").eq("email", request.email).execute()
        
        if not user.data:
            raise HTTPException(status_code=404, detail="Email not found")
        
        user_name = user.data[0].get("full_name", "User")
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        
        otp_data = {
            "email": request.email,
            "otp": otp,
            "expires_at": expires_at.isoformat(),
            "verified": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        supabase.table("otp_codes").delete().eq("email", request.email).execute()
        supabase.table("otp_codes").insert(otp_data).execute()
        
        background_tasks.add_task(send_password_reset_otp_email, request.email, user_name, otp)
        
        print(f"🔐 Reset OTP: {otp}", file=sys.stderr)
        
        return OTPResponse(
            message="Password reset code sent to your email",
            email=request.email,
            expires_at=expires_at.isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-reset-otp")
async def verify_reset_otp(request: VerifyOTPRequest):
    return await verify_otp(request)

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

@router.post("/reset-password")
async def reset_password(request: dict):
    try:
        email = request.get("email")
        otp = request.get("otp")
        new_password = request.get("new_password")
        
        otp_record = supabase.table("otp_codes")\
            .select("*")\
            .eq("email", email)\
            .eq("otp", otp)\
            .eq("verified", True)\
            .execute()
        
        if not otp_record.data:
            raise HTTPException(status_code=400, detail="Invalid or unverified OTP")
        
        otp_data = otp_record.data[0]
        expires_at = datetime.fromisoformat(otp_data["expires_at"].replace('Z', '+00:00'))
        
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="OTP has expired")
        
        password_hash = pwd_context.hash(new_password)
        
        result = supabase.table("users")\
            .update({"password_hash": password_hash})\
            .eq("email", email)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update password")
        
        supabase.table("otp_codes").delete().eq("email", email).execute()
        
        return {"message": "Password reset successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))