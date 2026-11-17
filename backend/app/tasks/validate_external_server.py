"""Worker task for bounded external MCP server validation."""

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.external_tool import ExternalToolServer
from app.services.external_validation_service import validate_server
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.validate_external_server.validate_external_server")
def validate_external_server(server_id: str, requested_by: str) -> str:
    async def _run() -> str:
        async with SessionLocal() as db:
            server = await db.scalar(
                select(ExternalToolServer).where(ExternalToolServer.id == server_id)
            )
            if server is None:
                return "not_found"
            result = await validate_server(db, server=server, requested_by=requested_by)
            return result.status

    return asyncio.run(_run())
