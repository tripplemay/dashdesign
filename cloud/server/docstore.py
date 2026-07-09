"""Document storage port + adapters.

Uploaded source documents (PDF/DOCX/TXT) are stored out-of-band from the JSON
baseline. The default adapter writes to the local filesystem (dev / single-node
serverless with a mounted volume); the OSS adapter is the production path on a
domestic object store. Per the Phase B contract, originals need not leave the
operator's machine at all — only extracted text is required for merge — so this
is optional infrastructure.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocumentStore(Protocol):
    def put(self, baseline_id: str, doc_id: str, filename: str, data: bytes) -> str:
        """Persist ``data`` and return a stable storage URL/URI for it."""
        ...


class LocalDocumentStore:
    """Filesystem-backed document store rooted at ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def put(self, baseline_id: str, doc_id: str, filename: str, data: bytes) -> str:
        # Namespaced by project + doc id; original filename kept as the leaf.
        safe_name = Path(filename).name or "document"
        dest_dir = self.root / baseline_id / doc_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        dest.write_bytes(data)
        return dest.resolve().as_uri()


class OSSDocumentStore:
    """Object-storage adapter (Aliyun OSS / Tencent COS).

    Wraps a vendor bucket client exposing ``put_object(key, data)``. The client
    is injected so the vendor SDK is only imported in the deploy image, never by
    tests or the desktop client.
    """

    def __init__(self, bucket_client, bucket: str, prefix: str = "baseline-docs/") -> None:
        self._client = bucket_client
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/"

    def put(self, baseline_id: str, doc_id: str, filename: str, data: bytes) -> str:
        key = f"{self._prefix}{baseline_id}/{doc_id}/{Path(filename).name or 'document'}"
        self._client.put_object(key, data)
        return f"oss://{self._bucket}/{key}"
