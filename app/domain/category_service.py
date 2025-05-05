from typing import List, Dict, Any
from app.dao.category_dao import CategoryDAO
from uuid import UUID

class CategoryService:
    def __init__(self, category_dao: CategoryDAO):
        self.category_dao = category_dao

    async def get_user_categories(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all categories for a specific user.
        """
        return await self.category_dao.get_user_categories(user_id)

    async def create_category(self, user_id: UUID, name: str, description: str = None) -> Dict[str, Any]:
        """
        Create a new category for a user.
        """
        return await self.category_dao.create_category(user_id, name, description) 