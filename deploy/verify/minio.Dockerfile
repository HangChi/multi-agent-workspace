# CI-only MinIO distribution. Production compose is deliberately unchanged.
# Official immutable release assets and their FULL GitHub release API SHA-256:
# https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z
# https://api.github.com/repos/minio/minio/releases/tags/RELEASE.2025-09-07T16-13-09Z
# LICENSE is preserved. Corresponding complete upstream source (AGPL-3.0):
# https://github.com/minio/minio/tree/07c3a429bfed433e49018cb0f78a52145d4bedeb
# No registry fallback, unchecked binary, credentials, or user data is embedded.
FROM python:3.12-slim
ARG TARGETARCH
RUN ARCH="$TARGETARCH" python - <<'PY'
import hashlib
import os
import pathlib
import urllib.request

arch = os.environ['ARCH']
if not arch:
    import platform
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
digests = {
    'amd64': '7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f',
    'arm64': '5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d',
}
release = 'RELEASE.2025-09-07T16-13-09Z'
assets = [
    (f'https://github.com/minio/minio/releases/download/{release}/minio.linux-{arch}.{release}',
     '/usr/local/bin/minio', digests[arch]),
    ('https://raw.githubusercontent.com/minio/minio/07c3a429bfed433e49018cb0f78a52145d4bedeb/LICENSE',
     '/usr/share/licenses/minio/LICENSE',
     '0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0'),
]
for url, target, expected in assets:
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(150_000_001)
    if len(data) > 150_000_000 or hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit('official MinIO asset checksum/size mismatch')
    path = pathlib.Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
pathlib.Path('/usr/local/bin/minio').chmod(0o755)
PY
RUN useradd --uid 10003 --no-create-home --shell /usr/sbin/nologin minio && \
    mkdir /data && chown minio:minio /data
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="07c3a429bfed433e49018cb0f78a52145d4bedeb" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
USER 10003:10003
EXPOSE 9000
HEALTHCHECK --interval=2s --timeout=3s --retries=30 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/minio/health/live',timeout=2)"]
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
