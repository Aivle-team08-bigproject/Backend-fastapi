from fastapi import APIRouter

from app.domains.auth.router.auth_router import router as auth_router
from app.domains.employees.router.employee_router import router as employee_router
from app.domains.employees.router.session_admin_router import router as session_admin_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(employee_router)
api_router.include_router(session_admin_router)
