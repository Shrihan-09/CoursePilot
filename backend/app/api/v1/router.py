"""v1 API router aggregation.

Versioned from the start. Rutgers requirement structures and term formats will
force breaking response changes; a version prefix makes that survivable.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import admin, explanations, health, meta

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(meta.router)
api_router.include_router(explanations.router)
api_router.include_router(admin.router)

# Future routers land here as they are implemented:
#   courses.router      — course search / lookup
#   students.router     — profile, completed courses
#   audit.router        — degree audit
#   planning.router     — plan generation + validation
#   schedule.router     — schedule generation
