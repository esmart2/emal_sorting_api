from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.models.category import Category, CategoryCreate
from app.domain.category_service import CategoryService
from app.dao.category_dao import CategoryDAO
from supabase import Client
from app.dependencies import get_supabase_client, verify_jwt

router = APIRouter(prefix="/categories", tags=["categories"])

def get_category_service(supabase: Client = Depends(get_supabase_client)) -> CategoryService:
    category_dao = CategoryDAO(supabase)
    return CategoryService(category_dao)

@router.get("/", response_model=List[Category])
async def get_user_categories(
    category_service: CategoryService = Depends(get_category_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Get all categories for the currently authenticated user.
    """
    try:
        user_id = auth["user"].user.id
        return await category_service.get_user_categories(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=Category)
async def create_category(
    category: CategoryCreate,
    category_service: CategoryService = Depends(get_category_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Create a new category for the currently authenticated user.
    """
    try:
        user_id = auth["user"].user.id
        return await category_service.create_category(
            user_id=user_id,
            name=category.name,
            description=category.description
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 