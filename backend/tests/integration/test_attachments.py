"""附件集成测试（T042）：预签名上传/下载/大小与类型校验/元数据回填。

覆盖 FR-008 附件能力：
- 上传 201：返回预签名 PUT URL，可 PUT 文件体
- 超 20MB 拒绝 400 / 不支持类型拒绝 400
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


async def _upload_attachment(client, headers: dict, request_id: str, **overrides) -> dict:
    resp = await client.post(
        f"{REQUESTS_URL}/{request_id}/attachments",
        headers=headers,
        params=_attachment_payload(**overrides),
    )
    assert resp.status_code == 201
    return resp.json()


# ---------- 上传与预签名 PUT ----------


async def test_upload_returns_presigned_put_url(client, make_headers):
    """上传 → 201，返回可 PUT 的预签名 URL，元数据回填。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)

    body = await _upload_attachment(client, headers, req["id"], filename="notes.txt", size=64)
    att = body["attachments"][0]
    assert att["name"] == "notes.txt"
    assert att["size"] == 64
    assert att["mime"] == "text/plain"
    assert att["key"].startswith(f"attachments/{req['id']}/")
    assert "X-Amz-Signature" in att["presigned_put_url"]

    # 预签名 URL 可真实 PUT
    async with httpx.AsyncClient() as http:
        put_resp = await http.put(att["presigned_put_url"], content=b"hello world")
        assert put_resp.status_code == 200

    # 元数据已回填 requests.attachments
    resp = await client.get(f"{REQUESTS_URL}/{req['id']}", headers=headers)
    assert resp.status_code == 200
    stored = resp.json()["attachments"]
    assert len(stored) == 1
    assert stored[0]["key"] == att["key"]
    assert stored[0]["name"] == "notes.txt"


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
    body = await _upload_attachment(client, headers, req["id"], filename="dl.txt", size=32)
    att = body["attachments"][0]

    # 先 PUT 内容
    async with httpx.AsyncClient() as http:
        put_resp = await http.put(att["presigned_put_url"], content=b"download me")
        assert put_resp.status_code == 200

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
