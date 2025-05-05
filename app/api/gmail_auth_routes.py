from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from supabase import create_client, Client
from app.dependencies import get_supabase_client, verify_jwt
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

router = APIRouter(prefix="/gmail", tags=["gmail"])

# Initialize OAuth for additional Gmail accounts
oauth = OAuth()
oauth.register(
    name="google_link",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile "
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/gmail.modify "
                "https://www.googleapis.com/auth/gmail.labels",  # Required for modifying/deleting messages
        "token_endpoint_auth_method": "client_secret_post"
    }
)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

async def verify_token(token: str, supabase: Client = Depends(get_supabase_client)) -> Dict[str, Any]:
    """Verify the token and return the user data"""
    try:
        user = supabase.auth.get_user(token)
        return {"user": user}
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

@router.get("/link")
async def link_start(
    request: Request,
    token: Optional[str] = Query(None),
    supabase: Client = Depends(get_supabase_client)
):
    """
    Start the process of linking another Gmail account.
    Redirects user to Google's consent screen.
    """
    try:
        # Log request details
        logger.info("Starting Gmail account linking process")
        logger.info(f"Query token present: {token is not None}")
        logger.info(f"Query parameters: {dict(request.query_params)}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Base URL: {request.base_url}")
        
        auth = None
        
        # Try query parameter token first
        if token:
            try:
                logger.info("Attempting to verify query parameter token")
                decoded_token = token
                try:
                    from urllib.parse import unquote
                    decoded_token = unquote(token)
                    logger.info("Successfully decoded URL-encoded token")
                except Exception as e:
                    logger.warning(f"Token decoding failed (might not be URL encoded): {str(e)}")
                
                auth = await verify_token(decoded_token, supabase)
                logger.info("Query parameter token verified successfully")
            except Exception as e:
                logger.warning(f"Query parameter token verification failed: {str(e)}")
                pass
        
        # If query token failed, try Authorization header
        if not auth:
            auth_header = request.headers.get('Authorization')
            if auth_header:
                try:
                    logger.info("Found Authorization header, attempting to verify")
                    token = auth_header.split(' ')[1] if ' ' in auth_header else auth_header
                    auth = await verify_token(token, supabase)
                    logger.info("Authorization header token verified successfully")
                except Exception as e:
                    logger.error(f"Authorization header token verification failed: {str(e)}")
                    raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
            else:
                logger.error("No authentication token provided")
                raise HTTPException(
                    status_code=401,
                    detail="No authentication token provided. Please include either a query parameter 'token' or an Authorization header."
                )

        # Store the user_id in session for the callback
        user_id = auth["user"].user.id
        logger.info(f"Storing user_id in session: {user_id}")
        request.session["user_id"] = user_id
        
        # Get and print the redirect URI
        redirect_uri = request.url_for("link_callback")
        logger.info(f"Full Redirect URI being used: {redirect_uri}")
        
        # Redirect user to Google consent screen with explicit parameters
        logger.info("Redirecting to Google consent screen")
        return await oauth.google_link.authorize_redirect(
            request, 
            redirect_uri,
            access_type="offline",  # Request refresh token
            prompt="consent"        # Force consent screen
        )
    except Exception as e:
        logger.error(f"Error in link_start: {str(e)}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/link/callback")
async def link_callback(
    request: Request,
    supabase: Client = Depends(get_supabase_client)
):
    """
    Handle the callback from Google OAuth.
    Stores the tokens in gmail_accounts table.
    """
    try:
        # Get tokens and user info from Google
        logger.info("Getting tokens from Google OAuth callback")
        logger.info(f"Callback query params: {dict(request.query_params)}")
        
        token = await oauth.google_link.authorize_access_token(request)
        logger.info("Successfully got access token from Google")
        
        # Get user info from userinfo endpoint
        userinfo_response = await oauth.google_link.get('https://www.googleapis.com/oauth2/v3/userinfo', token=token)
        userinfo = userinfo_response.json()
        logger.info(f"Successfully got user info from Google: {userinfo.get('email')}")
        
        # Get the user_id from session
        user_id = request.session.get("user_id")
        logger.info(f"Retrieved user_id from session: {user_id}")
        if not user_id:
            raise HTTPException(status_code=401, detail="No user session found")

        # Calculate expiration time and convert to ISO format string
        expires_at = (datetime.utcnow() + timedelta(seconds=token["expires_in"])).isoformat()
        
        # Check if we already have a refresh token for this account
        existing_account = supabase.table("gmail_accounts").select("refresh_token").eq("user_id", user_id).eq("google_sub", userinfo["sub"]).execute()
        existing_refresh_token = existing_account.data[0]["refresh_token"] if existing_account.data else None
        
        # Use the new refresh token if provided, otherwise keep the existing one
        refresh_token = token.get("refresh_token") or existing_refresh_token
        
        if not refresh_token:
            logger.error("No refresh token available - neither in response nor in database")
            raise HTTPException(status_code=400, detail="No refresh token available")
        
        # Log the data we're about to upsert
        upsert_data = {
            "user_id": user_id,
            "google_sub": userinfo["sub"],
            "access_token": token["access_token"],
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "email": userinfo["email"]  # Add email from userinfo
        }
        logger.info(f"Attempting to upsert Gmail account with google_sub: {userinfo['sub']}")

        # Upsert into gmail_accounts
        try:
            response = supabase.table("gmail_accounts").upsert(
                upsert_data,
                on_conflict="user_id,google_sub"
            ).execute()
            logger.info(f"Upsert response: {response}")

            if not response.data:
                logger.error("Upsert returned no data")
                raise HTTPException(status_code=500, detail="Failed to store Gmail account")
            
            logger.info("Successfully stored Gmail account")
        except Exception as e:
            logger.error(f"Database error during upsert: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

        # Clear the session
        request.session.pop("user_id", None)
        logger.info("Successfully cleared session")

        # Redirect back to frontend
        return RedirectResponse(f"{FRONTEND_URL}/inbox")
    except Exception as e:
        logger.error(f"Error in link_callback: {str(e)}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e)) 