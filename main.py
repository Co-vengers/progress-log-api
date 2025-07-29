# main.py
# To run this:
# 1. pip install "fastapi[all]" uvicorn firebase-admin-python
# 2. Get your Firebase serviceAccountKey.json file and place it in the same directory.
#    (Firebase Console -> Project Settings -> Service accounts -> Generate new private key)
# 3. Run the server: uvicorn main:app --reload

import os
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime
import json

# --- Pydantic Models (Data Validation) ---
class LogEntry(BaseModel):
    taskDescription: str
    project: str
    status: str
    priority: str
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    duration: Optional[str] = None
    comments: Optional[str] = None
    date: str = Field(default_factory=lambda: datetime.now().isoformat())

class LogEntryResponse(LogEntry):
    id: str

# --- Firebase Initialization ---
try:
    # Get the credentials from the environment variable for deployment
    creds_json_str = os.getenv("FIREBASE_CREDENTIALS")
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        cred = credentials.Certificate(creds_dict)
        print("Initializing Firebase Admin SDK from environment variable...")
    else:
        # Fallback to local file for development
        cred_path = 'serviceAccountKey.json'
        if not os.path.exists(cred_path):
            raise FileNotFoundError(
                "Firebase service account key file not found. "
                "Please download it from your Firebase project settings and place it as 'serviceAccountKey.json' "
                "or set the FIREBASE_CREDENTIALS environment variable."
            )
        cred = credentials.Certificate(cred_path)
        print("Initializing Firebase Admin SDK from local file...")

    # Check if the app is already initialized to prevent errors during hot-reloading
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    print("Firebase Admin SDK initialized successfully.")

except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    db = None

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Daily Progress Log API",
    description="API for managing daily progress logs.",
    version="1.0.0"
)

# --- CORS Middleware ---
# This allows the React frontend (running on a different port) to communicate with the API.

# IMPORTANT: Replace this with your actual Vercel frontend URL
# Example: "https://my-progress-log-client.vercel.app"
# Do NOT include a trailing slash "/"
origins = [
    "https://my-progress-log-qwsh.vercel.app/", # ### <--- REPLACE THIS LINE ###
    "http://localhost:3000", # Keep for local development
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods
    allow_headers=["*"], # Allows all headers
)

# --- Dependency to get and verify user ID from Firebase Auth token ---
async def get_current_user(request: Request) -> str:
    """
    Verifies the Firebase ID token from the Authorization header
    and returns the user's UID.
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Firebase connection not available.")

    auth_header = request.headers.get('Authorization')
    if not auth_header:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    try:
        # The token is expected to be "Bearer <token>"
        id_token = auth_header.split("Bearer ")[1]
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token['uid']
    except (IndexError, auth.InvalidIdTokenError, auth.ExpiredIdTokenError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired authentication token: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred during token verification: {e}")


# --- API Endpoints ---

@app.get("/", tags=["Root"])
def read_root():
    return {"message": "Welcome to the Daily Progress Log API!"}

@app.post("/logs", response_model=LogEntryResponse, tags=["Logs"])
async def create_log(log: LogEntry, user_id: str = Depends(get_current_user)):
    """
    Create a new log entry for the authenticated user.
    """
    try:
        appId = 'default-app-id' 
        collection_path = f"artifacts/{appId}/users/{user_id}/progress-logs"
        
        doc_ref = db.collection(collection_path).document()
        log_data = log.model_dump()
        doc_ref.set(log_data)
        
        return LogEntryResponse(id=doc_ref.id, **log_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create log: {e}")

@app.get("/logs", response_model=List[LogEntryResponse], tags=["Logs"])
async def get_logs(user_id: str = Depends(get_current_user)):
    """
    Retrieve all log entries for the authenticated user.
    """
    try:
        appId = 'default-app-id'
        collection_path = f"artifacts/{appId}/users/{user_id}/progress-logs"
        
        # Order by date descending to get the newest logs first
        docs = db.collection(collection_path).order_by("date", direction=firestore.Query.DESCENDING).stream()
        
        logs = []
        for doc in docs:
            log_data = doc.to_dict()
            logs.append(LogEntryResponse(id=doc.id, **log_data))
        
        return logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve logs: {e}")

@app.put("/logs/{log_id}", response_model=LogEntryResponse, tags=["Logs"])
async def update_log(log_id: str, log: LogEntry, user_id: str = Depends(get_current_user)):
    """
    Update an existing log entry for the authenticated user.
    """
    try:
        appId = 'default-app-id'
        doc_path = f"artifacts/{appId}/users/{user_id}/progress-logs/{log_id}"
        
        doc_ref = db.document(doc_path)
        if not doc_ref.get().exists:
             raise HTTPException(status_code=404, detail="Log not found")

        log_data = log.model_dump()
        doc_ref.update(log_data)
        
        # Return the updated data along with the ID
        updated_log_data = doc_ref.get().to_dict()
        return LogEntryResponse(id=log_id, **updated_log_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update log: {e}")

@app.delete("/logs/{log_id}", status_code=204, tags=["Logs"])
async def delete_log(log_id: str, user_id: str = Depends(get_current_user)):
    """
    Delete a specific log entry for the authenticated user.
    """
    try:
        appId = 'default-app-id'
        doc_path = f"artifacts/{appId}/users/{user_id}/progress-logs/{log_id}"
        
        doc_ref = db.document(doc_path)
        if not doc_ref.get().exists:
             raise HTTPException(status_code=404, detail="Log not found")

        doc_ref.delete()
        return None # FastAPI will return a 204 No Content response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete log: {e}")
