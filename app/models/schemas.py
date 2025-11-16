from typing import Optional, Literal
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

# Authentication schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user_type: str
    user_id: UUID
    full_name: str
    student_no: Optional[str] = None
    username: Optional[str] = None

class TokenData(BaseModel):
    id: Optional[UUID] = None
    user_type: Optional[str] = None

class UserLogin(BaseModel):
    password: str
    user_type: Literal["student", "admin"]
    username: Optional[str] = None  
    student_no: Optional[str] = None

# Student schemas
class StudentBase(BaseModel):
    student_no: str = Field(..., min_length=1, max_length=20)
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    program: str = Field(..., pattern='^(BSIT|BSCS|BSEMC)$')
    year_level: str = Field(..., min_length=1, max_length=20)
    section: str = Field(..., min_length=1, max_length=10)

class StudentCreate(StudentBase):
    password: str = Field(..., min_length=6)

class StudentUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)
    program: Optional[str] = Field(None, pattern='^(BSIT|BSCS|BSEMC)$')
    year_level: Optional[str] = Field(None, min_length=1, max_length=20)
    section: Optional[str] = Field(None, min_length=1, max_length=10)

class Student(StudentBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

# Administrator schemas
class AdminBase(BaseModel):
    first_name: str
    last_name: str
    username: str

class AdminCreate(AdminBase):
    password: str

class AdminUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class Admin(AdminBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

# Organization schemas
class OrganizationBase(BaseModel):
    name: str
    description: Optional[str] = None
    is_active: bool = False

class OrganizationCreate(OrganizationBase):
    pass

class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

class Organization(OrganizationBase):
    id: UUID
    created_at: datetime

# Position schemas
class PositionBase(BaseModel):
    name: str
    organization_id: UUID

class PositionCreate(PositionBase):
    pass

class Position(PositionBase):
    id: UUID
    created_at: datetime

class PositionResponse(PositionBase):
    id: UUID
    created_at: datetime

    class Config:
        orm_mode = True

# Election schemas
class ElectionBase(BaseModel):
    organization_id: UUID
    duration_hours: int
    eligible_voters: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: str = "not_started"

class ElectionCreate(ElectionBase):
    pass

class ElectionUpdate(BaseModel):
    duration_hours: Optional[int] = None
    eligible_voters: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = None

class Election(ElectionBase):
    id: UUID
    created_at: datetime

# Candidate schemas
class CandidateBase(BaseModel):
    name: str
    position_id: UUID
    organization_id: UUID
    photo_url: Optional[str] = None
    is_archived: bool = False

class CandidateCreate(CandidateBase):
    pass

class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    photo_url: Optional[str] = None
    is_archived: Optional[bool] = None

class Candidate(CandidateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

class CandidateResponse(CandidateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    is_archived: bool = False

    class Config:
        orm_mode = True

class CandidateHistory(BaseModel):
    id: UUID
    name: str
    position: str
    organization: str
    photo_url: Optional[str] = None
    created_at: datetime

# Vote schemas
class VoteBase(BaseModel):
    election_id: UUID
    student_id: UUID
    candidate_id: UUID
    position_id: UUID

class VoteCreate(VoteBase):
    pass

class Vote(VoteBase):
    id: UUID
    created_at: datetime