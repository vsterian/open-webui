"""
Unit tests for open_webui.utils.soniox module.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

from open_webui.utils.soniox import (
    SonioxClient,
    SonioxError,
    build_soniox_client_from_config,
    normalize_audio_file_for_soniox,
)


def _make_client(**overrides) -> SonioxClient:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://api.soniox.com/v1",
        "model": "stt-async-v4",
        "enable_language_identification": True,
        "language_hints": ["ro", "en"],
        "enable_speaker_diarization": True,
        "enable_translation": False,
        "translation_mode": "two_way",
        "translation_target_language": "en",
        "translation_second_language": "en",
        "context_terms": [],
        "context_text": "",
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

    def test_extract_speaker_segments(self):
        payload = {
            "tokens": [
                {"text": "Hello ", "language": "en", "speaker": "1", "start_s": 0.0},
                {"text": "there.", "language": "en", "speaker": "1", "start_s": 0.5},
                {"text": " How ", "language": "en", "speaker": "2", "start_s": 1.0},
                {"text": "are you?", "language": "en", "speaker": "2", "start_s": 1.2},
            ]
        }

        out = SonioxClient.extract_text_and_languages(payload)
        assert out["speakers"] == ["1", "2"]
        assert len(out["speaker_segments"]) == 2
        assert out["speaker_segments"][0]["speaker"] == "1"
        assert out["speaker_segments"][0]["text"] == "Hello there."
        assert out["speaker_segments"][0]["start_s"] == 0.0
        assert out["speaker_segments"][1]["speaker"] == "2"
        assert out["speaker_segments"][1]["text"] == "How are you?"

    def test_extract_translation(self):
        payload = {
            "tokens": [
                {"text": "Salut", "language": "ro", "translation_status": "original"},
                {"text": " ", "language": "ro", "translation_status": "none"},
                {"text": "Hello", "language": "en", "translation_status": "translation"},
            ]
        }

        out = SonioxClient.extract_text_and_languages(payload)
        assert "translated_text" in out
        assert out["translated_text"] == "Hello"

    def test_no_speakers_no_key(self):
        payload = {
            "tokens": [
                {"text": "Hello", "language": "en"},
            ]
        }

        out = SonioxClient.extract_text_and_languages(payload)
        assert "speakers" not in out
        assert "speaker_segments" not in out

    @patch("open_webui.utils.soniox.requests.post")
    def test_create_transcription_with_diarization(self, mock_post):
        client = _make_client(enable_speaker_diarization=True)

        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "tr-123"}
        mock_post.return_value = resp

        client.create_transcription(file_id="file-123")

        call_body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert call_body["enable_speaker_diarization"] is True

    @patch("open_webui.utils.soniox.requests.post")
    def test_create_transcription_with_two_way_translation(self, mock_post):
        client = _make_client(
            enable_translation=True,
            translation_mode="two_way",
            translation_second_language="en",
            language_hints=["ro"],
        )

        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "tr-123"}
        mock_post.return_value = resp

        client.create_transcription(file_id="file-123")

        call_body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert call_body["translation"]["type"] == "two_way"
        assert call_body["translation"]["language_a"] == "ro"
        assert call_body["translation"]["language_b"] == "en"

    @patch("open_webui.utils.soniox.requests.post")
    def test_create_transcription_with_context(self, mock_post):
        client = _make_client(
            context_terms=["Bădeni", "OpenWebUI"],
            context_text="This is a meeting about architecture.",
        )

        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "tr-123"}
        mock_post.return_value = resp

        client.create_transcription(file_id="file-123")

        call_body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert call_body["context"]["terms"] == ["Bădeni", "OpenWebUI"]
        assert call_body["context"]["text"] == "This is a meeting about architecture."


class TestBuildClientFromConfig:
    def test_build_success(self):
        config = SimpleNamespace(
            SONIOX_API_KEY="k",
            SONIOX_BASE_URL="https://api.eu.soniox.com/v1",
            SONIOX_MODEL="stt-async-v4",
            SONIOX_ENABLE_LANGUAGE_IDENTIFICATION=True,
            SONIOX_LANGUAGE_HINTS=["ro"],
            SONIOX_ENABLE_SPEAKER_DIARIZATION=True,
            SONIOX_ENABLE_TRANSLATION=False,
            SONIOX_TRANSLATION_MODE="two_way",
            SONIOX_TRANSLATION_TARGET_LANGUAGE="en",
            SONIOX_TRANSLATION_SECOND_LANGUAGE="en",
            SONIOX_CONTEXT_TERMS=["test"],
            SONIOX_CONTEXT_TEXT="context",
        )

        client = build_soniox_client_from_config(config)
        assert client.base_url == "https://api.eu.soniox.com/v1"
        assert client.language_hints == ["ro"]
        assert client.enable_speaker_diarization is True
        assert client.enable_translation is False
        assert client.translation_mode == "two_way"
        assert client.context_terms == ["test"]
        assert client.context_text == "context"

    def test_build_with_defaults(self):
        config = SimpleNamespace(
            SONIOX_API_KEY="k",
        )

        client = build_soniox_client_from_config(config)
        assert client.enable_speaker_diarization is True
        assert client.enable_translation is False
        assert client.translation_mode == "two_way"
        assert client.context_terms == []
        assert client.context_text == ""

    def test_missing_api_key(self):
        config = SimpleNamespace(SONIOX_API_KEY="")

        with pytest.raises(SonioxError, match="SONIOX_API_KEY"):
            build_soniox_client_from_config(config)


class TestNormalizeAudioForSoniox:
    @patch("open_webui.utils.soniox.Path.exists")
    def test_passthrough_for_supported_audio(self, mock_exists):
        mock_exists.return_value = True

        out_path, out_name = normalize_audio_file_for_soniox(
            "/tmp/meeting.mp3", filename="meeting.mp3", content_type="audio/mpeg"
        )

        assert out_path == "/tmp/meeting.mp3"
        assert out_name == "meeting.mp3"

    @patch("open_webui.utils.soniox.Path.exists")
    def test_convert_webm_to_mp3(self, mock_exists):
        mock_exists.return_value = True
        audio = MagicMock()
        fake_pydub = MagicMock()
        fake_pydub.AudioSegment.from_file.return_value = audio

        with patch.dict(sys.modules, {"pydub": fake_pydub}):
            out_path, out_name = normalize_audio_file_for_soniox(
                "/tmp/meeting.webm",
                filename="meeting.webm",
                content_type="audio/webm;codecs=opus",
            )

        assert out_path == "/tmp/meeting.mp3"
        assert out_name == "meeting.mp3"
        fake_pydub.AudioSegment.from_file.assert_called_once_with("/tmp/meeting.webm")
        audio.export.assert_called_once_with("/tmp/meeting.mp3", format="mp3")

    @patch("open_webui.utils.soniox.Path.exists")
    def test_missing_file_raises(self, mock_exists):
        mock_exists.return_value = False

        with pytest.raises(SonioxError, match="File does not exist"):
            normalize_audio_file_for_soniox("/tmp/missing.webm")

    @patch("open_webui.utils.soniox.Path.exists")
    def test_conversion_failure_raises(self, mock_exists):
        mock_exists.return_value = True
        fake_pydub = MagicMock()
        fake_pydub.AudioSegment.from_file.side_effect = Exception("decode failed")

        with patch.dict(sys.modules, {"pydub": fake_pydub}):
            with pytest.raises(SonioxError, match="Failed to convert audio"):
                normalize_audio_file_for_soniox(
                    "/tmp/meeting.webm",
                    filename="meeting.webm",
                    content_type="audio/webm",
                )
