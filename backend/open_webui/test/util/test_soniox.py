"""
Unit tests for open_webui.utils.soniox module.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

from open_webui.utils.soniox import (
    SonioxClient,
    SonioxError,
    build_soniox_client_from_config,
)


def _make_client(**overrides) -> SonioxClient:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://api.soniox.com/v1",
        "model": "stt-async-v4",
        "enable_language_identification": True,
        "language_hints": ["ro", "en"],
    }
    defaults.update(overrides)
    return SonioxClient(**defaults)


class TestSonioxClient:
    @patch("open_webui.utils.soniox.requests.get")
    def test_verify_connection_success(self, mock_get):
        client = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"name": "stt-async-v4"},
                {"name": "stt-rt-v4"},
            ]
        }
        mock_get.return_value = resp

        data = client.verify_connection()
        assert data["status"] == "ok"
        assert "stt-async-v4" in data["available_models"]

    @patch("open_webui.utils.soniox.requests.post")
    @patch("open_webui.utils.soniox.Path.exists")
    def test_upload_file_success(self, mock_exists, mock_post):
        client = _make_client()
        mock_exists.return_value = True

        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "file-123"}
        mock_post.return_value = resp

        with patch("builtins.open", mock_open(read_data=b"fake")):
            file_id = client.upload_file("/tmp/a.mp3", "a.mp3")

        assert file_id == "file-123"

    @patch("open_webui.utils.soniox.requests.post")
    def test_create_transcription_failure(self, mock_post):
        client = _make_client()

        resp = MagicMock()
        resp.status_code = 400
        resp.text = "bad request"
        resp.json.return_value = {"error": "bad request"}
        mock_post.return_value = resp

        with pytest.raises(SonioxError, match="Failed to create Soniox transcription"):
            client.create_transcription(file_id="file-123")

    @patch("open_webui.utils.soniox.requests.get")
    def test_wait_for_completion_success(self, mock_get):
        client = _make_client()

        processing = MagicMock()
        processing.status_code = 200
        processing.json.return_value = {"id": "tr-1", "status": "processing", "progress": 42}

        completed = MagicMock()
        completed.status_code = 200
        completed.json.return_value = {"id": "tr-1", "status": "completed", "progress": 100}

        mock_get.side_effect = [processing, completed]

        progress_calls = []

        with patch("open_webui.utils.soniox.time.sleep") as mock_sleep:
            result = client.wait_for_completion(
                "tr-1", poll_interval=1, timeout=10,
                on_progress=lambda s, p, e: progress_calls.append((s, p)),
            )

        assert result["status"] == "completed"
        mock_sleep.assert_called_once_with(1)
        assert len(progress_calls) == 2
        assert progress_calls[0] == ("processing", 42)
        assert progress_calls[1] == ("completed", 100)

    def test_extract_text_and_languages(self):
        payload = {
            "tokens": [
                {"text": "Salut", "language": "ro"},
                {"text": " ", "language": "ro"},
                {"text": "world", "language": "en"},
            ]
        }

        out = SonioxClient.extract_text_and_languages(payload)
        assert out["text"] == "Salut world"
        assert out["languages"] == ["ro", "en"]


class TestBuildClientFromConfig:
    def test_build_success(self):
        config = SimpleNamespace(
            SONIOX_API_KEY="k",
            SONIOX_BASE_URL="https://api.eu.soniox.com/v1",
            SONIOX_MODEL="stt-async-v4",
            SONIOX_ENABLE_LANGUAGE_IDENTIFICATION=True,
            SONIOX_LANGUAGE_HINTS=["ro"],
        )

        client = build_soniox_client_from_config(config)
        assert client.base_url == "https://api.eu.soniox.com/v1"
        assert client.language_hints == ["ro"]

    def test_missing_api_key(self):
        config = SimpleNamespace(SONIOX_API_KEY="")

        with pytest.raises(SonioxError, match="SONIOX_API_KEY"):
            build_soniox_client_from_config(config)
