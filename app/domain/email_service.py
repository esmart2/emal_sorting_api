from typing import List, Dict, Any, Optional
from app.dao.email_dao import EmailDAO
from googleapiclient.discovery import Resource, build
from google.oauth2.credentials import Credentials
import base64
import email
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import re
from uuid import UUID
import os

class EmailService:
    def __init__(self, email_dao: EmailDAO):
        self.email_dao = email_dao

    def _build_gmail_service(self, access_token: str, refresh_token: str) -> Resource:
        """
        Build a Gmail service from tokens.
        """
        requested_scopes = [
            "https://mail.google.com/",  # Full access to Gmail account (required for delete)
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.labels"  # Required for modifying/deleting messages
        ]
        
        print(f"Building Gmail service with token: {access_token[:10]}...")
        
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=requested_scopes
        )
        
        # Debug: Print the actual scopes in the credentials
        if hasattr(creds, 'scopes'):
            print(f"Actual scopes in credentials: {creds.scopes}")
        else:
            print("Warning: Could not determine scopes in credentials")
            
        return build("gmail", "v1", credentials=creds)

    async def ensure_primary_account_stored(self, user_id: UUID, provider_token: str, user_email: str, google_sub: str) -> None:
        """
        Ensure the user's primary Google account is stored in gmail_accounts table.
        Uses data from the auth token instead of making admin API calls.
        """
        try:
            # Check if this account is already in gmail_accounts
            existing = self.email_dao.supabase.table("gmail_accounts") \
                .select("*") \
                .eq("user_id", str(user_id)) \
                .eq("email", user_email) \
                .execute()
            
            if existing.data:
                return  # Account already stored
            
            # Store the primary account
            account_data = {
                "user_id": str(user_id),
                "google_sub": google_sub,
                "email": user_email,
                "access_token": provider_token,
                "refresh_token": "primary_account",  # Special value for primary account
                "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat()  # Approximate expiry
            }
            
            response = self.email_dao.supabase.table("gmail_accounts").insert(account_data).execute()
            if not response.data:
                raise Exception("Failed to store primary Gmail account")
                
        except Exception as e:
            print(f"Warning: Could not store primary account: {str(e)}")

    async def refresh_primary_account_tokens(self, user_id: UUID, account: Dict[str, Any]) -> bool:
        """
        Attempt to refresh tokens for the primary account.
        Returns True if successful, False otherwise.
        """
        try:
            # Get fresh user data from Supabase auth
            user_response = self.email_dao.supabase.auth.admin.get_user_by_id(user_id)
            if not user_response or not user_response.user:
                return False
            
            user_data = user_response.user
            identities = user_data.identities
            if not identities or not identities[0]:
                return False
                
            google_identity = identities[0]
            
            # Update the account with fresh tokens
            update_data = {
                "access_token": google_identity.get("access_token"),
                "refresh_token": google_identity.get("refresh_token"),
                "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat()
            }
            
            response = self.email_dao.supabase.table("gmail_accounts") \
                .update(update_data) \
                .eq("user_id", str(user_id)) \
                .eq("email", account["email"]) \
                .execute()
                
            return bool(response.data)
            
        except Exception as e:
            print(f"Error refreshing primary account tokens: {str(e)}")
            return False

    async def poll_all_gmail_accounts(
        self, 
        user_id: UUID, 
        provider_token: str = None, 
        google_sub: str = None, 
        user_email: str = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch emails from all Gmail accounts stored in gmail_accounts table.
        Uses provider_token for primary account and stored tokens for linked accounts.
        """
        try:
            all_emails = []
            
            # Get all Gmail accounts for this user
            accounts_response = self.email_dao.supabase.table('gmail_accounts') \
                .select('*') \
                .eq('user_id', str(user_id)) \
                .execute()
                
            if not accounts_response.data:
                return []  # No Gmail accounts found
                
            # Process each account
            for account in accounts_response.data:
                try:
                    # Determine which tokens to use
                    access_token = None
                    refresh_token = None
                    
                    if account['refresh_token'] == 'primary_account':
                        if not provider_token:
                            print(f"Warning: No provider token available for primary account {account['email']}")
                            continue
                        access_token = provider_token
                        refresh_token = None
                    else:
                        access_token = account['access_token']
                        refresh_token = account['refresh_token']
                    
                    # Build Gmail service
                    gmail_service = self._build_gmail_service(
                        access_token=access_token,
                        refresh_token=refresh_token
                    )
                    
                    # Fetch emails from this account
                    emails = await self.poll_gmail(gmail_service, user_id, account['google_sub'])
                    all_emails.extend(emails)
                    
                except Exception as e:
                    print(f"Error processing Gmail account {account['email']}: {str(e)}")
                    continue
            
            # Sort all emails by received_at
            all_emails.sort(key=lambda x: x["received_at"], reverse=True)
            return all_emails

        except Exception as e:
            raise Exception(f"Error polling Gmail accounts: {str(e)}")

    def extract_unsubscribe_link(self, headers: List[Dict[str, str]], body: str) -> Optional[str]:
        """
        Extract unsubscribe link from email headers or body.
        """
        # Check List-Unsubscribe header first
        list_unsubscribe = next(
            (h['value'] for h in headers if h['name'].lower() == 'list-unsubscribe'),
            None
        )
        if list_unsubscribe:
            # Extract URL from <> brackets if present
            url_match = re.search(r'<(https?://[^>]+)>', list_unsubscribe)
            if url_match:
                return url_match.group(1)
            # If no brackets, check if it's a direct URL
            url_match = re.search(r'(https?://\S+)', list_unsubscribe)
            if url_match:
                return url_match.group(1)

        # Look for common unsubscribe patterns in the body
        unsubscribe_patterns = [
            r'https?://[^\s<>"]+?unsubscribe[^\s<>"]*',
            r'https?://[^\s<>"]+?opt-?out[^\s<>"]*',
            r'https?://[^\s<>"]+?(?:click\.)[^\s<>"]*(?:unsubscribe|opt-?out)[^\s<>"]*'
        ]
        
        for pattern in unsubscribe_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(0)
        
        return None

    def extract_email_body(self, payload: Dict[str, Any]) -> str:
        """
        Extract email body from Gmail API message payload.
        Handles both plain text and HTML parts.
        
        Args:
            payload: The message payload from Gmail API
            
        Returns:
            str: The extracted email body, preferring HTML over plain text
        """
        if not payload:
            return ""

        # If the payload has a body and data, it's a simple message
        if "body" in payload and "data" in payload["body"]:
            return base64.urlsafe_b64decode(payload["body"]["data"].encode("utf-8")).decode("utf-8")

        # If the payload has parts, it's a multipart message
        if "parts" in payload:
            html_part = None
            text_part = None
            
            # First pass: look for HTML and text parts
            for part in payload["parts"]:
                mime_type = part.get("mimeType", "")
                if mime_type == "text/html" and "body" in part and "data" in part["body"]:
                    html_part = part
                elif mime_type == "text/plain" and "body" in part and "data" in part["body"]:
                    text_part = part
                    
                # Handle nested multipart messages
                elif "parts" in part:
                    nested_body = self.extract_email_body(part)
                    if nested_body:
                        return nested_body

            # Prefer HTML over plain text
            if html_part:
                return base64.urlsafe_b64decode(html_part["body"]["data"].encode("utf-8")).decode("utf-8")
            elif text_part:
                return base64.urlsafe_b64decode(text_part["body"]["data"].encode("utf-8")).decode("utf-8")

        return "No readable content"

    async def poll_gmail(self, gmail_service: Resource, user_id: UUID, google_sub: str) -> List[Dict[str, Any]]:
        """
        Fetch emails from Gmail API and store them in the database.
        Now includes google_sub to track which Gmail account the email came from.
        Only fetches the 2 most recent emails.
        After saving to database, archives the emails in Gmail.
        Returns only unprocessed emails that need AI analysis.
        """
        try:
            emails_to_store = []
            
            # Get list of messages, limited to 2 most recent
            results = gmail_service.users().messages().list(
                userId='me',
                maxResults=2  # Only get 2 most recent emails
            ).execute()
            
            messages = results.get('messages', [])
            
            for message in messages:
                # Get full message details
                msg = gmail_service.users().messages().get(userId='me', id=message['id']).execute()
                
                # Extract headers
                headers = msg['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                date = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
                
                # Extract body
                body = self.extract_email_body(msg['payload'])
                
                # Extract unsubscribe link
                unsubscribe_link = self.extract_unsubscribe_link(headers, body)
                
                # Create email object
                email_data = {
                    'gmail_message_id': message['id'],
                    'thread_id': msg['threadId'],
                    'subject': subject,
                    'body': body or 'No content',  # Ensure body is never empty
                    'received_at': date,
                    'archived': False,
                    'unsubscribe_link': unsubscribe_link,
                    'google_sub': google_sub  # Add google_sub to track which account it came from
                }
                
                emails_to_store.append(email_data)
            
            # Store new emails in the database
            if emails_to_store:
                #TODO: stored_emails and emails_to_store could be problematic
                stored_emails = await self.email_dao.insert_emails(user_id, emails_to_store)
                
                # Archive the emails in Gmail after successful storage
                for email in emails_to_store:
                    try:
                        # Modify the email to remove from inbox (archive it)
                        gmail_service.users().messages().modify(
                            userId='me',
                            id=email['gmail_message_id'],
                            body={
                                'removeLabelIds': ['INBOX']
                            }
                        ).execute()
                        
                        # Update archived status in database
                        await self.email_dao.mark_as_archived(user_id, email['gmail_message_id'])
                        
                    except Exception as e:
                        print(f"Warning: Could not archive email {email['gmail_message_id']}: {str(e)}")
            
            # Return only unprocessed emails that need AI analysis
            return await self.email_dao.get_unprocessed_emails(user_id)
            
        except Exception as e:
            raise Exception(f"Error processing emails: {str(e)}")

    async def get_all_emails(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Fetch all emails for a user from the database.
        """
        return await self.email_dao.get_all_emails(user_id)

    async def get_email_by_id(self, email_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a specific email by ID.
        """
        return await self.email_dao.get_email_by_id(email_id)

    async def get_emails_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Fetch emails by category.
        """
        return await self.email_dao.get_emails_by_category(category)

    async def get_recent_emails(self, date: str) -> List[Dict[str, Any]]:
        """
        Fetch recent emails after a specific date.
        """
        return await self.email_dao.get_recent_emails(date)

    async def get_emails_by_sender(self, sender: str) -> List[Dict[str, Any]]:
        """
        Fetch emails by sender.
        """
        return await self.email_dao.get_emails_by_sender(sender)

    async def get_unread_emails(self) -> List[Dict[str, Any]]:
        """
        Fetch all unread emails.
        """
        return await self.email_dao.get_unread_emails()

    async def execute_raw_sql(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query against the database.
        """
        return await self.email_dao.execute_raw_sql(query, params)

    async def execute_raw_sql_with_model(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query and map the results to Email models.
        """
        return await self.email_dao.execute_raw_sql_with_model(query, params)

    async def delete_emails(self, user_id: UUID, gmail_message_ids: List[str], provider_token: str = None) -> None:
        """
        Delete emails from Gmail and local database.
        For primary accounts (refresh_token = 'primary_account'), uses the provider_token from the API request.
        For linked accounts, uses the stored tokens.
        """
        try:
            # First, get the emails to find out which Gmail account they belong to
            emails_to_delete = []
            for gmail_message_id in gmail_message_ids:
                email = await self.email_dao.get_email_by_id(user_id, gmail_message_id)
                if email:
                    emails_to_delete.append({
                        'gmail_message_id': gmail_message_id,
                        'google_sub': email['google_sub']
                    })

            # Group emails by google_sub
            emails_by_account = {}
            for email in emails_to_delete:
                if email['google_sub'] not in emails_by_account:
                    emails_by_account[email['google_sub']] = []
                emails_by_account[email['google_sub']].append(email['gmail_message_id'])

            # Delete from each Gmail account
            for google_sub, message_ids in emails_by_account.items():
                try:
                    # Get the Gmail account credentials
                    account = await self.email_dao.get_gmail_account(user_id, google_sub)
                    if not account:
                        print(f"Warning: Gmail account {google_sub} not found")
                        continue

                    # Determine which access token to use
                    access_token = None
                    refresh_token = None
                    
                    if account["refresh_token"] == "primary_account":
                        if not provider_token:
                            print(f"Warning: No provider token available for primary account {account['email']}")
                            continue
                        access_token = provider_token
                        refresh_token = None  # Not needed for primary account
                    else:
                        access_token = account["access_token"]
                        refresh_token = account["refresh_token"]

                    # Build Gmail service for this account
                    gmail_service = self._build_gmail_service(
                        access_token=access_token,
                        refresh_token=refresh_token
                    )

                    # Delete from Gmail
                    for message_id in message_ids:
                        try:
                            # Instead of deleting, move to trash first
                            gmail_service.users().messages().trash(
                                userId='me',
                                id=message_id
                            ).execute()
                        except Exception as e:
                            print(f"Error deleting email {message_id} from Gmail account {account['email']}: {str(e)}")

                except Exception as e:
                    print(f"Error processing Gmail account {account.get('email', google_sub)}: {str(e)}")
                    continue

            # Delete from database
            await self.email_dao.delete_emails(user_id, gmail_message_ids)
            await self.email_dao.delete_processed_emails(user_id, gmail_message_ids)

        except Exception as e:
            raise Exception(f"Error deleting emails: {str(e)}")

    async def unsubscribe_email(self, user_id: UUID, gmail_message_id: str) -> Dict[str, Any]:
        """
        Mark an email as unsubscribed and return the unsubscribe link.
        """
        try:
            # Get the email from raw_emails
            email = await self.email_dao.get_email_by_id(user_id, gmail_message_id)
            if not email:
                raise Exception("Email not found")
            
            if not email.get('unsubscribe_link'):
                raise Exception("No unsubscribe link found for this email")
            
            # Mark as unsubscribed in processed_emails
            await self.email_dao.mark_as_unsubscribed(user_id, gmail_message_id)
            
            return {
                "unsubscribe_link": email['unsubscribe_link'],
                "message": "Email marked as unsubscribed"
            }
            
        except Exception as e:
            raise Exception(f"Error processing unsubscribe request: {str(e)}")

    async def archive_emails(self, user_id: UUID, gmail_message_ids: List[str], provider_token: str = None) -> None:
        """
        Archive emails in Gmail by removing the INBOX label.
        For primary accounts (refresh_token = 'primary_account'), uses the provider_token from the API request.
        For linked accounts, uses the stored tokens.
        """
        try:
            # First, get the emails to find out which Gmail account they belong to
            emails_to_archive = []
            for gmail_message_id in gmail_message_ids:
                email = await self.email_dao.get_email_by_id(user_id, gmail_message_id)
                if email:
                    emails_to_archive.append({
                        'gmail_message_id': gmail_message_id,
                        'google_sub': email['google_sub']
                    })

            # Group emails by google_sub
            emails_by_account = {}
            for email in emails_to_archive:
                if email['google_sub'] not in emails_by_account:
                    emails_by_account[email['google_sub']] = []
                emails_by_account[email['google_sub']].append(email['gmail_message_id'])

            # Archive in each Gmail account
            for google_sub, message_ids in emails_by_account.items():
                try:
                    # Get the Gmail account credentials
                    account = await self.email_dao.get_gmail_account(user_id, google_sub)
                    if not account:
                        print(f"Warning: Gmail account {google_sub} not found")
                        continue

                    # Determine which access token to use
                    access_token = None
                    refresh_token = None
                    
                    if account["refresh_token"] == "primary_account":
                        if not provider_token:
                            print(f"Warning: No provider token available for primary account {account['email']}")
                            continue
                        access_token = provider_token
                        refresh_token = None  # Not needed for primary account
                    else:
                        access_token = account["access_token"]
                        refresh_token = account["refresh_token"]

                    # Build Gmail service for this account
                    gmail_service = self._build_gmail_service(
                        access_token=access_token,
                        refresh_token=refresh_token
                    )

                    # Archive each email by removing INBOX label
                    for message_id in message_ids:
                        try:
                            gmail_service.users().messages().modify(
                                userId='me',
                                id=message_id,
                                body={
                                    'removeLabelIds': ['INBOX']
                                }
                            ).execute()
                        except Exception as e:
                            print(f"Error archiving email {message_id} in Gmail account {account['email']}: {str(e)}")

                except Exception as e:
                    print(f"Error processing Gmail account {account.get('email', google_sub)}: {str(e)}")
                    continue

            # Update archived status in database
            for message_id in gmail_message_ids:
                await self.email_dao.mark_as_archived(user_id, message_id)

        except Exception as e:
            raise Exception(f"Error archiving emails: {str(e)}") 