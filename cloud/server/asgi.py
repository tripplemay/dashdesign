"""ASGI entrypoints.

``app`` is the ASGI application for uvicorn (``uvicorn cloud.server.asgi:app``).
``handler`` is the serverless adapter (Aliyun FC / Tencent SCF via API Gateway),
available when ``mangum`` is installed in the deploy image.
"""

from __future__ import annotations

from cloud.server.app import create_app

app = create_app()

try:  # pragma: no cover - only exercised in the serverless deploy image
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
