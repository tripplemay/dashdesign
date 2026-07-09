"""Unit tests for the document-store factory + adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from cloud.server.config import Settings  # noqa: E402
from cloud.server.docstore import (  # noqa: E402
    LocalDocumentStore,
    OSSDocumentStore,
    build_document_store,
    content_hash,
)


def _settings(tmp_path, doc_store="local", bucket="", endpoint=""):
    return Settings(
        db_url="sqlite://", doc_store=doc_store, doc_root=tmp_path / "docs",
        oss_bucket=bucket, oss_endpoint=endpoint, oss_prefix="baseline-docs/",
        admin_token="", admin_password="", seed_demo=False,
    )


def test_build_local_store_by_default(tmp_path):
    store = build_document_store(_settings(tmp_path))
    assert isinstance(store, LocalDocumentStore)


def test_local_store_writes_file_and_returns_uri(tmp_path):
    store = LocalDocumentStore(tmp_path / "docs")
    url = store.put("proj", "doc1", "note.txt", b"hello")
    assert url.startswith("file://")
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    # Platform-correct URI -> path (Windows file:///C:/... needs url2pathname).
    path = Path(url2pathname(urlparse(url).path))
    assert path.read_bytes() == b"hello"


def test_oss_store_missing_config_raises(tmp_path):
    with pytest.raises(RuntimeError):
        build_document_store(_settings(tmp_path, doc_store="oss"))


def test_oss_store_builds_key_and_uri_with_fake_client():
    class _FakeBucket:
        def __init__(self):
            self.puts = []

        def put_object(self, key, data):
            self.puts.append((key, data))

    client = _FakeBucket()
    store = OSSDocumentStore(client, "my-bucket", "baseline-docs/")
    url = store.put("projA", "docX", "招商.pdf", b"bytes")
    assert url == "oss://my-bucket/baseline-docs/projA/docX/招商.pdf"
    assert client.puts == [("baseline-docs/projA/docX/招商.pdf", b"bytes")]


def test_content_hash_is_sha256():
    assert len(content_hash(b"x")) == 64
