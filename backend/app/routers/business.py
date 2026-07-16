from fastapi import APIRouter, Depends
from app.auth.dependencies import get_current_user
from app.models import User

router = APIRouter(prefix="/business", tags=["business"])

@router.get("/hello")
async def hello(user: User = Depends(get_current_user)):
    return {"message": f"Hello {user.email}", "userId": user.id}