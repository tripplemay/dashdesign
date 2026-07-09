"""Server configuration, sourced from environment variables.

Local/dev defaults run entirely on SQLite + local-filesystem document storage so
the whole backend is runnable and testable without any managed service. Prod
sets ``BASELINE_DB_URL`` to a managed Postgres DSN and ``BASELINE_DOC_STORE`` to
an object-storage backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


@dataclass(frozen=True)
class Settings:
    db_url: str
    doc_store: str  # "local" | "oss"
    doc_root: Path
    oss_bucket: str
    oss_prefix: str
    admin_token: str  # optional bootstrap token -> a global-admin principal
    seed_demo: bool

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")


def load_settings() -> Settings:
    return Settings(
        db_url=_env("BASELINE_DB_URL", "sqlite:///./baseline_cloud.db"),
        doc_store=_env("BASELINE_DOC_STORE", "local").lower(),
        doc_root=Path(_env("BASELINE_DOC_ROOT", "./baseline_documents")).expanduser(),
        oss_bucket=_env("BASELINE_OSS_BUCKET"),
        oss_prefix=_env("BASELINE_OSS_PREFIX", "baseline-docs/"),
        admin_token=_env("BASELINE_ADMIN_TOKEN"),
        seed_demo=_env("BASELINE_SEED_DEMO", "").lower() in ("1", "true", "yes"),
    )
