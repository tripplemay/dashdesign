#!/usr/bin/env python3
"""Shared OpenAI-compatible Image API helpers for DashDesign workflows."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

import requests
from PIL import Image


IMAGE_API_TIMEOUT_SECONDS = 900


def image_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def image_api_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def redact(value: object) -> str:
    text = str(value)
    key = image_api_key()
    if key:
        text = text.replace(key, "[redacted]")
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"sk-[\w-]+", "[redacted]", text)
    # Download URLs may contain signed credentials; never retain them in errors.
    return re.sub(r"https?://\S+", "[url]", text)[:800]


def sanitized_error(exc: Exception) -> dict[str, Any]:
    uncertain = isinstance(exc, (requests.Timeout, requests.ConnectionError))
    return {"error_type": type(exc).__name__, "reason": redact(exc), "execution_uncertain": uncertain}


def response_error(response, model: str) -> dict[str, Any]:
    error: Any = {}
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error", {})
    except ValueError:
        pass
    if not isinstance(error, dict):
        error = {"message": error}
    return {
        "status": "error", "status_code": response.status_code,
        "model": model,
        "error_type": redact(error.get("type") or error.get("code") or "http_error"),
        "reason": redact(error.get("message") or f"Image API HTTP {response.status_code}"),
        "request_id": redact(response.headers.get("x-request-id", response.headers.get("request-id", ""))),
        "execution_uncertain": response.status_code >= 500,
    }


def write_image_response(data: dict[str, Any], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".part")
    try:
        first_image = data["data"][0]
        if not isinstance(first_image, dict):
            raise ValueError("Image response did not include an image object")
        if first_image.get("b64_json"):
            content = base64.b64decode(first_image["b64_json"], validate=True)
        elif first_image.get("url"):
            response = requests.get(first_image["url"], timeout=IMAGE_API_TIMEOUT_SECONDS)
            response.raise_for_status()
            content = response.content
        else:
            raise ValueError("Image response did not include b64_json or url")
        temporary.write_bytes(content)
        with Image.open(temporary) as image:
            width, height = image.size
            image.verify()
        temporary.replace(output_path)
        return {"status": "generated", "output": str(output_path), "actual_px": f"{width}x{height}"}
    except Exception as exc:
        return {"status": "error", **sanitized_error(exc), "execution_uncertain": True}
    finally:
        temporary.unlink(missing_ok=True)


def execute_image_generation(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    api_key = image_api_key()
    if not api_key:
        return {"status": "error", "error_type": "missing_api_key", "reason": "OPENAI_API_KEY is not set"}
    try:
        response = requests.post(
            f"{image_api_base_url()}/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=IMAGE_API_TIMEOUT_SECONDS,
        )
        return _read_response(response, output_path, str(payload.get("model", "")))
    except Exception as exc:
        return {"status": "error", "model": payload.get("model"), **sanitized_error(exc)}


def execute_image_edit(
    source: Path,
    payload: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    api_key = image_api_key()
    if not api_key:
        return {"status": "error", "error_type": "missing_api_key", "reason": "OPENAI_API_KEY is not set"}
    try:
        with source.open("rb") as image_file:
            response = requests.post(
                f"{image_api_base_url()}/images/edits",
                headers={"Authorization": f"Bearer {api_key}"}, data=payload,
                files={"image": image_file}, timeout=IMAGE_API_TIMEOUT_SECONDS,
            )
        return _read_response(response, output_path, str(payload.get("model", "")))
    except Exception as exc:
        return {"status": "error", "model": payload.get("model"), **sanitized_error(exc)}


def _read_response(response, output_path: Path, model: str) -> dict[str, Any]:
    if response.status_code >= 400:
        return response_error(response, model)
    try:
        data = response.json()
    except ValueError as exc:
        return {"status": "error", "model": model, **sanitized_error(exc), "execution_uncertain": True}
    return {**write_image_response(data, output_path), "model": model,
            "request_id": redact(response.headers.get("x-request-id", ""))}


def execute_image_request(
    source: Path,
    payload: dict[str, Any],
    output_path: Path,
    api_mode: str,
) -> dict[str, Any]:
    if api_mode == "edit":
        return execute_image_edit(source, payload, output_path)
    return execute_image_generation(payload, output_path)
