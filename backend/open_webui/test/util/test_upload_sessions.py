import os

import pytest

from open_webui.utils.upload_sessions import UploadSessionError, UploadSessionStore


def test_upload_session_lifecycle(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)

    manifest = store.create_session(
        user_id="u1",
        filename="meeting.mov",
        content_type="video/quicktime",
        total_size=10,
        chunk_size=4,
        process=True,
        metadata={"language": "en"},
    )

    session_id = manifest["session_id"]
    assert manifest["total_chunks"] == 3

    manifest = store.add_chunk(session_id, user_id="u1", chunk_index=0, chunk_bytes=b"ABCD")
    assert manifest["uploaded_bytes"] == 4

    manifest = store.add_chunk(session_id, user_id="u1", chunk_index=1, chunk_bytes=b"EFGH")
    assert manifest["uploaded_bytes"] == 8

    manifest = store.add_chunk(session_id, user_id="u1", chunk_index=2, chunk_bytes=b"IJ")
    assert manifest["state"] == "ready"

    output_path = tmp_path / "assembled.bin"
    manifest = store.assemble(session_id, user_id="u1", output_path=str(output_path))
    assert manifest["state"] == "assembled"
    assert output_path.read_bytes() == b"ABCDEFGHIJ"

    store.cleanup(session_id)
    assert not (tmp_path / "sessions" / session_id).exists()


def test_upload_session_access_denied(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
    manifest = store.create_session(
        user_id="u1",
        filename="a.mov",
        content_type="video/quicktime",
        total_size=4,
        chunk_size=4,
        process=True,
    )

    with pytest.raises(UploadSessionError, match="access denied"):
        store.add_chunk(
            manifest["session_id"],
            user_id="u2",
            chunk_index=0,
            chunk_bytes=b"ABCD",
        )


def test_upload_session_missing_chunk_on_assemble(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
    manifest = store.create_session(
        user_id="u1",
        filename="a.mov",
        content_type="video/quicktime",
        total_size=8,
        chunk_size=4,
        process=True,
    )
    store.add_chunk(manifest["session_id"], user_id="u1", chunk_index=0, chunk_bytes=b"ABCD")

    with pytest.raises(UploadSessionError, match="incomplete"):
        store.assemble(
            manifest["session_id"],
            user_id="u1",
            output_path=str(tmp_path / "out.bin"),
        )


def test_upload_session_supports_provided_session_id(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
    manifest = store.create_session(
        user_id="u1",
        session_id="fixed-session-id",
        filename="a.mov",
        content_type="video/quicktime",
        total_size=8,
        chunk_size=4,
        process=True,
    )

    assert manifest["session_id"] == "fixed-session-id"


def test_azure_direct_upload_mode_rejects_chunk_endpoint(tmp_path):
    store = UploadSessionStore(base_dir=str(tmp_path / "sessions"), ttl_seconds=3600)
    manifest = store.create_session(
        user_id="u1",
        filename="a.mov",
        content_type="video/quicktime",
        total_size=8,
        chunk_size=4,
        process=True,
        upload_mode="azure_sas",
        upload_meta={"blob_url": "https://example.blob.core.windows.net/container/a.mov"},
    )

    with pytest.raises(UploadSessionError, match="Azure direct"):
        store.add_chunk(
            manifest["session_id"],
            user_id="u1",
            chunk_index=0,
            chunk_bytes=b"ABCD",
        )
