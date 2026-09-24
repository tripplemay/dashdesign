"""Advisory model discovery; never submits a generation request."""

import requests


def check_image_model(base_url: str, api_key: str, model: str) -> str:
    if not all(value.strip() for value in (base_url, api_key, model)):
        return "请填写 API 端点、Key 和图片模型。"
    try:
        response = requests.get(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {api_key}"}, timeout=15,
        )
        if response.status_code in (401, 403):
            return "检查失败：Key 无效或分组权限不足。本次未出图。"
        if response.status_code != 200:
            return f"模型列表返回 HTTP {response.status_code}，无法判断出图能力。本次未出图。"
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("Invalid model list")
        ids = {item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if model in ids:
            return "图片模型已列出；生成/编辑能力仍需实际验证。本次未出图。"
        return "未列出该图片模型，请核对分组与模型映射；列表可能不完整。本次未出图。"
    except requests.RequestException:
        return "模型检查连接失败或超时，无法判断出图能力。本次未出图。"
    except (ValueError, TypeError, AttributeError):
        return "模型列表格式异常，无法判断出图能力。本次未出图。"
