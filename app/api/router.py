from fastapi import APIRouter

from app.domains.auth.router.auth_router import router as auth_router
from app.domains.employees.router import router as employee_router, session_router, public_router
from app.domains.pipeline.router import router as pipeline_router
from app.domains.dashboard.router import router as dashboard_router
from app.domains.documents.router import router as documents_router
from app.domains.notices.router import router as notices_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(employee_router)
api_router.include_router(session_router)
api_router.include_router(public_router)
api_router.include_router(pipeline_router)
api_router.include_router(dashboard_router)
api_router.include_router(documents_router)
api_router.include_router(notices_router)
