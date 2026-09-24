"""Image model configuration and non-generating capability checks."""

from unittest.mock import Mock

import pytest
import requests

from ui import api_config, cloud_bootstrap
from ui.commands import GptForm, build_gpt_command, build_text_image_command
from ui.model_check import check_image_model
from tests.test_commands import text_image_form


@pytest.mark.parametrize("mode", ["background", "full_poster"])
def test_text_image_command_forwards_model(mode):
    command, _, _ = build_text_image_command(text_image_form(mode=mode, poster_copy="headline", image_model=" custom-image "))
    assert command[command.index("--model") + 1] == "custom-image"


def test_edit_command_forwards_model(tmp_path):
    source = tmp_path / "source.png"
    source.touch()
    command, _, _ = build_gpt_command(GptForm(str(source), str(tmp_path), "80", "180", "200", "", "", "", "custom-image"))
    assert command[command.index("--model") + 1] == "custom-image"


@pytest.mark.parametrize("code,body,expected", [
    (200, {"data": [{"id": "custom-image"}]}, "已列出"),
    (200, {"data": [{"id": "text-model"}]}, "未列出"),
    (200, {}, "格式异常"), (200, [], "格式异常"),
    (401, {}, "权限不足"), (403, {}, "权限不足"), (404, {}, "HTTP 404"),
])
def test_advisory_check_only_reads_models(monkeypatch, code, body, expected):
    response = Mock(status_code=code)
    response.json.return_value = body
    get = Mock(return_value=response)
    post = Mock(side_effect=AssertionError("Must not generate"))
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    result = check_image_model("https://sandbox.invalid/v1", "private-key", "custom-image")
    assert expected in result and "本次未出图" in result
    assert get.call_args.args[0].endswith("/v1/models")
    post.assert_not_called()


def test_advisory_timeout_never_leaks_key(monkeypatch):
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.Timeout("private-key")))
    assert "private-key" not in check_image_model("https://sandbox.invalid/v1", "private-key", "model")


def test_settings_load_and_save_image_model_without_network(monkeypatch):
    from ui.widgets.settings_dialog import SettingsDialog
    monkeypatch.setattr(cloud_bootstrap, "is_configured", lambda: False)
    monkeypatch.setattr(cloud_bootstrap, "cached_app_config", lambda: {"image_model": "cloud-image"})
    monkeypatch.setattr(api_config, "load_image_model", lambda: "local-image")
    save = Mock()
    push = Mock()
    monkeypatch.setattr(api_config, "save", save)
    monkeypatch.setattr(cloud_bootstrap, "push_app_config", push)
    dialog = SettingsDialog()
    assert dialog.local_image_model.text() == "local-image"
    assert dialog.cfg_image_model.text() == "cloud-image"
    dialog.local_api_base.setText("https://sandbox.invalid/v1")
    dialog.local_api_key.setText("fake-key")
    dialog.local_image_model.setText("new-local-image")
    dialog._save_local_api()
    assert save.call_args.args[3] == "new-local-image"
    dialog.cfg_image_model.setText("new-cloud-image")
    dialog._save_cloud()
    assert push.call_args.args[1]["image_model"] == "new-cloud-image"
    dialog.close()
