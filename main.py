from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.email_routes import router as email_router
from app.api.category_routes import router as category_router
from app.api.gmail_auth_routes import router as gmail_router
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Email Sorter API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Add session middleware for OAuth flow
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "your-secret-key"),  # Replace with a secure secret key
    max_age=3600  # Session expires in 1 hour
)

# Include routers
app.include_router(email_router)
app.include_router(category_router)
app.include_router(gmail_router)

@app.get("/")
async def root():
    return {"message": "Email Sorter API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 