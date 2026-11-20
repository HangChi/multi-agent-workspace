"""版本号三处一致（`app.__version__` / pyproject / 前端 package.json）。

为什么值得一个测试：版本号是「改一处、忘两处」的典型。症状不会报错，
只会让 OpenAPI 文档、Sentry release、前端包版本各说各话 ——
排查线上问题时，你会先怀疑「是不是发错版本了」。

后端内部已经收敛到单一来源（`app.__version__`），但 pyproject 与
frontend/package.json 属于不同构建系统，运行时无法共享，只能靠这条断言兜住。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from app import __version__

_ROOT = Path(__file__).resolve().parents[3]


def test_backend_version_matches_pyproject() -> None:
    pyproject = tomllib.loads(
        (_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert __version__ == pyproject["project"]["version"]


def test_frontend_version_matches_backend() -> None:
    package = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    assert __version__ == package["version"], (
        "前后端版本不一致：发版时两边要一起改，否则 Sentry 的 release 与前端包对不上"
    )
