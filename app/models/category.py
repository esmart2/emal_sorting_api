from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from uuid import UUID

class CategoryBase(BaseModel):
    name: str
    description: Optional[str] = None

class CategoryCreate(CategoryBase):
    pass

class Category(CategoryBase):
    id: int
    user_id: UUID
    category_id: int
    created_at: datetime

    class Config:
        from_attributes = True 