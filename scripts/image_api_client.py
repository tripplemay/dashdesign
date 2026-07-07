#!/usr/bin/env python3
"""Shared OpenAI-compatible Image API helpers for DashDesign workflows."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import requests
from PIL import Image


IMAGE_API_TIMEOUT_SECONDS = 900


def image_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def image_api_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def write_image_response(data: dict[str, Any], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        first_image = data["data"][0]
    except (KeyError, IndexError, TypeError):
        return {
            "status": "error",
            "reason": "Image response did not include data[0]",
            "body": json.dumps(data, ensure_ascii=False)[:2000],
        }

    if first_image.get("b64_json"):
        output_path.write_bytes(base64.b64decode(first_image["b64_json"]))
    elif first_image.get("url"):
        image_response = requests.get(first_image["url"], timeout=IMAGE_API_TIMEOUT_SECONDS)
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)
    else:
        return {
            "status": "error",
            "reason": "Image response did not include b64_json or url",
            "body": json.dumps(data, ensure_ascii=False)[:2000],
        }

    with Image.open(output_path) as image:
        width, height = image.size
    return {
        "status": "generated",
        "output": str(output_path),
        "actual_px": f"{width}x{height}",
    }


def execute_image_generation(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    api_key = image_api_key()
    if not api_key:
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not set",
        }

    response = requests.post(
        f"{image_api_base_url()}/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=IMAGE_API_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        return {
            "status": "error",
            "status_code": response.status_code,
            "body": response.text[:2000],
        }

    return write_image_response(response.json(), output_path)


def execute_image_edit(
    source: Path,
    payload: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    api_key = image_api_key()
    if not api_key:
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not set",
        }

    with source.open("rb") as image_file:
        response = requests.post(
            f"{image_api_base_url()}/images/edits",
            headers={"Authorization": f"Bearer {api_key}"},
            data=payload,
            files={"image": image_file},
            timeout=IMAGE_API_TIMEOUT_SECONDS,
        )
    if response.status_code >= 400:
        return {
            "status": "error",
            "status_code": response.status_code,
            "body": response.text[:2000],
        }

    return write_image_response(response.json(), output_path)


def execute_image_request(
    source: Path,
    payload: dict[str, Any],
    output_path: Path,
    api_mode: str,
) -> dict[str, Any]:
    if api_mode == "edit":
        return execute_image_edit(source, payload, output_path)
    return execute_image_generation(payload, output_path)
