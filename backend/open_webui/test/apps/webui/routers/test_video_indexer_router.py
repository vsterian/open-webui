"""
Unit tests for open_webui.routers.video_indexer module.

Tests the FastAPI router endpoints using TestClient with mocked
dependencies (auth, config state, Files model, VideoIndexerClient).

Heavy dependencies (SQLAlchemy, peewee, etc.) are mocked at the module
level before importing the router so these tests can run without a full
backend environment.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ──────────────────────────────────────────────
# Pre-import mocking of heavy dependencies
# ──────────────────────────────────────────────

# Mock modules that cause import chains into SQLAlchemy/greenlet/opentelemetry/etc.
# We only need the router's endpoint logic with Files and auth overridden.
_heavy_modules = [
    # DB / ORM
    "open_webui.models.files",
    "open_webui.internal.db",
    "open_webui.internal.wrappers",
    # Telemetry
    "opentelemetry",
    "opentelemetry.trace",
    # Auth utils (pulls in models, DB, otel)
    "open_webui.utils.auth",
    # Models used transitively by auth
    "open_webui.models.auths",
    "open_webui.models.users",
    "open_webui.models.groups",
    # Config / env (massive import tree)
    "open_webui.env",
]

for mod_name in _heavy_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Provide a mock Files class accessible as open_webui.models.files.Files
mock_files_module = sys.modules["open_webui.models.files"]
mock_files_class = MagicMock()
mock_files_module.Files = mock_files_class

# Provide real-looking auth dependency callables that FastAPI can use
_mock_get_admin_user = MagicMock()
_mock_get_verified_user = MagicMock()
sys.modules["open_webui.utils.auth"].get_admin_user = _mock_get_admin_user
sys.modules["open_webui.utils.auth"].get_verified_user = _mock_get_verified_user

# Now safe to import the router
from open_webui.routers.video_indexer import router, VideoIndexerConfigForm
from open_webui.utils.video_indexer import VideoIndexerError


# ──────────────────────────────────────────────
# Test App Setup
# ──────────────────────────────────────────────

def _create_test_app(config_overrides: dict = None) -> FastAPI:
    """
    Build a minimal FastAPI app with the video_indexer router mounted
    and mock auth dependencies injected.
    """
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/video-indexer")

    # Set up mock config on app state
    defaults = {
        "VIDEO_INDEXER_ENABLED": False,
        "VIDEO_INDEXER_PROVIDER": "azure_video_indexer",
        "VIDEO_INDEXER_ACCOUNT_NAME": "test-acct",
        "VIDEO_INDEXER_ACCOUNT_ID": "acct-000",
        "VIDEO_INDEXER_RESOURCE_GROUP": "test-rg",
        "VIDEO_INDEXER_SUBSCRIPTION_ID": "sub-111",
        "VIDEO_INDEXER_LOCATION": "eastus",
        "VIDEO_INDEXER_TENANT_ID": "tenant-222",
        "VIDEO_INDEXER_CLIENT_ID": "client-333",
        "VIDEO_INDEXER_CLIENT_SECRET": "secret-444",
        "VIDEO_INDEXER_INDEXING_PRESET": "Default",
        "VIDEO_INDEXER_LANGUAGE": "en-US",
        "SONIOX_API_KEY": "",
        "SONIOX_BASE_URL": "https://api.soniox.com/v1",
        "SONIOX_MODEL": "stt-async-v4",
        "SONIOX_ENABLE_LANGUAGE_IDENTIFICATION": True,
        "SONIOX_LANGUAGE_HINTS": [],
    }
    if config_overrides:
        defaults.update(config_overrides)

    app.state.config = SimpleNamespace(**defaults)

    # Override auth dependencies to bypass real auth
    mock_admin = SimpleNamespace(id="admin-1", name="Admin", email="admin@test.com", role="admin")
    mock_user = SimpleNamespace(id="user-1", name="User", email="user@test.com", role="user")

    app.dependency_overrides[_mock_get_admin_user] = lambda: mock_admin
    app.dependency_overrides[_mock_get_verified_user] = lambda: mock_user

    return app


# ──────────────────────────────────────────────
# Config Endpoints
# ──────────────────────────────────────────────

class TestConfigEndpoints:
    def setup_method(self):
        self.app = _create_test_app()
        self.client = TestClient(self.app)

    def test_get_config(self):
        resp = self.client.get("/api/v1/video-indexer/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ENABLED"] is False
        assert data["PROVIDER"] == "azure_video_indexer"
        assert data["ACCOUNT_NAME"] == "test-acct"
        assert data["LOCATION"] == "eastus"
        assert data["INDEXING_PRESET"] == "Default"
        assert data["LANGUAGE"] == "en-US"
        assert data["SONIOX_MODEL"] == "stt-async-v4"

    def test_update_config(self):
        payload = {
            "ENABLED": True,
            "PROVIDER": "soniox",
            "ACCOUNT_NAME": "new-acct",
            "ACCOUNT_ID": "new-id",
            "RESOURCE_GROUP": "new-rg",
            "SUBSCRIPTION_ID": "new-sub",
            "LOCATION": "westus2",
            "TENANT_ID": "new-tenant",
            "CLIENT_ID": "new-client",
            "CLIENT_SECRET": "new-secret",
            "INDEXING_PRESET": "AudioOnly",
            "LANGUAGE": "es-ES",
            "SONIOX_API_KEY": "soniox-key",
            "SONIOX_BASE_URL": "https://api.eu.soniox.com/v1",
            "SONIOX_MODEL": "stt-async-v4",
            "SONIOX_ENABLE_LANGUAGE_IDENTIFICATION": True,
            "SONIOX_LANGUAGE_HINTS": ["ro", "en"],
        }
        resp = self.client.post("/api/v1/video-indexer/config/update", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ENABLED"] is True
        assert data["PROVIDER"] == "soniox"
        assert data["ACCOUNT_NAME"] == "new-acct"
        assert data["LOCATION"] == "westus2"
        assert data["INDEXING_PRESET"] == "AudioOnly"
        assert data["SONIOX_BASE_URL"] == "https://api.eu.soniox.com/v1"

        # Verify state was actually mutated
        assert self.app.state.config.VIDEO_INDEXER_ENABLED is True
        assert self.app.state.config.VIDEO_INDEXER_PROVIDER == "soniox"
        assert self.app.state.config.VIDEO_INDEXER_LOCATION == "westus2"

    def test_update_config_partial_defaults(self):
        """Unset fields in the form use defaults."""
        payload = {
            "ENABLED": False,
            "ACCOUNT_NAME": "",
            "ACCOUNT_ID": "",
            "RESOURCE_GROUP": "",
            "SUBSCRIPTION_ID": "",
            "LOCATION": "",
            "TENANT_ID": "",
            "CLIENT_ID": "",
            "CLIENT_SECRET": "",
            "PROVIDER": "azure_video_indexer",
        }
        resp = self.client.post("/api/v1/video-indexer/config/update", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Defaults from Pydantic model
        assert data["INDEXING_PRESET"] == "Default"
        assert data["LANGUAGE"] == "en-US"


# ──────────────────────────────────────────────
# Verify Connection
# ──────────────────────────────────────────────

class TestVerifyEndpoint:
    def setup_method(self):
        self.app = _create_test_app()
        self.client = TestClient(self.app)

    @patch("open_webui.routers.video_indexer.build_client_from_config")
    def test_verify_success(self, mock_build):
        mock_client = MagicMock()
        mock_client.verify_connection.return_value = {"id": "acct-000", "name": "Test"}
        mock_build.return_value = mock_client

        resp = self.client.post("/api/v1/video-indexer/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider"] == "azure_video_indexer"
        assert data["account"]["name"] == "Test"

    @patch("open_webui.routers.video_indexer.build_soniox_client_from_config")
    def test_verify_soniox_success(self, mock_build):
        self.app.state.config.VIDEO_INDEXER_PROVIDER = "soniox"
        mock_client = MagicMock()
        mock_client.verify_connection.return_value = {"status": "ok", "model": "stt-async-v4"}
        mock_build.return_value = mock_client

        resp = self.client.post("/api/v1/video-indexer/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider"] == "soniox"

    @patch("open_webui.routers.video_indexer.build_client_from_config")
    def test_verify_config_error(self, mock_build):
        from open_webui.utils.video_indexer import VideoIndexerError
        mock_build.side_effect = VideoIndexerError("Missing config fields")

        resp = self.client.post("/api/v1/video-indexer/verify")
        assert resp.status_code == 400
        assert "Missing config" in resp.json()["detail"]

    @patch("open_webui.routers.video_indexer.build_client_from_config")
    def test_verify_connection_error(self, mock_build):
        from open_webui.utils.video_indexer import VideoIndexerError
        mock_client = MagicMock()
        mock_client.verify_connection.side_effect = VideoIndexerError("401 – Unauthorized")
        mock_build.return_value = mock_client

        resp = self.client.post("/api/v1/video-indexer/verify")
        assert resp.status_code == 400
        assert "401" in resp.json()["detail"]


# ──────────────────────────────────────────────
# File Status / Insights / Transcript
# ──────────────────────────────────────────────

class TestFileEndpoints:
    def setup_method(self):
        self.app = _create_test_app()
        self.client = TestClient(self.app)

    @patch("open_webui.routers.video_indexer.Files")
    def test_status_found(self, mock_files):
        mock_file = MagicMock()
        mock_file.meta = {
            "analyzer_provider": "soniox",
            "analyzer": {
                "transcription_id": "tr-abc",
                "state": "Processed",
                "progress": "100%",
            }
        }
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/status/file-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["transcription_id"] == "tr-abc"
        assert data["state"] == "Processed"

    @patch("open_webui.routers.video_indexer.Files")
    def test_status_file_not_found(self, mock_files):
        mock_files.get_file_by_id.return_value = None

        resp = self.client.get("/api/v1/video-indexer/status/nonexistent")
        assert resp.status_code == 404

    @patch("open_webui.routers.video_indexer.Files")
    def test_status_no_vi_meta(self, mock_files):
        mock_file = MagicMock()
        mock_file.meta = {}
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/status/file-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unknown"
        assert data["video_id"] is None

    @patch("open_webui.routers.video_indexer.Files")
    def test_insights_found(self, mock_files):
        mock_file = MagicMock()
        mock_file.meta = {
            "analyzer": {
                "insights": {"keywords": ["test"], "topics": ["AI"]},
            }
        }
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/insights/file-123")
        assert resp.status_code == 200
        data = resp.json()
        assert "keywords" in data

    @patch("open_webui.routers.video_indexer.Files")
    def test_insights_not_available(self, mock_files):
        mock_file = MagicMock()
        mock_file.meta = {"video_indexer": {}}
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/insights/file-123")
        assert resp.status_code == 404

    @patch("open_webui.routers.video_indexer.Files")
    def test_transcript_found(self, mock_files):
        mock_file = MagicMock()
        mock_file.data = {"content": "Hello world transcript text"}
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/transcript/file-123")
        assert resp.status_code == 200
        data = resp.json()
        assert "Hello world" in data["transcript"]

    @patch("open_webui.routers.video_indexer.Files")
    def test_transcript_not_available(self, mock_files):
        mock_file = MagicMock()
        mock_file.data = {}
        mock_files.get_file_by_id.return_value = mock_file

        resp = self.client.get("/api/v1/video-indexer/transcript/file-123")
        assert resp.status_code == 404


# ──────────────────────────────────────────────
# Search
# ──────────────────────────────────────────────

class TestSearchEndpoint:
    def setup_method(self):
        self.app = _create_test_app({"VIDEO_INDEXER_ENABLED": True})
        self.client = TestClient(self.app)

    @patch("open_webui.routers.video_indexer.build_client_from_config")
    def test_search_success(self, mock_build):
        mock_client = MagicMock()
        mock_client.search_videos.return_value = {
            "results": [{"id": "vid-1", "name": "Test Video"}],
            "nextPage": {},
        }
        mock_build.return_value = mock_client

        resp = self.client.get(
            "/api/v1/video-indexer/search",
            params={"query": "demo", "page_size": 5},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_search_disabled(self):
        app = _create_test_app({"VIDEO_INDEXER_ENABLED": False})
        client = TestClient(app)

        resp = client.get(
            "/api/v1/video-indexer/search",
            params={"query": "anything"},
        )
        assert resp.status_code == 400
        assert "not enabled" in resp.json()["detail"]

    @patch("open_webui.routers.video_indexer.build_client_from_config")
    def test_search_api_error(self, mock_build):
        from open_webui.utils.video_indexer import VideoIndexerError
        mock_client = MagicMock()
        mock_client.search_videos.side_effect = VideoIndexerError("502 – Bad Gateway")
        mock_build.return_value = mock_client

        resp = self.client.get(
            "/api/v1/video-indexer/search",
            params={"query": "fail"},
        )
        assert resp.status_code == 502
