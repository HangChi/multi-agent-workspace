"""Real sandbox -> MinIO -> signed GET smoke (not an API ownership test).

From the repository root, after starting docker-compose.verify.yml:
  docker exec -i aw-002-verify-worker-1 python - < deploy/verify/s4_artifact_runtime.py
Runs only against the dedicated aw_verify database configuration. Does not use
the database, provision identities, emit signed URLs, or retain uploaded objects.
"""
import hashlib
import json
import urllib.request
import uuid

from app.core.config import settings
from app.executors.sandbox_client import SandboxRun
from app.services import artifact_service


def main() -> None:
    assert settings.database_url.rsplit('/', 1)[-1] == 'aw_verify'
    assert settings.minio_endpoint == 'minio:9000'
    eid, rid = str(uuid.uuid4()), str(uuid.uuid4())
    key = None
    try:
        code = (
            "import csv\n"
            "with open('/workspace/output/sales.csv','w',newline='') as f:\n"
            " w=csv.writer(f); w.writerow(['day','sales']); w.writerows((i,i*10) for i in range(1,21))\n"
            "print('sales.csv: 20 rows')"
        )
        with SandboxRun(execution_id=eid, language='python', code=code, timeout_s=5) as run:
            outcome = run.execute()
            assert outcome.exit_code == 0, 'CSV sandbox failed'
            files = list(run.iter_output_files())
            assert len(files) == 1
            item = files[0]
            key, digest = artifact_service.put_bytes(rid, eid, item.name, item.data, mime='text/csv')
            with urllib.request.urlopen(artifact_service.presign_get(key), timeout=10) as response:
                downloaded = response.read()
            assert downloaded == item.data
            assert hashlib.sha256(downloaded).hexdigest() == digest
            assert len(downloaded.splitlines()) == 21
            print(json.dumps({
                'probe': 'real sandbox -> real MinIO -> presigned HTTP GET',
                'execution_id': eid, 'passed': True, 'rows': 20,
                'size_bytes': len(downloaded), 'sha256': digest,
            }))
    finally:
        if key is not None:
            artifact_service._minio_client().remove_object(settings.minio_bucket, key)


if __name__ == '__main__':
    main()
