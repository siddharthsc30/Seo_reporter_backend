from typing import Optional
from fastapi import Header, HTTPException, status, Depends
from pydantic import BaseModel
from supabase_client import verify_supabase_token

class UserContext(BaseModel):
    id: str
    email: str
    role: str = "authenticated"
    access_token: str
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None

async def get_current_user(authorization: Optional[str] = Header(None)) -> UserContext:
    """
    FastAPI dependency that enforces Supabase authentication via Bearer token.
    Raises HTTP 401 if missing or invalid.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Please sign in via Supabase.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    user_data = await verify_supabase_token(token)
    if not user_data or "id" not in user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase authentication session.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    meta = user_data.get("user_metadata", {}) or {}
    return UserContext(
        id=user_data["id"],
        email=user_data.get("email", ""),
        role=user_data.get("role", "authenticated"),
        access_token=token,
        full_name=meta.get("full_name") or meta.get("name") or user_data.get("email", "").split("@")[0],
        avatar_url=meta.get("avatar_url")
    )

async def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[UserContext]:
    """
    FastAPI dependency for endpoints that work for both authenticated and guest users.
    Returns UserContext if valid Bearer token provided, None otherwise.
    """
    if not authorization:
        return None
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    
    token = parts[1]
    user_data = await verify_supabase_token(token)
    if not user_data or "id" not in user_data:
        return None
    
    meta = user_data.get("user_metadata", {}) or {}
    return UserContext(
        id=user_data["id"],
        email=user_data.get("email", ""),
        role=user_data.get("role", "authenticated"),
        access_token=token,
        full_name=meta.get("full_name") or meta.get("name"),
        avatar_url=meta.get("avatar_url")
    )
