from typing import List, Any, Dict, Optional
from app.models.email import Email
from supabase import Client
from datetime import datetime
from uuid import UUID
import pytz
import email.utils

class EmailDAO:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    # Predefined SQL queries
    GET_ALL_EMAILS = "SELECT * FROM public.emails ORDER BY received_at DESC"
    GET_EMAIL_BY_ID = "SELECT * FROM public.emails WHERE id = :id"
    GET_EMAILS_BY_CATEGORY = "SELECT * FROM public.emails WHERE category = :category"
    GET_RECENT_EMAILS = "SELECT * FROM public.emails WHERE received_at > :date ORDER BY received_at DESC"
    GET_EMAILS_BY_SENDER = "SELECT * FROM public.emails WHERE sender = :sender"
    GET_UNREAD_EMAILS = "SELECT * FROM public.emails WHERE is_read = false"

    def parse_email_date(self, date_str: str) -> str:
        """
        Parse various email date formats and return ISO format.
        Handles both RFC 2822 format (from Gmail) and other common formats.
        """
        try:
            # First try parsing as RFC 2822 format (what Gmail uses)
            parsed_tuple = email.utils.parsedate_tz(date_str)
            if parsed_tuple:
                # Convert to timestamp, then to datetime
                timestamp = email.utils.mktime_tz(parsed_tuple)
                dt = datetime.fromtimestamp(timestamp, pytz.UTC)
                return dt.isoformat()
        except Exception:
            pass

        try:
            # Try common email date format
            dt = datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S %z')
            return dt.isoformat()
        except Exception:
            pass

        try:
            # Try parsing with UTC timezone indicator
            dt = datetime.strptime(date_str.replace(' (UTC)', ''), '%a, %d %b %Y %H:%M:%S')
            dt = pytz.UTC.localize(dt)
            return dt.isoformat()
        except Exception as e:
            raise Exception(f"Unable to parse date format: {date_str}. Error: {str(e)}")

    async def insert_emails(self, user_id: UUID, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Insert multiple emails into the database for a specific user.
        Handles conflicts by updating existing records.
        Now includes google_sub field.
        """
        try:
            # Format emails for insertion
            formatted_emails = []
            for email in emails:
                # Convert received_at to proper timestamp
                received_at = email['received_at']
                if isinstance(received_at, str):
                    received_at = self.parse_email_date(received_at)

                formatted_email = {
                    'user_id': str(user_id),
                    'gmail_message_id': email['gmail_message_id'],
                    'thread_id': email['thread_id'],
                    'subject': email['subject'],
                    'body': email['body'],
                    'received_at': received_at,
                    'archived': email.get('archived', False),
                    'unsubscribe_link': email.get('unsubscribe_link'),
                    'google_sub': email['google_sub']  # Include google_sub
                }
                formatted_emails.append(formatted_email)

            # Insert emails with upsert (update on conflict)
            response = self.supabase.table('raw_emails').upsert(
                formatted_emails,
                on_conflict='user_id,gmail_message_id,thread_id,google_sub'  # Updated conflict constraint
            ).execute()

            if not response.data:
                raise Exception("Failed to insert emails")

            return response.data

        except Exception as e:
            raise Exception(f"Error inserting emails: {str(e)}")

    async def get_all_emails(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all emails for a specific user.
        """
        try:
            response = self.supabase.table('processed_emails') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .order('received_at', desc=True) \
                .execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error fetching emails: {str(e)}")


    async def get_all_raw_emails(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all emails for a specific user.
        """
        try:
            response = self.supabase.table('raw_emails') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .order('received_at', desc=True) \
                .execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error fetching emails: {str(e)}")


    async def get_email_by_id(self, user_id: UUID, gmail_message_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific email by its Gmail message ID for a user, including processing results and category info.
        """
        try:
            # First get the raw email
            raw_email = self.supabase.from_('raw_emails') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .eq('gmail_message_id', gmail_message_id) \
                .execute()

            if not raw_email.data:
                return None

            # Get the first matching email
            email_data = raw_email.data[0]

            # Then get the processed email with category info
            try:
                processed = self.supabase.from_('processed_emails') \
                    .select('''
                        ai_summary,
                        unsubscribed,
                        categories (
                            name,
                            description
                        )
                    ''') \
                    .eq('user_id', str(user_id)) \
                    .eq('gmail_message_id', gmail_message_id) \
                    .execute()

                # Combine the data
                if processed.data and len(processed.data) > 0:
                    processed_data = processed.data[0]
                    category = processed_data.get('categories', {})
                    email_data.update({
                        'ai_summary': processed_data.get('ai_summary'),
                        'unsubscribed': processed_data.get('unsubscribed'),
                        'category_name': category.get('name'),
                        'category_description': category.get('description')
                    })
                else:
                    email_data.update({
                        'ai_summary': None,
                        'unsubscribed': None,
                        'category_name': None,
                        'category_description': None
                    })
            except Exception as e:
                # If processed data doesn't exist yet, just return raw email data
                # with empty processed fields
                email_data.update({
                    'ai_summary': None,
                    'unsubscribed': None,
                    'category_name': None,
                    'category_description': None
                })

            return email_data

        except Exception as e:
            raise Exception(f"Error fetching email: {str(e)}")

    async def get_emails_by_category(self, category: str) -> List[Email]:
        """
        Fetch emails by category using predefined SQL query.
        """
        return await self.execute_raw_sql_with_model(
            self.GET_EMAILS_BY_CATEGORY,
            {"category": category}
        )

    async def get_recent_emails(self, date: str) -> List[Email]:
        """
        Fetch recent emails using predefined SQL query.
        """
        return await self.execute_raw_sql_with_model(
            self.GET_RECENT_EMAILS,
            {"date": date}
        )

    async def get_emails_by_sender(self, sender: str) -> List[Email]:
        """
        Fetch emails by sender using predefined SQL query.
        """
        return await self.execute_raw_sql_with_model(
            self.GET_EMAILS_BY_SENDER,
            {"sender": sender}
        )

    async def get_unread_emails(self) -> List[Email]:
        """
        Fetch unread emails using predefined SQL query.
        """
        return await self.execute_raw_sql_with_model(self.GET_UNREAD_EMAILS)

    async def execute_raw_sql(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query against the database.
        
        Args:
            query (str): The SQL query to execute
            params (Dict[str, Any], optional): Parameters for the query
            
        Returns:
            List[Dict[str, Any]]: The query results
            
        Example:
            ```python
            results = await email_dao.execute_raw_sql(
                "SELECT * FROM public.emails WHERE category = :category",
                {"category": "important"}
            )
            ```
        """
        try:
            response = self.supabase.rpc('execute_sql', {
                'query': query,
                'params': params or {}
            }).execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error executing raw SQL: {str(e)}")

    async def execute_raw_sql_with_model(self, query: str, params: Dict[str, Any] = None) -> List[Email]:
        """
        Execute a raw SQL query and map the results to Email models.
        
        Args:
            query (str): The SQL query to execute
            params (Dict[str, Any], optional): Parameters for the query
            
        Returns:
            List[Email]: The query results mapped to Email models
            
        Example:
            ```python
            emails = await email_dao.execute_raw_sql_with_model(
                "SELECT * FROM public.emails WHERE received_at > :date",
                {"date": "2024-01-01"}
            )
            ```
        """
        results = await self.execute_raw_sql(query, params)
        return [Email(**result) for result in results]

    async def get_emails_by_ids(self, user_id: UUID, gmail_message_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get specific emails by their Gmail message IDs for a user.
        """
        try:
            response = self.supabase.table('raw_emails') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .in_('gmail_message_id', gmail_message_ids) \
                .execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error fetching emails by IDs: {str(e)}")

    async def get_all_categories(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all categories for a user.
        """
        try:
            response = self.supabase.table('categories') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .execute()
            return response.data
        except Exception as e:
            raise Exception(f"Error fetching categories: {str(e)}")

    async def save_email_categories(self, user_id: UUID, categorizations: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Save the email categorization results to the processed_emails table.
        Now includes google_sub field.
        """
        try:
            # Get the original email data for each categorized email
            gmail_message_id = categorizations["gmail_message_id"]
            email = (await self.get_emails_by_ids(user_id, [gmail_message_id]))[0]
            
            # Format the data for insertion
            processed_email = {
                'user_id': str(user_id),
                'gmail_message_id': gmail_message_id,
                'thread_id': email['thread_id'],
                'subject': email['subject'],
                'ai_summary': categorizations['summary'],
                'category_id': categorizations['category_id'],
                'unsubscribed': False,  # Default value
                'received_at': email['received_at'],
                'archived': email.get('archived', False),
                'google_sub': email['google_sub']  # Include google_sub
            }

            # Insert or update the processed email
            response = self.supabase.table('processed_emails').upsert(
                processed_email,
                on_conflict='user_id,gmail_message_id,thread_id,google_sub'  # Updated conflict constraint
            ).execute()

            if not response.data:
                raise Exception("Failed to save processed email")

            return response.data

        except Exception as e:
            raise Exception(f"Error saving email categorization: {str(e)}")

    async def delete_emails(self, user_id: UUID, gmail_message_ids: List[str]) -> None:
        """
        Delete emails from raw_emails table.
        """
        try:
            response = self.supabase.table('raw_emails') \
                .delete() \
                .eq('user_id', str(user_id)) \
                .in_('gmail_message_id', gmail_message_ids) \
                .execute()
                
            if not response.data:
                raise Exception("Failed to delete emails from raw_emails")
                
        except Exception as e:
            raise Exception(f"Error deleting emails from raw_emails: {str(e)}")

    async def delete_processed_emails(self, user_id: UUID, gmail_message_ids: List[str]) -> None:
        """
        Delete emails from processed_emails table.
        """
        try:
            response = self.supabase.table('processed_emails') \
                .delete() \
                .eq('user_id', str(user_id)) \
                .in_('gmail_message_id', gmail_message_ids) \
                .execute()
                
            if not response.data:
                raise Exception("Failed to delete emails from processed_emails")
                
        except Exception as e:
            raise Exception(f"Error deleting emails from processed_emails: {str(e)}")

    async def mark_as_unsubscribed(self, user_id: UUID, gmail_message_id: str) -> None:
        """
        Mark an email as unsubscribed in the processed_emails table.
        """
        try:
            response = self.supabase.table('processed_emails') \
                .update({'unsubscribed': True}) \
                .eq('user_id', str(user_id)) \
                .eq('gmail_message_id', gmail_message_id) \
                .execute()
                
            if not response.data:
                raise Exception("Failed to mark email as unsubscribed")
                
        except Exception as e:
            raise Exception(f"Error marking email as unsubscribed: {str(e)}")

    async def get_gmail_account(self, user_id: UUID, google_sub: str) -> Optional[Dict[str, Any]]:
        """
        Get Gmail account details by user_id and google_sub.
        """
        try:
            response = self.supabase.table('gmail_accounts') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .eq('google_sub', google_sub) \
                .execute()
                
            if not response.data:
                return None
                
            return response.data[0]  # Return first matching account
        except Exception as e:
            raise Exception(f"Error fetching Gmail account: {str(e)}")

    async def mark_as_archived(self, user_id: UUID, gmail_message_id: str) -> None:
        """
        Mark an email as archived in both raw_emails and processed_emails tables.
        """
        try:
            # Update in raw_emails
            response = self.supabase.table('raw_emails') \
                .update({'archived': True}) \
                .eq('user_id', str(user_id)) \
                .eq('gmail_message_id', gmail_message_id) \
                .execute()
                
            if not response.data:
                print(f"Warning: Could not update archived status in raw_emails for message {gmail_message_id}")
            
            # Update in processed_emails
            response = self.supabase.table('processed_emails') \
                .update({'archived': True}) \
                .eq('user_id', str(user_id)) \
                .eq('gmail_message_id', gmail_message_id) \
                .execute()
                
            if not response.data:
                print(f"Warning: Could not update archived status in processed_emails for message {gmail_message_id}")
                
        except Exception as e:
            raise Exception(f"Error marking email as archived: {str(e)}")

    async def get_unprocessed_emails(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all raw emails that haven't been processed yet.
        Returns raw emails that don't have any entry in the processed_emails table.
        """
        try:
            # Get all raw emails for this user
            raw_response = self.supabase.table('raw_emails') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .order('received_at', desc=True) \
                .execute()

            # Get all processed email IDs for this user
            processed_response = self.supabase.table('processed_emails') \
                .select('gmail_message_id') \
                .eq('user_id', str(user_id)) \
                .execute()

            # Filter out any emails that exist in processed_emails
            processed_ids = {email['gmail_message_id'] for email in processed_response.data} if processed_response.data else set()
            unprocessed_emails = [email for email in raw_response.data if email['gmail_message_id'] not in processed_ids] if raw_response.data else []
            
            return unprocessed_emails
            
        except Exception as e:
            raise Exception(f"Error fetching unprocessed emails: {str(e)}") 