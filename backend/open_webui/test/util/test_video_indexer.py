"""
Unit tests for open_webui.utils.video_indexer module.

Tests the VideoIndexerClient class (auth, upload, polling, insight extraction)
and the build_client_from_config helper using mocked HTTP / MSAL responses.
"""

import time
import pytest
from unittest.mock import patch, MagicMock, mock_open, PropertyMock
from types import SimpleNamespace

# We mock msal at import time so the real package is not required for testing.
import sys

msal_mock = MagicMock()
sys.modules["msal"] = msal_mock

from open_webui.utils.video_indexer import (
    VideoIndexerClient,
    VideoIndexerError,
    build_client_from_config,
)


# ──────────────────────────────────────────────
# Fixtures / Helpers
# ──────────────────────────────────────────────

def _make_client(**overrides) -> VideoIndexerClient:
    """Build a client with sensible test defaults."""
    defaults = dict(
        account_name="test-account",
        account_id="00000000-0000-0000-0000-000000000000",
        resource_group="test-rg",
        subscription_id="11111111-1111-1111-1111-111111111111",
        location="eastus",
        tenant_id="22222222-2222-2222-2222-222222222222",
        client_id="33333333-3333-3333-3333-333333333333",
        client_secret="super-secret",
    )
    defaults.update(overrides)
    return VideoIndexerClient(**defaults)


def _mock_arm_token(client, token="fake-arm-token", expires_in=3600):
    """Shortcut to inject a cached ARM token so we skip MSAL."""
    client._arm_token = token
    client._arm_token_expiry = time.time() + expires_in


def _mock_vi_token(client, token="fake-vi-token", expires_in=1800):
    """Shortcut to inject a cached VI token."""
    client._vi_token = token
    client._vi_token_expiry = time.time() + expires_in


# ──────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────

class TestAuthentication:
    """Tests for MSAL / ARM / VI token acquisition."""

    def test_arm_token_cached_returns_immediately(self):
        client = _make_client()
        _mock_arm_token(client, token="cached-arm")

        # Should return the cached token without touching MSAL
        assert client._get_arm_token() == "cached-arm"

    def test_arm_token_expired_refreshes(self):
        client = _make_client()
        client._arm_token = "old"
        client._arm_token_expiry = time.time() - 100  # expired

        mock_app = MagicMock()
        mock_app.acquire_token_silent.return_value = None
        mock_app.acquire_token_for_client.return_value = {
            "access_token": "fresh-arm-token",
            "expires_in": 3600,
        }
        client._msal_app = mock_app

        token = client._get_arm_token()
        assert token == "fresh-arm-token"
        assert client._arm_token == "fresh-arm-token"
        mock_app.acquire_token_for_client.assert_called_once()

    def test_arm_token_failure_raises(self):
        client = _make_client()
        mock_app = MagicMock()
        mock_app.acquire_token_silent.return_value = None
        mock_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "Bad credentials",
        }
        client._msal_app = mock_app

        with pytest.raises(VideoIndexerError, match="Bad credentials"):
            client._get_arm_token()

    @patch("open_webui.utils.video_indexer.requests.post")
    def test_get_access_token_success(self, mock_post):
        client = _make_client()
        _mock_arm_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"accessToken": "vi-token-123"}
        mock_post.return_value = mock_resp

        token = client.get_access_token()
        assert token == "vi-token-123"
        assert client._vi_token == "vi-token-123"

    @patch("open_webui.utils.video_indexer.requests.post")
    def test_get_access_token_cached(self, mock_post):
        client = _make_client()
        _mock_vi_token(client, token="cached-vi")

        token = client.get_access_token()
        assert token == "cached-vi"
        mock_post.assert_not_called()

    @patch("open_webui.utils.video_indexer.requests.post")
    def test_get_access_token_http_error_raises(self, mock_post):
        client = _make_client()
        _mock_arm_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(VideoIndexerError, match="403"):
            client.get_access_token()

    @patch("open_webui.utils.video_indexer.requests.post")
    def test_get_access_token_empty_token_raises(self, mock_post):
        client = _make_client()
        _mock_arm_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # no accessToken field
        mock_post.return_value = mock_resp

        with pytest.raises(VideoIndexerError, match="Empty access token"):
            client.get_access_token()


# ──────────────────────────────────────────────
# Connection verification
# ──────────────────────────────────────────────

class TestVerifyConnection:
    @patch("open_webui.utils.video_indexer.requests.post")
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_verify_success(self, mock_get, mock_post):
        client = _make_client()
        _mock_arm_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "My Account",
            "location": "eastus",
            "properties": {"accountId": "00000000-0000-0000-0000-000000000000"},
        }
        mock_get.return_value = mock_resp

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"accessToken": "vi-token-123"}
        mock_post.return_value = mock_token_resp

        result = client.verify_connection()
        assert result["status"] == "ok"
        assert result["account_name"] == "My Account"
        assert result["account_id"] == "00000000-0000-0000-0000-000000000000"

    @patch("open_webui.utils.video_indexer.requests.get")
    def test_verify_failure(self, mock_get):
        client = _make_client()
        _mock_arm_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        with pytest.raises(VideoIndexerError, match="401"):
            client.verify_connection()


# ──────────────────────────────────────────────
# Upload
# ──────────────────────────────────────────────

class TestUpload:
    @patch("builtins.open", mock_open(read_data=b"fake-video-content"))
    @patch("open_webui.utils.video_indexer.requests.post")
    def test_upload_video_success(self, mock_post):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "vid-abc-123"}
        mock_post.return_value = mock_resp

        video_id = client.upload_video("/path/to/video.mp4", "test-video")
        assert video_id == "vid-abc-123"

        # Verify the POST was made with correct URL containing account ID
        call_args = mock_post.call_args
        assert "/Videos" in call_args[0][0] or "/Videos" in str(call_args)

    @patch("builtins.open", mock_open(read_data=b"fake-video-content"))
    @patch("open_webui.utils.video_indexer.requests.post")
    def test_upload_video_failure(self, mock_post):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_post.return_value = mock_resp

        with pytest.raises(VideoIndexerError, match="400"):
            client.upload_video("/path/to/video.mp4", "test-video")

    @patch("builtins.open", mock_open(read_data=b"fake-video-content"))
    @patch("open_webui.utils.video_indexer.requests.post")
    def test_upload_with_callback_url(self, mock_post):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "vid-456"}
        mock_post.return_value = mock_resp

        video_id = client.upload_video(
            "/path/to/video.mp4",
            "test-video",
            callback_url="https://example.com/callback",
        )
        assert video_id == "vid-456"

    @patch("builtins.open", mock_open(read_data=b"fake-video-content"))
    @patch("open_webui.utils.video_indexer.requests.post")
    def test_upload_no_id_in_response(self, mock_post):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "Processing"}  # no "id"
        mock_post.return_value = mock_resp

        with pytest.raises(VideoIndexerError, match="No video id"):
            client.upload_video("/tmp/video.mp4", "test")


# ──────────────────────────────────────────────
# Status / Polling
# ──────────────────────────────────────────────

class TestStatusAndPolling:
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_get_video_status(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "state": "Processing",
            "processingProgress": "42%",
        }
        mock_get.return_value = mock_resp

        result = client.get_video_status("vid-123")
        assert result["state"] == "Processing"
        assert result["processingProgress"] == "42%"

    @patch("open_webui.utils.video_indexer.time.sleep")
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_wait_for_indexing_success(self, mock_get, mock_sleep):
        client = _make_client()
        _mock_vi_token(client)

        # First call: Processing; second call: Processed
        resp_processing = MagicMock()
        resp_processing.status_code = 200
        resp_processing.json.return_value = {"state": "Processing", "processingProgress": "50%"}

        resp_done = MagicMock()
        resp_done.status_code = 200
        resp_done.json.return_value = {"state": "Processed", "processingProgress": "100%"}

        mock_get.side_effect = [resp_processing, resp_done]

        result = client.wait_for_indexing("vid-123", poll_interval=1, timeout=60)
        assert result == "Processed"
        mock_sleep.assert_called_once_with(1)

    @patch("open_webui.utils.video_indexer.time.sleep")
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_wait_for_indexing_failure(self, mock_get, mock_sleep):
        client = _make_client()
        _mock_vi_token(client)

        resp_failed = MagicMock()
        resp_failed.status_code = 200
        resp_failed.json.return_value = {"state": "Failed", "processingProgress": ""}

        mock_get.return_value = resp_failed

        with pytest.raises(VideoIndexerError, match="failed"):
            client.wait_for_indexing("vid-123", poll_interval=1, timeout=60)

    @patch("open_webui.utils.video_indexer.time.time")
    @patch("open_webui.utils.video_indexer.time.sleep")
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_wait_for_indexing_timeout(self, mock_get, mock_sleep, mock_time):
        client = _make_client()
        _mock_vi_token(client)

        # Simulate time progressing past timeout.  The first call to time.time()
        # comes from get_access_token inside get_video_status, so we need extra
        # values.  Token is already cached so the comparison `now < expiry - 60`
        # just needs a reasonable number.
        mock_time.side_effect = [
            0,      # wait_for_indexing: start = time.time()
            500,    # get_access_token: now (token cached, passes)
            0,      # elapsed = time.time() - start -- first loop iteration
            500,    # get_access_token: now -- for status call
            0,      # wait_for_indexing: elapsed check -- still fine
            500,    # get_access_token: now
            100,    # elapsed = time.time() - start -- > timeout=5
        ]

        resp_processing = MagicMock()
        resp_processing.status_code = 200
        resp_processing.json.return_value = {"state": "Processing", "processingProgress": "10%"}
        mock_get.return_value = resp_processing

        with pytest.raises(VideoIndexerError, match="timed out"):
            client.wait_for_indexing("vid-123", poll_interval=1, timeout=5)


# ──────────────────────────────────────────────
# Insights retrieval
# ──────────────────────────────────────────────

class TestInsightsRetrieval:
    @patch("open_webui.utils.video_indexer.requests.get")
    def test_get_video_index(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        expected_data = {"videos": [], "summarizedInsights": {}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = expected_data
        mock_get.return_value = mock_resp

        result = client.get_video_index("vid-123")
        assert result == expected_data

    @patch("open_webui.utils.video_indexer.requests.get")
    def test_get_transcript(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Hello world\nSecond line"
        mock_get.return_value = mock_resp

        result = client.get_transcript("vid-123", fmt="txt")
        assert result == "Hello world\nSecond line"

    @patch("open_webui.utils.video_indexer.requests.get")
    def test_get_transcript_vtt(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "WEBVTT\n\n00:00.000 --> 00:05.000\nHello"
        mock_get.return_value = mock_resp

        result = client.get_transcript("vid-123", fmt="vtt")
        assert "WEBVTT" in result

    @patch("open_webui.utils.video_indexer.requests.get")
    def test_get_thumbnail(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\xff\xd8\xff\xe0"  # JPEG header bytes
        mock_get.return_value = mock_resp

        result = client.get_thumbnail("vid-123", "thumb-001")
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"

    @patch("open_webui.utils.video_indexer.requests.get")
    def test_search_videos(self, mock_get):
        client = _make_client()
        _mock_vi_token(client)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"id": "vid-1"}], "nextPage": {}}
        mock_get.return_value = mock_resp

        result = client.search_videos("car crash", page_size=10, skip=0)
        assert len(result["results"]) == 1


# ──────────────────────────────────────────────
# extract_structured_content
# ──────────────────────────────────────────────

class TestExtractStructuredContent:
    """Test the static method that transforms VI JSON into text for RAG."""

    def test_full_index_extraction(self):
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {
                                "text": "Hello everyone",
                                "speakerId": 1,
                                "instances": [{"start": "0:00:00"}],
                            },
                            {
                                "text": "Welcome to the demo",
                                "speakerId": 2,
                                "instances": [{"start": "0:00:05"}],
                            },
                        ]
                    }
                }
            ],
            "summarizedInsights": {
                "keywords": [{"name": "demo"}, {"name": "AI"}],
                "topics": [{"name": "Technology"}, {"name": "Education"}],
                "namedLocations": [{"name": "Seattle"}],
                "namedPeople": [{"name": "John Doe"}],
                "brands": [{"name": "Microsoft"}],
                "sentiments": [
                    {"sentimentKey": "Positive", "seenDurationRatio": 0.75},
                    {"sentimentKey": "Neutral", "seenDurationRatio": 0.25},
                ],
            },
        }

        result = VideoIndexerClient.extract_structured_content(index_data)

        # Transcript
        assert "## Video Transcript" in result
        assert "Hello everyone" in result
        assert "Welcome to the demo" in result
        assert "Speaker 1:" in result
        assert "Speaker 2:" in result
        assert "[0:00:00]" in result

        # Keywords
        assert "## Keywords" in result
        assert "demo" in result
        assert "AI" in result

        # Topics
        assert "## Topics" in result
        assert "Technology" in result

        # Entities
        assert "## Named Entities" in result
        assert "Seattle (Location)" in result
        assert "John Doe (Person)" in result
        assert "Microsoft (Brand)" in result

        # Sentiment
        assert "## Sentiment" in result
        assert "Positive: 75%" in result

    def test_empty_index(self):
        result = VideoIndexerClient.extract_structured_content({})
        assert result == ""

    def test_transcript_only(self):
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {
                                "text": "Just a transcript",
                                "instances": [{"start": "0:00:00"}],
                            }
                        ]
                    }
                }
            ],
            "summarizedInsights": {},
        }

        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Video Transcript" in result
        assert "Just a transcript" in result
        assert "## Keywords" not in result
        assert "## Topics" not in result

    def test_keywords_only(self):
        index_data = {
            "videos": [],
            "summarizedInsights": {
                "keywords": [{"name": "python"}, {"name": "testing"}],
            },
        }

        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Keywords" in result
        assert "python" in result
        assert "## Video Transcript" not in result

    def test_transcript_without_speaker_or_timestamp(self):
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {"text": "No metadata", "instances": [{}]},
                        ]
                    }
                }
            ],
            "summarizedInsights": {},
        }

        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "No metadata" in result
        # No speaker prefix or timestamp when empty
        assert "Speaker" not in result


# ──────────────────────────────────────────────
# build_client_from_config
# ──────────────────────────────────────────────

class TestBuildClientFromConfig:
    def _make_config(self, **overrides):
        defaults = {
            "VIDEO_INDEXER_ACCOUNT_NAME": "my-account",
            "VIDEO_INDEXER_ACCOUNT_ID": "acct-id",
            "VIDEO_INDEXER_RESOURCE_GROUP": "my-rg",
            "VIDEO_INDEXER_SUBSCRIPTION_ID": "sub-id",
            "VIDEO_INDEXER_LOCATION": "eastus",
            "VIDEO_INDEXER_TENANT_ID": "tenant-id",
            "VIDEO_INDEXER_CLIENT_ID": "client-id",
            "VIDEO_INDEXER_CLIENT_SECRET": "client-secret",
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_build_success(self):
        config = self._make_config()
        client = build_client_from_config(config)
        assert isinstance(client, VideoIndexerClient)
        assert client.account_name == "my-account"
        assert client.location == "eastus"

    def test_missing_field_raises(self):
        config = self._make_config(VIDEO_INDEXER_CLIENT_SECRET="")
        with pytest.raises(VideoIndexerError, match="VIDEO_INDEXER_CLIENT_SECRET"):
            build_client_from_config(config)

    def test_multiple_missing_fields(self):
        config = self._make_config(
            VIDEO_INDEXER_ACCOUNT_NAME="",
            VIDEO_INDEXER_TENANT_ID="",
        )
        with pytest.raises(VideoIndexerError, match="VIDEO_INDEXER_ACCOUNT_NAME"):
            build_client_from_config(config)

    def test_missing_attribute_treated_as_empty(self):
        """Config objects that don't even have the attribute should be caught."""
        config = SimpleNamespace()  # empty – no VIDEO_INDEXER_* attrs
        with pytest.raises(VideoIndexerError, match="Missing"):
            build_client_from_config(config)
