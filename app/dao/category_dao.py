from typing import List, Dict, Any
from supabase import Client
from uuid import UUID

class CategoryDAO:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def get_user_categories(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all categories for a specific user.
        """
        try:
            response = self.supabase.table('categories') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .order('created_at', desc=True) \
                .execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error fetching categories: {str(e)}")

    async def create_category(self, user_id: UUID, name: str, description: str = None) -> Dict[str, Any]:
        """
        Create a new category for a user.
        """
        try:
            # Get the next category_id for this user
            response = self.supabase.table('categories') \
                .select('category_id') \
                .eq('user_id', str(user_id)) \
                .order('category_id', desc=True) \
                .limit(1) \
                .execute()
            
            next_category_id = 1
            if response.data:
                next_category_id = response.data[0]['category_id'] + 1

            # Create the new category
            new_category = {
                'user_id': str(user_id),
                'category_id': next_category_id,
                'name': name,
                'description': description
            }

            response = self.supabase.table('categories') \
                .insert(new_category) \
                .execute()

            if not response.data:
                raise Exception("Failed to create category")

            return response.data[0]
        except Exception as e:
            raise Exception(f"Error creating category: {str(e)}") 