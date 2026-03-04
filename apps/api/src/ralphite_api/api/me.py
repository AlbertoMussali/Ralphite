from fastapi import APIRouter, Depends

from ralphite_api.api.deps import get_current_user
from ralphite_api.models import User
from ralphite_api.schemas.auth import UserResponse

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        settings_json=user.settings_json,
    )
