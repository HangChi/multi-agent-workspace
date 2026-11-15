"""CI-owned database bootstrap + real 001->002 migration/history verification.

Executed on stdin inside this run's migrate image, before API/worker startup.
Never import tests/conftest.py, reset a schema, alter an existing user or mint JWTs.
The empty database and exact pre-002 revision are checked before creating users.
Credentials arrive only via environment; output contains booleans/counts/UUIDs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password

NAMESPACE = uuid.UUID("43178b0f-8d83-46ec-ad3b-afd26ed5fd1a")


def fixture_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def guard() -> None:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    if (os.environ.get("AW_VERIFY_CI") != "1" or url.host != "postgres"
            or url.database != "aw_verify" or not os.environ.get("AW_VERIFY_USER_PASSWORD")):
        raise RuntimeError("CI fixture requires this run's isolated verification database")


async def seed() -> dict:
    guard()
    async with SessionLocal() as db:
        revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        if revision != "7c1e9a4b2d30":
            raise RuntimeError("legacy fixture requires exact pre-002 revision")
        count = (await db.execute(text("SELECT count(*) FROM users"))).scalar_one()
        if count != 0:
            raise RuntimeError("refusing to bootstrap a nonempty verification database")
        password_hash = hash_password(os.environ["AW_VERIFY_USER_PASSWORD"])
        for identity in ("owner", "other", "admin"):
            await db.execute(text(
                "INSERT INTO users (id,email,password_hash,role,status) "
                "VALUES (:id,:email,:password,:role,'active')"
            ), {"id": fixture_id(identity), "email": f"{identity}@002-ci.example.com",
                "password": password_hash, "role": "admin" if identity == "admin" else "member"})
        tools = ["web_fetch", "shell", "code_interpreter", "http", "calculate"]
        await db.execute(text(
            "INSERT INTO agents (id,owner_id,name,system_prompt,model,temperature,max_steps,"
            "max_run_seconds,tools,cost_limit_cents,status) VALUES "
            "(:id,:owner,'002 legacy fixture','historical immutable prompt','gpt-4o-mini',0,8,"
            "180,CAST(:tools AS json),10000,'active')"
        ), {"id": fixture_id("agent"), "owner": fixture_id("owner"), "tools": json.dumps(tools)})
        await db.execute(text(
            "INSERT INTO requests (id,owner_id,title,priority,status,attachments) "
            "VALUES (:id,:owner,'002 historical request','normal','completed','[]'::json)"
        ), {"id": fixture_id("request"), "owner": fixture_id("owner")})
        await db.execute(text(
            "INSERT INTO runs (id,request_id,owner_id,assignee_type,assignee_id,strategy,status,"
            "current_step,cost_cents,sequence,result) VALUES "
            "(:id,:request,:owner,'agent',:agent,'direct','completed',1,0,1,CAST(:result AS json))"
        ), {"id": fixture_id("run"), "request": fixture_id("request"), "owner": fixture_id("owner"),
            "agent": fixture_id("agent"), "result": json.dumps({"tools_snapshot": tools})})
        await db.execute(text(
            "INSERT INTO steps (id,run_id,seq,kind,tool_name,duration_ms,cost_cents,status,output) "
            "VALUES (:id,:run,1,'tool','web_fetch',1,0,'completed',CAST(:output AS json))"
        ), {"id": fixture_id("step"), "run": fixture_id("run"),
            "output": json.dumps({"historical": True, "tools_snapshot": tools})})
        await db.commit()
    return {"legacy_seeded": True, "users": 3}


async def verify() -> dict:
    guard()
    async with SessionLocal() as db:
        tools = (await db.execute(text("SELECT tools FROM agents WHERE id=:id"),
                                 {"id": fixture_id("agent")})).scalar_one()
        if tools != ["read_url", "run_shell", "run_python", "calculate"]:
            raise RuntimeError("real migration did not map legacy tools")
        row = (await db.execute(text("SELECT tool_name,output FROM steps WHERE id=:id"),
                                {"id": fixture_id("step")})).one()
        snapshot = ["web_fetch", "shell", "code_interpreter", "http", "calculate"]
        if row.tool_name != "web_fetch" or row.output["tools_snapshot"] != snapshot:
            raise RuntimeError("migration rewrote historical tool evidence")
        run = (await db.execute(text(
            "SELECT result,tool_grants_snapshot IS NULL AS grants_snapshot_is_sql_null "
            "FROM runs WHERE id=:id"
        ), {"id": fixture_id("run")})).one()
        if run.result["tools_snapshot"] != snapshot:
            raise RuntimeError("migration rewrote historical configuration snapshot")
        if not run.grants_snapshot_is_sql_null:
            raise RuntimeError("migration fabricated historical tool grants")
        catalog = (await db.execute(text("SELECT count(*) FROM tools WHERE enabled=true"))).scalar_one()
        if catalog != 5:
            raise RuntimeError("migration failed to seed five tools")
    return {"migration_mapped": True, "history_preserved": True,
            "legacy_grants_snapshot_sql_null": True,
            "catalog_count": catalog, "historical_run_id": fixture_id("run")}


async def facts() -> dict:
    guard()
    async with SessionLocal() as db:
        executions = (await db.execute(text("SELECT id FROM sandbox_executions"))).scalars().all()
        missing = (await db.execute(text(
            "SELECT count(*) FROM sandbox_executions e WHERE NOT EXISTS "
            "(SELECT 1 FROM audit_logs a WHERE a.target_id=e.id AND a.action='sandbox_exec')"
        ))).scalar_one()
        mismatched = (await db.execute(text(
            "SELECT count(*) FROM egress_accesses a JOIN sandbox_executions e ON e.id=a.execution_id "
            "JOIN runs r ON r.id=e.run_id WHERE a.run_id<>e.run_id OR a.agent_id<>e.agent_id OR a.user_id<>r.owner_id"
        ))).scalar_one()
        if missing or mismatched:
            raise RuntimeError("execution/audit/egress persistence invariants failed")
    return {"execution_ids": [str(eid) for eid in executions], "unaudited_executions": missing,
            "mismatched_egress_subjects": mismatched}


async def main() -> None:
    try:
        result = await {"seed": seed, "verify": verify, "facts": facts}[sys.argv[1]]()
    except Exception as exc:  # noqa: BLE001 - never disclose SQL bind arguments
        # SQL/driver exceptions may contain bind arguments, hashes or credentials.
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"passed": True, **result}))


if __name__ == "__main__":
    asyncio.run(main())
