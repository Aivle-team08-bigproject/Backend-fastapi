from fastapi import APIRouter

from app.domains.auth.router.auth_router import router as auth_router
from app.domains.employees.router import router as employee_router, session_router
from app.domains.automation.router.requirements_analysis_router import (
    router as requirements_analysis_router,
)
from app.domains.pipeline.router import router as pipeline_router
from app.domains.dashboard.router import router as dashboard_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(employee_router)
api_router.include_router(session_router)
api_router.include_router(requirements_analysis_router)
api_router.include_router(pipeline_router)
api_router.include_router(dashboard_router)
