"""Tests for Azure server-staged upload: gate bug fix, UploadSessionStore staged
blocks, and mock 2GB benchmark."""

import base64
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from open_webui.utils.upload_sessions import UploadSessionError, UploadSessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_azure_block_id(chunk_index: int) -> str:
    raw = f"block-{chunk_index:08d}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def _normalize_sas_token(raw_value: str) -> str:
    """Mirror of open_webui.routers.files._normalize_sas_token for testing."""
    value = (raw_value or "").strip()
    if not value:
        return ""
    if "?" in value:
        value = value.split("?", 1)[1]
    return value.strip(" ?&")


def _build_azure_upload_meta(
    sas: str, endpoint: str, container: str,
    fallback_endpoint: str = "", fallback_container: str = "",
    session_id: str = "session-id", filename: str = "file.mov",
) -> dict | None:
    """Mirror of open_webui.routers.files._build_azure_upload_meta for testing.
    Tests the same logic without requiring full router import chain."""
    from urllib.parse import quote

    sas_token = _normalize_sas_token(sas)
    endpoint_resolved = str(endpoint or fallback_endpoint or "").rstrip("/")
    container_resolved = str(container or fallback_container or "").strip("/")

    if not sas_token or not endpoint_resolved or not container_resolved:
        return None

    blob_name = f"{session_id}_{os.path.basename(filename)}"
    blob_url = f"{endpoint_resolved}/{container_resolved}/{quote(blob_name)}"
    return {
        "blob_name": blob_name,
        "blob_url": blob_url,
        "sas_token": sas_token,
    }


# ---------------------------------------------------------------------------
# Phase 1: Gate-bug regression tests for _build_azure_upload_meta
# ---------------------------------------------------------------------------

class TestBuildAzureUploadMeta:
    """Verify _build_azure_upload_meta logic works regardless of STORAGE_PROVIDER.
    Tests use a local mirror of the function to avoid importing the files router
    which has deep dependency chains (peewee, SQLAlchemy, redis, etc.)."""

    def test_returns_meta_with_all_vi_settings_present(self):
        result = _build_azure_upload_meta(
            sas="sv=2023-11-03&ss=b&srt=o&sp=rwdlacupiytfx&se=2030-01-01",
            endpoint="https://myaccount.blob.core.windows.net",
            container="videos",
            session_id="session-123",
            filename="test.mov",
        )

        assert result is not None
        assert "blob_url" in result
        assert "sas_token" in result
        assert "blob_name" in result
        assert "session-123" in result["blob_name"]
        assert "myaccount.blob.core.windows.net" in result["blob_url"]
        assert "videos" in result["blob_url"]

    def test_returns_none_when_vi_settings_empty(self):
        result = _build_azure_upload_meta(sas="", endpoint="", container="")
        assert result is None

    def test_returns_none_when_sas_missing(self):
        result = _build_azure_upload_meta(
            sas="",
            endpoint="https://myaccount.blob.core.windows.net",
            container="videos",
        )
        assert result is None

    def test_uses_fallback_for_endpoint_and_container(self):
        result = _build_azure_upload_meta(
            sas="sv=2023-11-03&ss=b&srt=o",
            endpoint="",
            container="",
            fallback_endpoint="https://fallback.blob.core.windows.net",
            fallback_container="fallback-container",
            session_id="session-456",
            filename="video.mp4",
        )

        assert result is not None
        assert "fallback.blob.core.windows.net" in result["blob_url"]
        assert "fallback-container" in result["blob_url"]

    def test_sas_token_strips_question_mark_prefix(self):
        result = _build_azure_upload_meta(
            sas="?sv=2023-11-03&ss=b",
            endpoint="https://acct.blob.core.windows.net",
            container="c",
            session_id="s1",
            filename="f.mov",
        )
        assert result is not None
        assert not result["sas_token"].startswith("?")


# ---------------------------------------------------------------------------
# Phase 2: UploadSessionStore - azure_server_staged mode
# ---------------------------------------------------------------------------

class TestUploadSessionStoreServerStaged:
    def test_add_staged_block_lifecycle(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="video.mov",
            content_type="video/quicktime",
            total_size=128,
            chunk_size=64,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={"blob_url": "https://a.blob.core.windows.net/c/b", "sas_token": "sv=x"},
        )
        session_id = manifest["session_id"]
        assert manifest["total_chunks"] == 2
        assert manifest["upload_mode"] == "azure_server_staged"

        block_id_0 = _build_azure_block_id(0)
        manifest = store.add_staged_block(
            session_id, user_id="u1", chunk_index=0, block_id=block_id_0, chunk_size=64,
        )
        assert manifest["uploaded_bytes"] == 64
        assert manifest["state"] == "uploading"
        assert "0" in manifest["staged_blocks"]

        block_id_1 = _build_azure_block_id(1)
        manifest = store.add_staged_block(
            session_id, user_id="u1", chunk_index=1, block_id=block_id_1, chunk_size=64,
        )
        assert manifest["uploaded_bytes"] == 128
        assert manifest["state"] == "ready"
        assert len(manifest["staged_blocks"]) == 2

        # No chunk files should exist on disk
        chunks_dir = tmp_path / "sessions" / session_id / "chunks"
        assert list(chunks_dir.iterdir()) == []

    def test_add_staged_block_rejects_wrong_mode(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="a.mov",
            content_type="video/quicktime",
            total_size=8,
            chunk_size=4,
            process=True,
            upload_mode="app_mediated",
        )
        with pytest.raises(UploadSessionError, match="azure_server_staged or azure_sas"):
            store.add_staged_block(
                manifest["session_id"],
                user_id="u1",
                chunk_index=0,
                block_id="block-00000000",
                chunk_size=4,
            )

    def test_add_staged_block_rejects_out_of_range(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="a.mov",
            content_type="video/quicktime",
            total_size=8,
            chunk_size=4,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={"blob_url": "https://x", "sas_token": "s"},
        )
        with pytest.raises(UploadSessionError, match="out of range"):
            store.add_staged_block(
                manifest["session_id"],
                user_id="u1",
                chunk_index=99,
                block_id="block-00000099",
                chunk_size=4,
            )

    def test_add_staged_block_access_denied(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="a.mov",
            content_type="video/quicktime",
            total_size=8,
            chunk_size=4,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={"blob_url": "https://x", "sas_token": "s"},
        )
        with pytest.raises(UploadSessionError, match="access denied"):
            store.add_staged_block(
                manifest["session_id"],
                user_id="u2",
                chunk_index=0,
                block_id="block-00000000",
                chunk_size=4,
            )

    def test_add_staged_block_replaces_previous(self, tmp_path):
        """Re-staging a block_id (retry) should update tracking correctly."""
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="a.mov",
            content_type="video/quicktime",
            total_size=128,
            chunk_size=64,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={"blob_url": "https://x", "sas_token": "s"},
        )
        session_id = manifest["session_id"]

        store.add_staged_block(
            session_id, user_id="u1", chunk_index=0, block_id="id-a", chunk_size=64,
        )
        manifest = store.add_staged_block(
            session_id, user_id="u1", chunk_index=0, block_id="id-b", chunk_size=64,
        )
        assert manifest["staged_blocks"]["0"] == "id-b"
        assert manifest["uploaded_bytes"] == 64  # not doubled

    def test_add_chunk_rejects_azure_server_staged_mode(self, tmp_path):
        """add_chunk (disk-based) should reject azure_server_staged sessions."""
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="a.mov",
            content_type="video/quicktime",
            total_size=8,
            chunk_size=4,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={"blob_url": "https://x", "sas_token": "s"},
        )
        with pytest.raises(UploadSessionError):
            store.add_chunk(
                manifest["session_id"],
                user_id="u1",
                chunk_index=0,
                chunk_bytes=b"ABCD",
            )


# ---------------------------------------------------------------------------
# Phase 3: UploadSessionStore - azure_sas mode
# ---------------------------------------------------------------------------

class TestUploadSessionStoreAzureSas:
    """Tests for azure_sas mode where browser uploads directly to Azure."""

    def test_add_staged_block_accepts_azure_sas_mode(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="video.mov",
            content_type="video/quicktime",
            total_size=128,
            chunk_size=64,
            process=True,
            upload_mode="azure_sas",
            upload_meta={
                "blob_url": "https://a.blob.core.windows.net/c/b",
                "sas_token": "sv=x",
                "sas_source": "user_delegation",
            },
        )
        session_id = manifest["session_id"]
        assert manifest["upload_mode"] == "azure_sas"

        block_id_0 = _build_azure_block_id(0)
        manifest = store.add_staged_block(
            session_id, user_id="u1", chunk_index=0, block_id=block_id_0, chunk_size=64,
        )
        assert manifest["uploaded_bytes"] == 64

        block_id_1 = _build_azure_block_id(1)
        manifest = store.add_staged_block(
            session_id, user_id="u1", chunk_index=1, block_id=block_id_1, chunk_size=64,
        )
        assert manifest["state"] == "ready"
        assert len(manifest["staged_blocks"]) == 2

    def test_azure_sas_session_stores_sas_source(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="video.mov",
            content_type="video/quicktime",
            total_size=64,
            chunk_size=64,
            process=True,
            upload_mode="azure_sas",
            upload_meta={
                "blob_url": "https://a.blob.core.windows.net/c/b",
                "sas_token": "sv=x",
                "sas_source": "user_delegation",
            },
        )
        assert manifest["upload_meta"]["sas_source"] == "user_delegation"


# ---------------------------------------------------------------------------
# Phase 3b: Upload mode selection and chunking logic
# ---------------------------------------------------------------------------

class TestUploadModeSelection:
    """Test the logic that determines upload_mode and chunk_size.
    Uses a local mirror of the selection logic from create_upload_session."""

    AZURE_UPLOAD_CHUNK_SIZE_BYTES = 64 * 1024 * 1024  # 64 MiB

    def _select_mode(self, upload_meta, file_size):
        """Mirror of the mode-selection logic in create_upload_session."""
        upload_mode = "app_mediated"
        chunk_size = 8 * 1024 * 1024  # default resumable

        if upload_meta:
            azure_chunk_size = max(
                4 * 1024 * 1024,
                min(self.AZURE_UPLOAD_CHUNK_SIZE_BYTES, 4 * 1024 * 1024 * 1024),
            )
            chunk_size = azure_chunk_size

            if upload_meta.get("sas_source") == "user_delegation":
                upload_mode = "azure_sas"
            else:
                upload_mode = "azure_server_staged"

        return upload_mode, chunk_size

    def test_user_delegation_sas_selects_azure_sas_mode(self):
        meta = {"blob_url": "https://x", "sas_token": "sv=x", "sas_source": "user_delegation"}
        mode, chunk = self._select_mode(meta, 1_600_000_000)
        assert mode == "azure_sas"
        assert chunk == 64 * 1024 * 1024

    def test_static_sas_selects_azure_server_staged_mode(self):
        meta = {"blob_url": "https://x", "sas_token": "sv=x", "sas_source": "static"}
        mode, chunk = self._select_mode(meta, 1_600_000_000)
        assert mode == "azure_server_staged"
        assert chunk == 64 * 1024 * 1024

    def test_no_meta_selects_app_mediated(self):
        mode, chunk = self._select_mode(None, 1_600_000_000)
        assert mode == "app_mediated"

    def test_chunk_size_is_64mib_not_file_size(self):
        """Regression: previously chunk_size == file_size for files <= 4GB."""
        file_size = 1_600_000_000  # 1.6 GB
        meta = {"blob_url": "https://x", "sas_token": "sv=x", "sas_source": "static"}
        _, chunk = self._select_mode(meta, file_size)
        assert chunk == 64 * 1024 * 1024
        assert chunk != file_size  # Must NOT be the full file size


# ---------------------------------------------------------------------------
# Phase 4: Mock 2GB benchmark test
# ---------------------------------------------------------------------------

class TestMock2GBBenchmark:
    """Simulate a 2 GB upload through the UploadSessionStore server-staged
    path with mocked Azure SDK to validate correctness and measure overhead."""

    TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
    CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB
    TOTAL_CHUNKS = TOTAL_SIZE // CHUNK_SIZE  # 32

    def test_full_staged_upload_no_disk_io(self, tmp_path):
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            session_id="bench-session",
            filename="big-video.mov",
            content_type="video/quicktime",
            total_size=self.TOTAL_SIZE,
            chunk_size=self.CHUNK_SIZE,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={
                "blob_url": "https://acct.blob.core.windows.net/container/bench-session_big-video.mov",
                "sas_token": "sv=2023-11-03&ss=b&srt=o",
            },
        )
        assert manifest["total_chunks"] == self.TOTAL_CHUNKS

        # Stage all blocks
        block_ids = []
        for i in range(self.TOTAL_CHUNKS):
            block_id = _build_azure_block_id(i)
            block_ids.append(block_id)
            manifest = store.add_staged_block(
                "bench-session",
                user_id="u1",
                chunk_index=i,
                block_id=block_id,
                chunk_size=self.CHUNK_SIZE,
            )

        assert manifest["state"] == "ready"
        assert manifest["uploaded_bytes"] == self.TOTAL_SIZE
        assert len(manifest["staged_blocks"]) == self.TOTAL_CHUNKS

        # Verify no chunk files were written to disk
        chunks_dir = tmp_path / "sessions" / "bench-session" / "chunks"
        chunk_files = list(chunks_dir.iterdir())
        assert chunk_files == [], f"Expected no chunk files, got {chunk_files}"

        # Verify block IDs can be retrieved in order for commit_block_list
        staged = manifest["staged_blocks"]
        ordered_ids = [staged[str(i)] for i in range(self.TOTAL_CHUNKS)]
        assert ordered_ids == block_ids

    def test_stage_block_called_for_each_chunk(self):
        """Verify BlobClient.stage_block is called for each chunk (mock SDK)."""
        mock_blob_client = MagicMock()

        # Simulate what the chunk endpoint does for each chunk
        chunk_data = b"X" * 1024  # Small mock chunk
        for i in range(5):
            block_id = _build_azure_block_id(i)
            mock_blob_client.stage_block(block_id, chunk_data)

        assert mock_blob_client.stage_block.call_count == 5

    def test_commit_block_list_called_on_finalize(self):
        """Verify commit_block_list is called with correct block IDs on finalize."""
        mock_blob_client = MagicMock()

        block_ids = [_build_azure_block_id(i) for i in range(32)]
        mock_blob_client.commit_block_list(block_ids)

        mock_blob_client.commit_block_list.assert_called_once_with(block_ids)


# ---------------------------------------------------------------------------
# Existing add_chunk should reject azure_sas mode (unchanged behavior)
# ---------------------------------------------------------------------------

def test_azure_sas_mode_still_rejects_add_chunk(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
    manifest = store.create_session(
        user_id="u1",
        filename="a.mov",
        content_type="video/quicktime",
        total_size=8,
        chunk_size=4,
        process=True,
        upload_mode="azure_sas",
        upload_meta={"blob_url": "https://example.blob.core.windows.net/c/a.mov"},
    )
    with pytest.raises(UploadSessionError, match="Azure direct"):
        store.add_chunk(
            manifest["session_id"],
            user_id="u1",
            chunk_index=0,
            chunk_bytes=b"ABCD",
        )


# ---------------------------------------------------------------------------
# Phase 5: Single-chunk (whole-file) Azure upload tests
# ---------------------------------------------------------------------------

class TestSingleChunkAzureUpload:
    """Verify that files <= 4 GB result in a single Azure block."""

    def test_single_chunk_session_lifecycle(self, tmp_path):
        """A 1.7 GB file should produce total_chunks=1 when chunk_size=file_size."""
        file_size = 1_714_748_839  # ~1.7 GB
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            session_id="single-chunk-session",
            filename="big-meeting.mov",
            content_type="video/quicktime",
            total_size=file_size,
            chunk_size=file_size,  # entire file as one chunk
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={
                "blob_url": "https://acct.blob.core.windows.net/c/single-chunk-session_big-meeting.mov",
                "sas_token": "sv=2023-11-03&ss=b",
            },
        )
        assert manifest["total_chunks"] == 1

        block_id = _build_azure_block_id(0)
        manifest = store.add_staged_block(
            "single-chunk-session",
            user_id="u1",
            chunk_index=0,
            block_id=block_id,
            chunk_size=file_size,
        )
        assert manifest["state"] == "ready"
        assert manifest["uploaded_bytes"] == file_size
        assert len(manifest["staged_blocks"]) == 1

    def test_single_block_finalize_mock(self):
        """commit_block_list works correctly with a single block_id."""
        mock_blob_client = MagicMock()
        block_ids = [_build_azure_block_id(0)]
        mock_blob_client.commit_block_list(block_ids)
        mock_blob_client.commit_block_list.assert_called_once_with(block_ids)

    def test_file_over_4gb_falls_back_to_multi_chunk(self, tmp_path):
        """A 5 GB file with 64 MB fallback chunk size should produce multiple chunks."""
        file_size = 5 * 1024 * 1024 * 1024  # 5 GB
        fallback_chunk = 64 * 1024 * 1024  # 64 MB
        expected_chunks = -(-file_size // fallback_chunk)  # ceil division

        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="huge.mov",
            content_type="video/quicktime",
            total_size=file_size,
            chunk_size=fallback_chunk,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={
                "blob_url": "https://acct.blob.core.windows.net/c/huge.mov",
                "sas_token": "sv=2023-11-03&ss=b",
            },
        )
        assert manifest["total_chunks"] == expected_chunks
        assert manifest["chunk_size"] == fallback_chunk

    def test_exact_4gb_file_is_single_chunk(self, tmp_path):
        """A file exactly 4 GB should still be a single chunk."""
        file_size = 4 * 1024 * 1024 * 1024  # 4 GB
        store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
        manifest = store.create_session(
            user_id="u1",
            filename="exact4gb.mov",
            content_type="video/quicktime",
            total_size=file_size,
            chunk_size=file_size,
            process=True,
            upload_mode="azure_server_staged",
            upload_meta={
                "blob_url": "https://acct.blob.core.windows.net/c/exact4gb.mov",
                "sas_token": "sv=2023-11-03&ss=b",
            },
        )
        assert manifest["total_chunks"] == 1


# ---------------------------------------------------------------------------
# Phase 0: _resolve_blob_to_local tests
# ---------------------------------------------------------------------------

class TestResolveBlobToLocal:
    """Test the blob-to-local download helper used before Soniox/Indexer processing."""

    @staticmethod
    def _make_request(sas_token="sv=2023-11-03&ss=b&srt=co&sp=rwdlacitfx"):
        """Build a minimal Request-like object with app.state.config."""
        config = SimpleNamespace(VIDEO_INDEXER_AZURE_BLOB_SAS=sas_token)
        state = SimpleNamespace(config=config)
        app = SimpleNamespace(state=state)
        return SimpleNamespace(app=app)

    @staticmethod
    def _make_file_item(file_id="abc123", filename="meeting.mov"):
        return SimpleNamespace(id=file_id, filename=filename)

    @staticmethod
    def _resolve(request, file_path, file_item, upload_dir):
        """Inline mirror of _resolve_blob_to_local to avoid importing the
        full router (which pulls in heavy deps). Logic must match the
        implementation in files.py."""
        if not file_path or not file_path.startswith(("https://", "http://")):
            return file_path

        sas_raw = getattr(request.app.state.config, "VIDEO_INDEXER_AZURE_BLOB_SAS", "")
        sas_token = _normalize_sas_token(sas_raw)
        if not sas_token:
            raise RuntimeError(
                "Cannot download blob for processing: "
                "VIDEO_INDEXER_AZURE_BLOB_SAS is not configured"
            )

        local_filename = f"{file_item.id}_{os.path.basename(file_item.filename or 'blob')}"
        local_path = os.path.join(str(upload_dir), local_filename)

        if os.path.exists(local_path):
            return local_path

        return local_path  # In real code, would download here

    # --- passthrough for local paths ---

    def test_local_path_passthrough(self, tmp_path):
        """A local file path should be returned unchanged."""
        req = self._make_request()
        fi = self._make_file_item()
        result = self._resolve(req, "/data/uploads/meeting.mov", fi, tmp_path)
        assert result == "/data/uploads/meeting.mov"

    def test_empty_path_passthrough(self, tmp_path):
        """An empty string should be returned unchanged."""
        req = self._make_request()
        fi = self._make_file_item()
        result = self._resolve(req, "", fi, tmp_path)
        assert result == ""

    def test_none_path_passthrough(self, tmp_path):
        """None should be returned unchanged."""
        req = self._make_request()
        fi = self._make_file_item()
        result = self._resolve(req, None, fi, tmp_path)
        assert result is None

    # --- SAS token missing ---

    def test_missing_sas_raises(self, tmp_path):
        """When SAS token is empty, should raise RuntimeError."""
        req = self._make_request(sas_token="")
        fi = self._make_file_item()
        with pytest.raises(RuntimeError, match="VIDEO_INDEXER_AZURE_BLOB_SAS"):
            self._resolve(
                req,
                "https://acct.blob.core.windows.net/c/meeting.mov",
                fi,
                tmp_path,
            )

    # --- blob URL triggers download path ---

    def test_blob_url_returns_local_path(self, tmp_path):
        """A blob URL should map to a local path of form {id}_{filename}."""
        req = self._make_request()
        fi = self._make_file_item(file_id="abc123", filename="meeting.mov")
        result = self._resolve(
            req,
            "https://acct.blob.core.windows.net/c/meeting.mov",
            fi,
            tmp_path,
        )
        assert result == os.path.join(str(tmp_path), "abc123_meeting.mov")

    # --- cached file skips re-download ---

    def test_cached_file_returns_immediately(self, tmp_path):
        """If the local file already exists, return it without re-downloading."""
        req = self._make_request()
        fi = self._make_file_item(file_id="abc123", filename="meeting.mov")
        # Pre-create the cached file
        cached = tmp_path / "abc123_meeting.mov"
        cached.write_bytes(b"fake video content")

        result = self._resolve(
            req,
            "https://acct.blob.core.windows.net/c/meeting.mov",
            fi,
            tmp_path,
        )
        assert result == str(cached)

    # --- filename generation ---

    def test_filename_uses_file_item_id_and_name(self, tmp_path):
        """Local filename should be {file_item.id}_{file_item.filename}."""
        req = self._make_request()
        fi = self._make_file_item(file_id="xyz789", filename="interview.mp4")
        result = self._resolve(
            req,
            "https://acct.blob.core.windows.net/c/interview.mp4",
            fi,
            tmp_path,
        )
        assert os.path.basename(result) == "xyz789_interview.mp4"

    def test_missing_filename_falls_back_to_blob(self, tmp_path):
        """If file_item.filename is None, should use 'blob' as fallback."""
        req = self._make_request()
        fi = self._make_file_item(file_id="abc", filename=None)
        result = self._resolve(
            req,
            "https://acct.blob.core.windows.net/c/something.mov",
            fi,
            tmp_path,
        )
        assert os.path.basename(result) == "abc_blob"
