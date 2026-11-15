"""Real TLS/socket fixture checks, not the Worker/API deployment acceptance gate."""

import ssl
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.executors.mcp_client import MCPClient, MCPToolError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from deploy.verify.mcp_fixture_server import Handler


@pytest.fixture
def https_fixture(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "fixture.pem", tmp_path / "fixture.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.scenario = "normal"
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/mcp", ssl.create_default_context(cafile=str(cert_path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


async def test_real_https_fixture_handshake_discovery_success_and_tool_error(https_fixture):
    endpoint, context = https_fixture
    async with (
        httpx.AsyncClient(verify=context, trust_env=False) as http,
        MCPClient(endpoint, http_client=http) as client,
    ):
        await client.initialize()
        tools = await client.list_tools()
        assert {tool.name for tool in tools} >= {"search", "error_search"}
        result = await client.call_tool("search", {"query": "owned fixture"})
        assert result["isError"] is False
        with pytest.raises(MCPToolError):
            await client.call_tool("error_search", {})
