"""附件集成测试（T042）：表单上传/下载/大小与类型校验/元数据回填。

覆盖 FR-008 附件能力：
- 上传 201：返回 POST Policy 凭据，可表单 POST 文件体
- 超 20MB 拒绝 400 / 不支持类型拒绝 400
- **对象存储层强制大小上限**：即便绕过应用层校验，超限上传也由 MinIO 拒绝
- 下载 302 → 预签名 GET URL
- 附件元数据回填 requests.attachments
"""

import uuid
from urllib.parse import quote

import httpx

REQUESTS_URL = "/api/v1/requests"


def _request_payload(**overrides: str) -> dict:
    payload = {"title": "attachment test", "priority": "normal"}
    payload.update(overrides)
    return payload


async def _create_request(client, headers: dict) -> dict:
    resp = await client.post(REQUESTS_URL, headers=headers, json=_request_payload())
    assert resp.status_code == 201
    return resp.json()


def _attachment_payload(**overrides) -> dict:
    payload = {
        "filename": f"file-{uuid.uuid4().hex[:8]}.txt",
        "size": 1024,
        "mime_type": "text/plain",
    }
    payload.update(overrides)
    return payload


async def _upload_attachment(
    client, headers: dict, request_id: str, **overrides
) -> dict:
    resp = await client.post(
        f"{REQUESTS_URL}/{request_id}/attachments",
        headers=headers,
        params=_attachment_payload(**overrides),
    )
    assert resp.status_code == 201
    return resp.json()


async def _post_object(att: dict, content: bytes) -> httpx.Response:
    """按 POST Policy 凭据真实上传一个对象。"""
    async with httpx.AsyncClient() as http:
        return await http.post(
            att["upload_url"],
            data=att["upload_fields"],
            files={"file": ("blob", content)},
        )


# ---------- 上传与 POST Policy ----------


async def test_upload_returns_policy_and_can_post(client, make_headers):
    """上传 → 201，返回可表单 POST 的 policy 凭据，元数据回填。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)

    body = await _upload_attachment(
        client, headers, req["id"], filename="notes.txt", size=64
    )
    att = body["attachments"][0]
    assert att["name"] == "notes.txt"
    assert att["size"] == 64
    assert att["mime"] == "text/plain"
    assert att["key"].startswith(f"attachments/{req['id']}/")
    # policy 凭据：目标地址 + 签名字段（且 key 必须回传，否则存储端拒绝）
    assert att["upload_url"].endswith("/agent-workbench")
    assert "policy" in att["upload_fields"]
    assert att["upload_fields"]["key"] == att["key"]

    # 凭据可真实上传
    put_resp = await _post_object(att, b"hello world")
    assert put_resp.status_code in (200, 204), put_resp.text[:200]

    # 元数据已回填 requests.attachments
    resp = await client.get(f"{REQUESTS_URL}/{req['id']}", headers=headers)
    assert resp.status_code == 200
    stored = resp.json()["attachments"]
    assert len(stored) == 1
    assert stored[0]["key"] == att["key"]
    assert stored[0]["name"] == "notes.txt"


async def test_object_store_enforces_size_limit(client, make_headers):
    """核心回归：绕过应用层校验后，超限上传必须由**对象存储**拒绝。

    修复前的漏洞：服务端只校验客户端声明的 `size`，预签名 PUT 无任何长度条件，
    声明 1 字节再上传任意大小都能成功。现在 policy 带 content-length-range，
    超限时 MinIO 直接返回 400 EntityTooLarge。
    """
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)

    # 声明一个很小的 size（应用层校验通过），实际传超限内容
    att = (
        await _upload_attachment(
            client,
            headers,
            req["id"],
            filename="sneaky.bin",
            size=10,
            mime_type="application/zip",
        )
    )["attachments"][0]

    too_big = 21 * 1024 * 1024  # 超过 20MB 上限
    resp = await _post_object(att, b"x" * too_big)
    assert resp.status_code == 400, f"超限上传本应被存储端拒绝，实际 {resp.status_code}"
    assert "EntityTooLarge" in resp.text or "too large" in resp.text.lower()


async def test_upload_to_other_users_request_forbidden(client, auth_headers):
    """给他人需求传附件 → 403。"""
    owner_headers, _ = await auth_headers(role="member")
    req = await _create_request(client, owner_headers)

    other_headers, _ = await auth_headers(role="member")
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/attachments",
        headers=other_headers,
        params=_attachment_payload(),
    )
    assert resp.status_code == 403


# ---------- 大小/类型校验 ----------


async def test_upload_over_20mb_rejected(client, make_headers):
    """超 20MB → 400。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    too_big = 20 * 1024 * 1024 + 1
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/attachments",
        headers=headers,
        params=_attachment_payload(size=too_big),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_upload_disallowed_type_rejected(client, make_headers):
    """白名单外的 mime → 400。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/attachments",
        headers=headers,
        params=_attachment_payload(mime_type="application/x-msdownload"),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


# ---------- 下载 ----------


async def test_download_returns_presigned_get_url(client, make_headers):
    """下载 → 302 到预签名 GET URL，可真实 GET 回内容。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    body = await _upload_attachment(
        client, headers, req["id"], filename="dl.txt", size=32
    )
    att = body["attachments"][0]

    # 先上传内容
    put_resp = await _post_object(att, b"download me")
    assert put_resp.status_code in (200, 204), put_resp.text[:200]

    # 下载端点 → 302 预签名 GET
    key_q = quote(att["key"], safe="")
    resp = await client.get(
        f"{REQUESTS_URL}/{req['id']}/attachments/{key_q}",
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 302
    get_url = resp.headers["location"]
    assert "X-Amz-Signature" in get_url

    # 浏览器链接带不了 Bearer 头：同一端点 + redirect=false 取回 URL
    resolved = await client.get(
        f"{REQUESTS_URL}/{req['id']}/attachments/{key_q}",
        headers=headers,
        params={"redirect": "false"},
    )
    assert resolved.status_code == 200
    assert "X-Amz-Signature" in resolved.json()["url"]

    # 预签名 GET URL 可真实取回内容
    async with httpx.AsyncClient() as http:
        get_resp = await http.get(get_url)
    assert get_resp.status_code == 200
    assert get_resp.content == b"download me"


async def test_download_attachment_not_found(client, make_headers):
    """下载不存在的附件 → 404。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    resp = await client.get(
        f"{REQUESTS_URL}/{req['id']}/attachments/nonexistent-key",
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 404
