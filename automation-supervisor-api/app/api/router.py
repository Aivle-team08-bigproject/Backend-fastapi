from fastapi import APIRouter

from app.api.supervisor import router as supervisor_router

api_router = APIRouter()
api_router.include_router(supervisor_router)
