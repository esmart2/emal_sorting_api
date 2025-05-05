from supabase import create_client, Client
import os
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError
from typing import Optional, Any
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("Missing Supabase credentials")

supabase: Client = create_client(supabase_url, supabase_key)

def get_supabase_client() -> Client:
    """
    Creates and returns a Supabase client instance.
    """
    return supabase

# Security scheme for JWT authentication
security = HTTPBearer()

def get_gmail_service(google_token: str) -> Any:
    """
    Creates and returns a Gmail API service instance using the provided Google OAuth token.
    """
    try:
        credentials = Credentials(
            token=google_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=['https://www.googleapis.com/auth/gmail.readonly']
        )
        
        return build('gmail', 'v1', credentials=credentials)
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to initialize Gmail service: {str(e)}"
        )

async def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Security(security),
    google_token: Optional[str] = Header(None, alias="X-Google-Token"),
    google_refresh: Optional[str] = Header(None, alias="X-Google-Refresh")
) -> dict:
    """
    Verify JWT token from Supabase and Google OAuth.
    Returns the decoded JWT payload if valid.
    """
    try:
        # Get the token from the Authorization header
        token = credentials.credentials
        
        # Verify the token with Supabase
        user = supabase.auth.get_user(token)
        
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials"
            )
            
        if not google_token:
            raise HTTPException(
                status_code=401,
                detail="Google OAuth token is required"
            )
            
        # Initialize Gmail service
        gmail_service = get_gmail_service(google_token)
            
        # Store the tokens and services in the request state for later use
        return {
            "user": user,
            "access_token": token,
            "google_token": google_token,
            "gmail_service": gmail_service
        }
        
    except InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=str(e)
        ) 