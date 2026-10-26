from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from auth import get_current_user, UserContext
from supabase_client import get_authenticated_client

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None

@router.get("/me")
async def get_current_user_profile(user: UserContext = Depends(get_current_user)):
    """
    Validates token and returns current user details from Supabase profiles table.
    If the profile row doesn't exist yet, it initializes it.
    """
    client = get_authenticated_client(user.access_token)
    profile = None
    
    try:
        res = client.table("profiles").select("*").eq("id", user.id).execute()
        if res.data and len(res.data) > 0:
            profile = res.data[0]
        else:
            # Create profile row if not existing
            now_str = datetime.utcnow().isoformat()
            new_profile = {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name or user.email.split("@")[0].title(),
                "role": "SEO Strategist",
                "created_at": now_str,
                "updated_at": now_str
            }
            create_res = client.table("profiles").insert(new_profile).execute()
            if create_res.data and len(create_res.data) > 0:
                profile = create_res.data[0]
            else:
                profile = new_profile
    except Exception as e:
        print(f"Notice: Supabase profile sync: {e}")
        profile = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": "SEO Strategist"
        }

    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "fullName": profile.get("full_name") or user.full_name or user.email.split("@")[0],
            "role": profile.get("role") or "SEO Strategist",
            "avatarUrl": profile.get("avatar_url")
        }
    }

@router.put("/profile")
async def update_user_profile(
    payload: UpdateProfileRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Updates the authenticated user's profile details (full name, avatar, role).
    Persists changes to the Supabase 'profiles' table.
    Returns: Updated profile object.
    """
    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().isoformat()
    
    update_data = {"updated_at": now_str}
    if payload.full_name is not None:
        update_data["full_name"] = payload.full_name
    if payload.avatar_url is not None:
        update_data["avatar_url"] = payload.avatar_url
    if payload.role is not None:
        update_data["role"] = payload.role

    try:
        res = client.table("profiles").update(update_data).eq("id", user.id).execute()
        return {
            "success": True,
            "profile": res.data[0] if res.data else update_data
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile in Supabase: {str(e)}"
        )
