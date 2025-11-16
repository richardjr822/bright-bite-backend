from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field

class MenuItemBase(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, min_length=1)
    price: Optional[float] = Field(None, ge=0)
    category: Optional[str] = Field(None, min_length=1)
    image_url: Optional[str] = None
    is_available: Optional[bool] = True
    has_discount: Optional[bool] = False
    discount_percentage: Optional[int] = Field(0, ge=0, le=100)
    prep_time_minutes: Optional[int] = Field(15, ge=1)
    is_vegetarian: Optional[bool] = True

class MenuItemCreate(MenuItemBase):
    name: str
    description: str
    price: float
    category: str

class MenuItemUpdate(MenuItemBase):
    pass  # all optional for partial updates

class MenuItemRead(MenuItemBase):
    id: UUID
    vendor_id: UUID
    created_at: datetime
    updated_at: datetime

class MenuListResponse(BaseModel):
    menu_items: List[MenuItemRead]