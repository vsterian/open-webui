import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional


class UploadSessionError(Exception):
    """Raised when upload session operations fail."""


class UploadSessionStore:
    def __init__(self, base_dir: str, ttl_seconds: int = 86400):
        self.base_dir = Path(base_dir)
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    def _chunks_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "chunks"

    def _read_manifest(self, session_id: str) -> dict[str, Any]:
        path = self._manifest_path(session_id)
        if not path.exists():
            raise UploadSessionError("Upload session not found")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, session_id: str, manifest: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = session_dir / "manifest.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        tmp_path.replace(self._manifest_path(session_id))

    def _validate_access(
        self,
        manifest: dict[str, Any],
        user_id: Optional[str],
    ) -> None:
        if user_id and manifest.get("user_id") != user_id:
            raise UploadSessionError("Upload session access denied")

    def _check_not_expired(self, manifest: dict[str, Any]) -> None:
        updated_at = int(manifest.get("updated_at") or 0)
        if (time.time() - updated_at) > self.ttl_seconds:
            raise UploadSessionError("Upload session expired")

    def create_session(
        self,
        *,
        user_id: str,
        session_id: Optional[str] = None,
        filename: str,
        content_type: Optional[str],
        total_size: int,
        chunk_size: int,
        process: bool,
        metadata: Optional[dict[str, Any]] = None,
        upload_mode: str = "app_mediated",
        upload_meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if total_size <= 0:
            raise UploadSessionError("Invalid total upload size")
        if chunk_size <= 0:
            raise UploadSessionError("Invalid chunk size")

        filename = os.path.basename(filename)
        total_chunks = int(math.ceil(total_size / chunk_size))
        now = int(time.time())
        session_id = session_id or str(uuid.uuid4())
        manifest: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "filename": filename,
            "content_type": content_type,
            "total_size": int(total_size),
            "chunk_size": int(chunk_size),
            "total_chunks": total_chunks,
            "uploaded_chunks": {},
            "uploaded_bytes": 0,
            "process": bool(process),
            "metadata": metadata or {},
            "upload_mode": upload_mode,
            "upload_meta": upload_meta or {},
            "state": "uploading",
            "created_at": now,
            "updated_at": now,
        }

        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._chunks_dir(session_id).mkdir(parents=True, exist_ok=True)
        self._write_manifest(session_id, manifest)
        return manifest

    def get_session(self, session_id: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        manifest = self._read_manifest(session_id)
        self._validate_access(manifest, user_id)
        self._check_not_expired(manifest)
        return manifest

    def add_chunk(
        self,
        session_id: str,
        *,
        user_id: str,
        chunk_index: int,
        chunk_bytes: bytes,
    ) -> dict[str, Any]:
        manifest = self.get_session(session_id, user_id=user_id)
        if manifest.get("state") != "uploading":
            raise UploadSessionError("Upload session is not accepting chunks")

        if manifest.get("upload_mode") == "azure_sas":
            raise UploadSessionError("Upload session is configured for Azure direct upload")

        if manifest.get("upload_mode") == "azure_server_staged":
            raise UploadSessionError("Upload session is configured for Azure server-staged upload")

        total_chunks = int(manifest.get("total_chunks") or 0)
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise UploadSessionError("Chunk index out of range")
        if not chunk_bytes:
            raise UploadSessionError("Empty chunk is not allowed")

        chunk_name = f"{chunk_index:08d}.part"
        chunk_path = self._chunks_dir(session_id) / chunk_name
        previous_size = int(manifest.get("uploaded_chunks", {}).get(str(chunk_index), 0))

        tmp_path = chunk_path.with_suffix(".tmp")
        with open(tmp_path, "wb") as f:
            f.write(chunk_bytes)
        tmp_path.replace(chunk_path)

        chunk_size = chunk_path.stat().st_size
        uploaded_chunks = manifest.get("uploaded_chunks", {})
        uploaded_chunks[str(chunk_index)] = chunk_size
        manifest["uploaded_chunks"] = uploaded_chunks

        uploaded_bytes = int(manifest.get("uploaded_bytes") or 0)
        uploaded_bytes = uploaded_bytes - previous_size + chunk_size
        manifest["uploaded_bytes"] = max(0, uploaded_bytes)
        manifest["updated_at"] = int(time.time())

        if len(uploaded_chunks) == total_chunks:
            manifest["state"] = "ready"

        self._write_manifest(session_id, manifest)
        return manifest

    def add_staged_block(
        self,
        session_id: str,
        *,
        user_id: str,
        chunk_index: int,
        block_id: str,
        chunk_size: int,
    ) -> dict[str, Any]:
        """Record a block that was already staged to Azure (no local disk I/O)."""
        manifest = self.get_session(session_id, user_id=user_id)
        if manifest.get("state") != "uploading":
            raise UploadSessionError("Upload session is not accepting chunks")
        if manifest.get("upload_mode") != "azure_server_staged":
            raise UploadSessionError("Session is not in azure_server_staged mode")

        total_chunks = int(manifest.get("total_chunks") or 0)
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise UploadSessionError("Chunk index out of range")
        if not block_id:
            raise UploadSessionError("Empty block_id is not allowed")

        staged_blocks = manifest.get("staged_blocks", {})
        uploaded_chunks = manifest.get("uploaded_chunks", {})
        previous_size = int(uploaded_chunks.get(str(chunk_index), 0))

        staged_blocks[str(chunk_index)] = block_id
        uploaded_chunks[str(chunk_index)] = chunk_size
        manifest["staged_blocks"] = staged_blocks
        manifest["uploaded_chunks"] = uploaded_chunks

        uploaded_bytes = int(manifest.get("uploaded_bytes") or 0)
        uploaded_bytes = uploaded_bytes - previous_size + chunk_size
        manifest["uploaded_bytes"] = max(0, uploaded_bytes)
        manifest["updated_at"] = int(time.time())

        if len(staged_blocks) == total_chunks:
            manifest["state"] = "ready"

        self._write_manifest(session_id, manifest)
        return manifest

    def assemble(
        self,
        session_id: str,
        *,
        user_id: str,
        output_path: str,
    ) -> dict[str, Any]:
        manifest = self.get_session(session_id, user_id=user_id)

        total_chunks = int(manifest.get("total_chunks") or 0)
        uploaded_chunks = manifest.get("uploaded_chunks", {})
        if len(uploaded_chunks) != total_chunks:
            raise UploadSessionError("Upload is incomplete")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "wb") as target:
            for index in range(total_chunks):
                if str(index) not in uploaded_chunks:
                    raise UploadSessionError("Missing upload chunk")
                chunk_path = self._chunks_dir(session_id) / f"{index:08d}.part"
                if not chunk_path.exists():
                    raise UploadSessionError("Missing upload chunk file")
                with open(chunk_path, "rb") as source:
                    while True:
                        buf = source.read(1024 * 1024)
                        if not buf:
                            break
                        target.write(buf)

        assembled_size = output.stat().st_size
        expected_size = int(manifest.get("total_size") or 0)
        if expected_size > 0 and assembled_size != expected_size:
            raise UploadSessionError(
                f"Assembled file size mismatch: expected {expected_size}, got {assembled_size}"
            )

        manifest["state"] = "assembled"
        manifest["updated_at"] = int(time.time())
        self._write_manifest(session_id, manifest)
        return manifest

    def cleanup(self, session_id: str) -> None:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return

        for root, dirs, files in os.walk(session_dir, topdown=False):
            for filename in files:
                try:
                    os.remove(Path(root) / filename)
                except OSError:
                    pass
            for dirname in dirs:
                try:
                    os.rmdir(Path(root) / dirname)
                except OSError:
                    pass

        try:
            os.rmdir(session_dir)
        except OSError:
            pass
