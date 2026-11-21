"""e2e 的 API 夹具层。

约定：**数据一律经 API 造，浏览器只负责断言页面**。
这样失败时能分清「后端行为变了」和「页面显示错了」——把两者混在一起查，
正是 011 那次把两段式拉取的时序误判成 app bug 的原因。

所有方法都直接吃 token，不缓存登录态：用例之间不共享副作用。
"""

from __future__ import annotations

from typing import Any

import httpx

PASSWORD = "TestPass123!"


class ApiClient:
    def __init__(self, base: str) -> None:
        self.base = base
        self.http = httpx.Client(base_url=f"{base}/api/v1", timeout=30.0)

    def close(self) -> None:
        self.http.close()

    # ---------- 认证 ----------

    def register(self, email: str, invite_code: str, password: str = PASSWORD) -> dict[str, Any]:
        resp = self.http.post(
            "/auth/register",
            json={"email": email, "password": password, "invite_code": invite_code},
        )
        resp.raise_for_status()
        return resp.json()

    def login(self, email: str, password: str = PASSWORD) -> dict[str, Any]:
        resp = self.http.post("/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        return resp.json()

    # ---------- 通用 ----------

    def post(
        self, token: str, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        return self.http.post(path, json=json, headers=_auth(token))

    def get(self, token: str, path: str, **params: Any) -> httpx.Response:
        return self.http.get(path, headers=_auth(token), params=params)

    def patch(
        self, token: str, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        return self.http.patch(path, json=json, headers=_auth(token))

    def delete(self, token: str, path: str) -> httpx.Response:
        return self.http.delete(path, headers=_auth(token))

    # ---------- 领域快捷方法（断言状态码，失败信息带响应体） ----------

    def create_agent(self, token: str, name: str, **overrides: Any) -> dict[str, Any]:
        payload = {
            "name": name,
            "system_prompt": "你是 e2e 验收助手，请简短回答。",
            "model": "Deepseek-V4.1-Flash",
            "max_steps": 4,
            "cost_limit_cents": 10000,
            "tools": [],
        }
        payload.update(overrides)
        return _expect(self.post(token, "/agents", payload), 201, "建 Agent")

    def create_project(self, token: str, name: str) -> dict[str, Any]:
        return _expect(self.post(token, "/projects", {"name": name}), 201, "建项目")

    def create_request(
        self, token: str, title: str, project_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if project_id:
            payload["project_id"] = project_id
        return _expect(self.post(token, "/requests", payload), 201, "建需求")

    def add_dependency(self, token: str, request_id: str, dependency_id: str) -> httpx.Response:
        return self.post(
            token,
            f"/requests/{request_id}/dependencies",
            {"dependency_id": dependency_id},
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expect(resp: httpx.Response, status: int, what: str) -> dict[str, Any]:
    if resp.status_code != status:
        raise AssertionError(f"{what}失败: {resp.status_code} {resp.text[:300]}")
    return resp.json()
