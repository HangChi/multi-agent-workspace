"""引导脚本：创建首个 admin 用户 + 生成管理员邀请码（T024）。

用法：
    .venv/bin/python scripts/bootstrap.py --email admin@x.com --password '***' --invite-code ADMIN-123 --invite-role member --invite-max-uses 10

输出到 stdout（含生成的邀请码），供 quickstart S1 使用。
"""

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.invite_code import InviteCode
from app.models.user import User


async def _run(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        # 1. 创建/更新 admin 用户
        from sqlalchemy import select

        existing = (
            await db.execute(select(User).where(User.email == args.email))
        ).scalar_one_or_none()
        if existing is None:
            admin = User(
                email=args.email,
                password_hash=hash_password(args.password),
                role="admin",
                status="active",
            )
            db.add(admin)
            await db.flush()
            print(f"admin 用户已创建: {args.email} (role=admin)")
        else:
            admin = existing
            admin.role = "admin"
            admin.status = "active"
            await db.flush()
            print(f"admin 用户已存在，已提升为 admin: {args.email}")

        # 2. 创建邀请码
        code = args.invite_code or secrets.token_urlsafe(8).upper()
        invite = InviteCode(
            code=code,
            role=args.invite_role,
            max_uses=args.invite_max_uses,
            used_count=0,
            expires_at=None,
            created_by=admin.id,
        )
        db.add(invite)
        await db.commit()
        print(
            f"邀请码: {code} (role={args.invite_role}, max_uses={args.invite_max_uses})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 admin 用户与邀请码")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="AdminPass123!")
    parser.add_argument("--invite-code", default="")
    parser.add_argument("--invite-role", default="member", choices=["member", "viewer"])
    parser.add_argument("--invite-max-uses", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
