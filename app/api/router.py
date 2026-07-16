from fastapi import APIRouter

from app.api.requirements import router as requirements_router
from app.api.tasks import router as tasks_router

api_router = APIRouter()
api_router.include_router(requirements_router)
api_router.include_router(tasks_router)
