"""CI/local-owned database bootstrap for the 003 deployment acceptance.

Executed on stdin inside this run's migrate image, after ``alembic upgrade head``
on an empty verification database. It never imports tests/conftest.py, never
resets or alters an existing schema, and never mints JWTs: the harness logs in
through the real authentication API afterwards. Credentials arrive only through
the environment; output contains booleans and counts only.

The 002 legacy bootstrap (``ci_fixture.py``) is deliberately not reused here: it
requires the exact pre-002 revision and asserts 002 migration history, which is
an independent feature gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from sqlalchemy import text

NAMESPACE = uuid.UUID("0f4d0f8e-5b9a-4d33-9a1c-6f6a4b2c7e11")
IDENTITIES = ("owner", "other", "admin")


def fixture_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def guard() -> None:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    if (os.environ.get("AW_VERIFY_EXTERNAL") != "1" or url.host != "postgres"
            or url.database != "aw_verify" or not os.environ.get("AW_VERIFY_USER_PASSWORD")):
        raise RuntimeError("external fixture requires this run's isolated verification database")


async def seed() -> dict:
    guard()
    async with SessionLocal() as db:
        count = (await db.execute(text("SELECT count(*) FROM users"))).scalar_one()
        if count != 0:
            raise RuntimeError("refusing to bootstrap a nonempty verification database")
        password_hash = hash_password(os.environ["AW_VERIFY_USER_PASSWORD"])
        for identity in IDENTITIES:
            await db.execute(text(
                "INSERT INTO users (id,email,password_hash,role,status) "
                "VALUES (:id,:email,:password,:role,'active')"
            ), {"id": fixture_id(identity), "email": f"{identity}@003-ci.example.com",
                "password": password_hash, "role": "admin" if identity == "admin" else "member"})
        await db.commit()
    return {"users": len(IDENTITIES)}


async def facts() -> dict:
    """Bounded read-back of the persistence invariants this acceptance asserts."""
    guard()
    secret = os.environ["AW_VERIFY_FIXTURE_SECRET"]
    async with SessionLocal() as db:
        invocations = (await db.execute(text(
            "SELECT count(*) FROM external_tool_invocations"))).scalar_one()
        unrecorded = (await db.execute(text(
            "SELECT count(*) FROM external_tool_invocations i WHERE i.step_id IS NULL "
            "OR NOT EXISTS (SELECT 1 FROM steps s WHERE s.id = i.step_id)"))).scalar_one()
        snapshots = (await db.execute(text(
            "SELECT count(*) FROM run_external_tool_snapshots"))).scalar_one()
        leaked = (await db.execute(text(
            "SELECT count(*) FROM external_tool_invocations "
            "WHERE error_summary LIKE :needle"), {"needle": f"%{secret}%"})).scalar_one()
        plaintext = (await db.execute(text(
            "SELECT count(*) FROM external_credentials "
            "WHERE ciphertext = :plain OR ciphertext LIKE :needle"),
            {"plain": secret, "needle": f"%{secret}%"})).scalar_one()
    if unrecorded or leaked or plaintext:
        raise RuntimeError("external invocation persistence invariants failed")
    return {"invocations": invocations, "invocations_without_step": unrecorded,
            "run_snapshots": snapshots, "leaked_credentials": leaked + plaintext}


async def main() -> None:
    try:
        result = await {"seed": seed, "facts": facts}[sys.argv[1]]()
    except Exception as exc:  # noqa: BLE001 - never disclose SQL bind arguments
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"passed": True, **result}))


if __name__ == "__main__":
    asyncio.run(main())
