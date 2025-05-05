from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Any, Optional
from uuid import UUID
from googleapiclient.discovery import Resource
from app.models.email import Email
from app.domain.email_service import EmailService
from app.dao.email_dao import EmailDAO
from supabase import Client
from pydantic import BaseModel
from app.dependencies import get_supabase_client, verify_jwt
import os
from app.services.openai_service import OpenAIService
from fastapi.security import OAuth2PasswordRequestForm
from datetime import datetime

router = APIRouter(prefix="/emails", tags=["emails"])

# Set up templates
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))

class RawSQLQuery(BaseModel):
    query: str
    params: Dict[str, Any] = None

class SQLResponse(BaseModel):
    data: List[Dict[str, Any]] | None
    metadata: Dict[str, Any]
    query: Dict[str, Any]
    timestamp: str

class EmailIdsRequest(BaseModel):
    gmail_message_ids: List[str]

class EmailCategorization(BaseModel):
    gmail_message_id: str
    category_id: int
    summary: str
    confidence: float

class LinkedAccount(BaseModel):
    email: str
    created_at: datetime

    class Config:
        from_attributes = True  # This enables ORM mode for Pydantic

def get_email_service(supabase: Client = Depends(get_supabase_client)) -> EmailService:
    email_dao = EmailDAO(supabase)
    return EmailService(email_dao)

@router.get("/", response_model=List[Dict[str, Any]])
async def get_emails(
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Fetch emails from Gmail API and store them in the database.
    Requires authentication with both Supabase and Google OAuth.
    """
    try:
        user_id = auth["user"].user.id
        return await email_service.get_all_emails(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/collection", response_model=Dict[str, str])
async def start_email_collection(
    background_tasks: BackgroundTasks,
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Start the email collection process in the background.
    This will:
    1. Fetch emails from all linked Gmail accounts
    2. Store them in the raw_emails table
    3. Process each email with OpenAI for categorization
    4. Store the results in the processed_emails table
    """
    try:
        user_id = auth["user"].user.id
        google_token = auth.get("google_token")
        user_email = auth["user"].user.email
        
        # Get the Google sub ID from identities
        google_sub = None
        if auth["user"].user.identities:
            google_identity = next(
                (identity for identity in auth["user"].user.identities 
                 if identity.provider == "google"),
                None
            )
            if google_identity:
                google_sub = google_identity.id  # This is the Google sub ID
        
        if not google_sub:
            # Fallback to provider_id from metadata
            google_sub = auth["user"].user.app_metadata.get("provider_id")
            
        print("Found Google sub ID:", google_sub)
        
        if not google_sub:
            raise HTTPException(
                status_code=400,
                detail="Could not find Google sub ID for user"
            )
        
        openai_service = OpenAIService()
        # TODO uncomment this when ready to collect emails.
        # Add the task to background processing
        background_tasks.add_task(
            process_emails_background,
            email_service,
            user_id,
            google_token,
            google_sub,
            user_email,
            openai_service
        )
        
        return {"status": "Email collection process started successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def process_emails_background(
    email_service: EmailService,
    user_id: UUID,
    google_token: str,
    google_sub: str,
    user_email: str,
    openai_service: OpenAIService
):
    """
    Background task to process emails from all Gmail accounts:
    1. Fetch emails from all linked Gmail accounts
    2. Store in raw_emails table
    3. Categorize each email with OpenAI
    4. Store results in processed_emails table
    """
    try:
        # Fetch and store emails from all accounts
        emails = await email_service.poll_all_gmail_accounts(
            user_id, 
            google_token,
            google_sub, 
            user_email
        )
        print(f"Successfully fetched {len(emails)} emails for user {user_id}")
        
        # Get categories for this user
        categories = await email_service.email_dao.get_all_categories(user_id)
        
        if not categories:
            print(f"No categories found for user {user_id}")
            return
            
        print(f"Found {len(categories)} categories for user {user_id}")
            
        # Process each email that hasn't been processed yet
        processed_count = 0
        for email in emails:
            try:
                # Categorize email
                result = await openai_service.categorize_email(email, categories)
                
                # Save categorization
                await email_service.email_dao.save_email_categories(user_id, result)
                processed_count += 1
                
            except Exception as e:
                # Log error but continue processing other emails
                print(f"Warning: Could not process email {email.get('gmail_message_id', 'unknown')}: {str(e)}")
                continue
                
        print(f"Successfully processed {processed_count} new emails for user {user_id}")
                
    except Exception as e:
        print(f"Error in background task: {str(e)}")

@router.post("/delete", response_model=Dict[str, str])
async def delete_emails(
    request: EmailIdsRequest,
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Delete specified emails from their respective Gmail accounts and local database.
    Requires a list of Gmail message IDs.
    The function will:
    1. Find which Gmail account each email belongs to
    2. Delete each email from its respective Gmail account
    3. Delete the emails from both raw_emails and processed_emails tables
    """
    try:
        user_id = auth["user"].user.id
        # Get the provider token from auth data
        provider_token = auth.get("google_token")
        
        # Debug: Print auth details
        print("Auth data available:", list(auth.keys()))
        if "provider" in auth:
            print("Provider details:", auth["provider"])
        if "user" in auth and hasattr(auth["user"], "app_metadata"):
            print("User metadata:", auth["user"].app_metadata)
            
        # Check if we have the token
        if not provider_token:
            print("Warning: No provider token found in auth data")
        else:
            print(f"Provider token present: {provider_token[:10]}...")
            
        await email_service.delete_emails(user_id, request.gmail_message_ids, provider_token=provider_token)
        return {"message": f"Successfully deleted {len(request.gmail_message_ids)} emails"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/unsubscribe/{gmail_message_id}", response_model=Dict[str, Any])
async def unsubscribe_email(
    gmail_message_id: str,
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Mark an email as unsubscribed and return the unsubscribe link.
    """
    try:
        user_id = auth["user"].user.id
        result = await email_service.unsubscribe_email(user_id, gmail_message_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/store-primary-account", response_model=Dict[str, str])
async def store_primary_account(
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Store the user's primary Google account (from Supabase auth) in gmail_accounts table.
    This should be called once during login.
    """
    try:
        user_id = auth["user"].user.id
        google_token = auth.get("google_token")
        user_email = auth["user"].user.email
        
        if not google_token:
            raise HTTPException(
                status_code=400,
                detail="No Google token found in auth data"
            )
            
        # Get the Google sub ID from identities
        google_sub = None
        if auth["user"].user.identities:
            google_identity = next(
                (identity for identity in auth["user"].user.identities 
                 if identity.provider == "google"),
                None
            )
            if google_identity:
                google_sub = google_identity.id  # This is the Google sub ID
        
        if not google_sub:
            # Fallback to provider_id from metadata
            google_sub = auth["user"].user.app_metadata.get("provider_id")
            
        if not google_sub:
            raise HTTPException(
                status_code=400,
                detail="Could not find Google sub ID for user"
            )
            
        await email_service.ensure_primary_account_stored(
            user_id=user_id,
            provider_token=google_token,
            user_email=user_email,
            google_sub=google_sub
        )
        
        return {"status": "Primary account stored successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/accounts/linked", response_model=List[Dict[str, Any]])
async def get_linked_accounts(
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Get all Gmail accounts linked to the user.
    Returns the created_at and email fields for each account.
    """
    try:
        user_id = auth["user"].user.id
        
        response = email_service.email_dao.supabase.table("gmail_accounts") \
            .select("email,created_at") \
            .eq("user_id", str(user_id)) \
            .execute()
            
        if not response.data:
            return []
            
        return response.data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{gmail_message_id}", response_model=Dict[str, Any])
async def get_email_by_id(
    gmail_message_id: str,
    email_service: EmailService = Depends(get_email_service),
    auth: dict = Depends(verify_jwt)
):
    """
    Fetch a specific email by its Gmail message ID.
    Requires authentication.
    """
    try:
        user_id = auth["user"].user.id
        email = await email_service.email_dao.get_email_by_id(user_id, gmail_message_id)
        if not email:
            raise HTTPException(status_code=404, detail="Email not found")
        return email
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 