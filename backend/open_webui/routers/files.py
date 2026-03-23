import logging
import os
import uuid
import json
import base64
from types import SimpleNamespace
from pathlib import Path
from typing import Optional
from urllib.parse import quote
import asyncio

from fastapi import (
    BackgroundTasks,
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
    Query,
)

from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from open_webui.internal.db import get_session, SessionLocal

from open_webui.constants import ERROR_MESSAGES
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.models.files import (
    FileForm,
    FileModel,
    FileModelResponse,
    Files,
)
from open_webui.models.chats import Chats
from open_webui.models.knowledge import Knowledges
from open_webui.models.groups import Groups
from open_webui.models.access_grants import AccessGrants


from open_webui.routers.retrieval import ProcessFileForm, process_file
from open_webui.routers.audio import transcribe

from open_webui.storage.provider import Storage


from open_webui.config import (
    BYPASS_ADMIN_ACCESS_CONTROL,
    UPLOAD_DIR,
    STORAGE_PROVIDER,
    AZURE_STORAGE_ENDPOINT,
    AZURE_STORAGE_CONTAINER_NAME,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.misc import strict_match_mime_type
from open_webui.utils.upload_sessions import UploadSessionError, UploadSessionStore
from open_webui.utils.video_indexer import (
    VideoIndexerClient,
    VideoIndexerError,
    build_client_from_config,
)
from open_webui.utils.soniox import (
    SonioxError,
    build_soniox_client_from_config,
    compute_ffmpeg_timeout_seconds,
    normalize_audio_file_for_soniox,
)
from pydantic import BaseModel
from azure.storage.blob import BlobClient

log = logging.getLogger(__name__)

router = APIRouter()


from open_webui.utils.access_control.files import has_access_to_file

############################
# Upload File
############################


def _is_text_file(file_path: str, chunk_size: int = 8192) -> bool:
    """Check if a file is likely a text file by reading a chunk and validating UTF-8.

    This catches files whose extensions are mis-mapped by mimetypes/browsers
    (e.g. TypeScript .ts → video/mp2t) without maintaining an extension whitelist.
    """
    try:
        resolved = Storage.get_file(file_path)
        with open(resolved, "rb") as f:
            chunk = f.read(chunk_size)
        if not chunk:
            return False
        # Null bytes are a strong indicator of binary content
        if b"\x00" in chunk:
            return False
        chunk.decode("utf-8")
        return True
    except (UnicodeDecodeError, Exception):
        return False


def process_uploaded_file(
    request,
    file,
    file_path,
    file_item,
    file_metadata,
    user,
    db: Optional[Session] = None,
):
    def _process_handler(db_session):
        try:
            content_type = file.content_type

            # Detect mis-labeled text files (e.g. .ts → video/mp2t)
            if content_type and content_type.startswith(("image/", "video/")):
                if _is_text_file(file_path):
                    content_type = "text/plain"

            if content_type:
                stt_supported_content_types = getattr(
                    request.app.state.config, "STT_SUPPORTED_CONTENT_TYPES", []
                )
                analyzer_enabled = getattr(
                    request.app.state.config,
                    "VIDEO_INDEXER_ENABLED",
                    False,
                )
                analyzer_provider = getattr(
                    request.app.state.config,
                    "VIDEO_INDEXER_PROVIDER",
                    "azure_video_indexer",
                )

                if analyzer_enabled and content_type.startswith(("video/", "audio/")):
                    _process_media_with_analyzer(
                        request=request,
                        file_item=file_item,
                        file_path=file_path,
                        file_metadata=file_metadata,
                        user=user,
                        db_session=db_session,
                        content_type=content_type,
                        provider=analyzer_provider,
                    )

                elif strict_match_mime_type(stt_supported_content_types, content_type):
                    file_path_processed = Storage.get_file(file_path)
                    result = transcribe(
                        request, file_path_processed, file_metadata, user
                    )

                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file_item.id, content=result.get("text", "")
                        ),
                        user=user,
                        db=db_session,
                    )
                elif (not content_type.startswith(("image/", "video/"))) or (
                    request.app.state.config.CONTENT_EXTRACTION_ENGINE == "external"
                ):
                    process_file(
                        request,
                        ProcessFileForm(file_id=file_item.id),
                        user=user,
                        db=db_session,
                    )
                else:
                    raise Exception(
                        f"File type {content_type} is not supported for processing"
                    )
            else:
                log.info(
                    f"File type {file.content_type} is not provided, but trying to process anyway"
                )
                process_file(
                    request,
                    ProcessFileForm(file_id=file_item.id),
                    user=user,
                    db=db_session,
                )

        except Exception as e:
            log.error(f"Error processing file: {file_item.id}")
            Files.update_file_data_by_id(
                file_item.id,
                {
                    "status": "failed",
                    "error": str(e.detail) if hasattr(e, "detail") else str(e),
                },
                db=db_session,
            )

    if db:
        _process_handler(db)
    else:
        with SessionLocal() as db_session:
            _process_handler(db_session)


def _get_upload_session_store(request: Request) -> UploadSessionStore:
    ttl_seconds = int(
        getattr(
            request.app.state.config,
            "RESUMABLE_UPLOAD_SESSION_TTL_SECONDS",
            86400,
        )
        or 86400
    )
    sessions_dir = os.path.join(UPLOAD_DIR, "resumable-sessions")
    return UploadSessionStore(base_dir=sessions_dir, ttl_seconds=ttl_seconds)


class UploadSessionCreateForm(BaseModel):
    filename: str
    size: int
    content_type: Optional[str] = None
    process: bool = True
    metadata: Optional[dict] = None
    chunk_size: Optional[int] = None
    allow_azure_sas: bool = True


class UploadSessionFinalizeForm(BaseModel):
    block_ids: Optional[list[str]] = None


def _is_media_content_type(content_type: Optional[str]) -> bool:
    if not isinstance(content_type, str):
        return False
    return content_type.startswith("video/") or content_type.startswith("audio/")


def _normalize_sas_token(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""
    if "?" in value:
        value = value.split("?", 1)[1]
    return value.strip(" ?&")


def _build_azure_block_id(chunk_index: int) -> str:
    raw = f"block-{chunk_index:08d}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def _build_azure_upload_meta(request: Request, session_id: str, filename: str) -> Optional[dict]:
    sas_token_raw = getattr(request.app.state.config, "VIDEO_INDEXER_AZURE_BLOB_SAS", "")
    sas_token = _normalize_sas_token(sas_token_raw)
    endpoint = str(
        getattr(request.app.state.config, "VIDEO_INDEXER_AZURE_BLOB_ENDPOINT", "")
        or AZURE_STORAGE_ENDPOINT
        or ""
    ).rstrip("/")
    container = str(
        getattr(request.app.state.config, "VIDEO_INDEXER_AZURE_BLOB_CONTAINER", "")
        or AZURE_STORAGE_CONTAINER_NAME
        or ""
    ).strip("/")
    if not sas_token or not endpoint or not container:
        return None

    blob_name = f"{session_id}_{os.path.basename(filename)}"
    blob_url = f"{endpoint}/{container}/{quote(blob_name)}"
    return {
        "blob_name": blob_name,
        "blob_url": blob_url,
        "sas_token": sas_token,
    }


def _resolve_blob_to_local(request: Request, file_path: str, file_item) -> str:
    """Download an Azure blob to local disk if file_path is a remote URL.

    After azure_server_staged uploads the file record stores a blob URL
    (https://...) as file_path.  Processing code (ffmpeg, Soniox upload)
    requires a local file.  This helper transparently downloads the blob
    using the Video Indexer SAS token and returns a local path.  If the
    path is already local it is returned unchanged.
    """
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
    local_path = os.path.join(UPLOAD_DIR, local_filename)

    if os.path.exists(local_path):
        log.info("Blob already downloaded locally: %s", local_path)
        return local_path

    log.info("Downloading blob to local disk: %s -> %s", file_path, local_path)
    blob_client = BlobClient.from_blob_url(f"{file_path}?{sas_token}")
    tmp_path = local_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            stream = blob_client.download_blob()
            stream.readinto(f)
        os.replace(tmp_path, local_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    log.info("Blob downloaded successfully: %s (%d bytes)", local_path, os.path.getsize(local_path))
    return local_path


def _create_uploaded_file_record(
    *,
    user,
    filename: str,
    file_path: str,
    content_type: Optional[str],
    size: int,
    process: bool,
    file_metadata: dict,
    db: Session,
):
    file_id = str(uuid.uuid4())
    file_item = Files.insert_new_file(
        user.id,
        FileForm(
            **{
                "id": file_id,
                "filename": filename,
                "path": file_path,
                "data": {
                    **({"status": "pending"} if process else {}),
                },
                "meta": {
                    "name": filename,
                    "content_type": content_type if isinstance(content_type, str) else None,
                    "size": int(size),
                    "data": file_metadata,
                },
            }
        ),
        db=db,
    )

    if "channel_id" in file_metadata:
        channel = Channels.get_channel_by_id_and_user_id(
            file_metadata["channel_id"], user.id, db=db
        )
        if channel:
            Channels.add_file_to_channel_by_id(channel.id, file_item.id, user.id, db=db)

    return file_item


@router.post("/uploads/sessions")
def create_upload_session(
    request: Request,
    form_data: UploadSessionCreateForm,
    user=Depends(get_verified_user),
):
    if form_data.size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Invalid upload size"),
        )

    default_chunk_size = int(
        getattr(
            request.app.state.config,
            "RESUMABLE_UPLOAD_CHUNK_SIZE_BYTES",
            8 * 1024 * 1024,
        )
        or (8 * 1024 * 1024)
    )
    chunk_size = int(form_data.chunk_size or default_chunk_size)

    if chunk_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Invalid chunk size"),
        )

    if not form_data.filename or not os.path.basename(form_data.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Invalid filename"),
        )

    store = _get_upload_session_store(request)
    try:
        session_id = str(uuid.uuid4())
        upload_meta = None
        upload_mode = "app_mediated"
        if _is_media_content_type(form_data.content_type):
            upload_meta = _build_azure_upload_meta(request, session_id, form_data.filename)
            if upload_meta:
                upload_mode = "azure_server_staged"
                # Send the entire file as a single Azure Put Block call
                # (Azure SDK v12 supports up to 4 GB per block).
                # For files larger than 4 GB, fall back to multi-block with
                # AZURE_UPLOAD_CHUNK_SIZE_BYTES.
                max_single_block = 4 * 1024 * 1024 * 1024  # 4 GB
                if form_data.size <= max_single_block:
                    chunk_size = form_data.size
                else:
                    azure_chunk_size = int(
                        getattr(
                            request.app.state.config,
                            "AZURE_UPLOAD_CHUNK_SIZE_BYTES",
                            64 * 1024 * 1024,
                        )
                        or (64 * 1024 * 1024)
                    )
                    chunk_size = max(4 * 1024 * 1024, min(azure_chunk_size, max_single_block))

        session = store.create_session(
            user_id=user.id,
            session_id=session_id,
            filename=form_data.filename,
            content_type=form_data.content_type,
            total_size=form_data.size,
            chunk_size=chunk_size,
            process=form_data.process,
            metadata=form_data.metadata or {},
            upload_mode=upload_mode,
            upload_meta=upload_meta,
        )
    except UploadSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(exc)),
        )

    log.info(
        "Upload session created: session_id=%s, mode=%s, chunks=%d, chunk_size=%d, total=%d",
        session["session_id"],
        session.get("upload_mode", "app_mediated"),
        session["total_chunks"],
        session["chunk_size"],
        session.get("total_size", 0),
    )

    response = {
        "session_id": session["session_id"],
        "filename": session["filename"],
        "chunk_size": session["chunk_size"],
        "total_chunks": session["total_chunks"],
        "uploaded_bytes": session["uploaded_bytes"],
        "state": session["state"],
        "upload_mode": session.get("upload_mode", "app_mediated"),
    }

    if session.get("upload_mode") == "azure_sas":
        response["azure_blob_url"] = session.get("upload_meta", {}).get("blob_url", "")
        response["azure_blob_sas_token"] = session.get("upload_meta", {}).get(
            "sas_token", ""
        )

    return response


@router.get("/uploads/sessions/{session_id}")
def get_upload_session(
    request: Request,
    session_id: str,
    user=Depends(get_verified_user),
):
    store = _get_upload_session_store(request)
    try:
        session = store.get_session(session_id, user_id=user.id)
    except UploadSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.DEFAULT(str(exc)),
        )

    return {
        "session_id": session["session_id"],
        "filename": session["filename"],
        "chunk_size": session["chunk_size"],
        "total_chunks": session["total_chunks"],
        "uploaded_chunks": len(session.get("uploaded_chunks", {})),
        "uploaded_bytes": session.get("uploaded_bytes", 0),
        "state": session.get("state", "uploading"),
    }


@router.put("/uploads/sessions/{session_id}/chunks/{chunk_index}")
async def upload_session_chunk(
    request: Request,
    session_id: str,
    chunk_index: int,
    user=Depends(get_verified_user),
):
    body = await request.body()
    store = _get_upload_session_store(request)

    try:
        session = store.get_session(session_id, user_id=user.id)

        if session.get("upload_mode") == "azure_server_staged":
            upload_meta = session.get("upload_meta", {})
            blob_url = upload_meta.get("blob_url", "")
            sas_token = upload_meta.get("sas_token", "")
            if not blob_url or not sas_token:
                raise UploadSessionError("Azure server-staged metadata is missing")

            block_id = _build_azure_block_id(chunk_index)
            blob_client = BlobClient.from_blob_url(f"{blob_url}?{sas_token}")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, blob_client.stage_block, block_id, body
            )

            session = store.add_staged_block(
                session_id,
                user_id=user.id,
                chunk_index=chunk_index,
                block_id=block_id,
                chunk_size=len(body),
            )
            log.debug(
                "Azure staged block %d/%d (%d bytes) for session=%s",
                chunk_index + 1,
                session.get("total_chunks", 0),
                len(body),
                session_id,
            )
        else:
            session = store.add_chunk(
                session_id,
                user_id=user.id,
                chunk_index=chunk_index,
                chunk_bytes=body,
            )
    except UploadSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(exc)),
        )

    return {
        "session_id": session["session_id"],
        "uploaded_chunks": len(session.get("uploaded_chunks", {})),
        "uploaded_bytes": session.get("uploaded_bytes", 0),
        "total_chunks": session.get("total_chunks", 0),
        "state": session.get("state", "uploading"),
    }


@router.post("/uploads/sessions/{session_id}/finalize", response_model=FileModelResponse)
def finalize_upload_session(
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str,
    form_data: Optional[UploadSessionFinalizeForm] = None,
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    store = _get_upload_session_store(request)
    assembled_path = os.path.join(UPLOAD_DIR, f"assembled-{session_id}.bin")

    try:
        session = store.get_session(session_id, user_id=user.id)
        if session.get("upload_mode") in ("azure_sas", "azure_server_staged"):
            upload_meta = session.get("upload_meta", {})
            blob_url = upload_meta.get("blob_url", "")
            sas_token = upload_meta.get("sas_token", "")
            if not blob_url or not sas_token:
                raise UploadSessionError("Azure upload metadata is missing")

            if session.get("upload_mode") == "azure_server_staged":
                staged_blocks = session.get("staged_blocks", {})
                total_chunks = int(session.get("total_chunks") or 0)
                if len(staged_blocks) != total_chunks:
                    raise UploadSessionError(
                        f"Upload incomplete: {len(staged_blocks)}/{total_chunks} blocks staged"
                    )
                block_ids = [
                    staged_blocks[str(i)]
                    for i in range(total_chunks)
                ]
            elif form_data and form_data.block_ids:
                block_ids = form_data.block_ids
            else:
                block_ids = [
                    _build_azure_block_id(index)
                    for index in range(int(session.get("total_chunks") or 0))
                ]

            if not block_ids:
                raise UploadSessionError("No Azure block ids available for finalize")

            blob_client = BlobClient.from_blob_url(f"{blob_url}?{sas_token}")
            blob_client.commit_block_list(block_ids)
            log.info(
                "Azure upload finalized: session=%s, blocks=%d, blob=%s",
                session_id, len(block_ids), blob_url,
            )

            file_item = _create_uploaded_file_record(
                user=user,
                filename=session["filename"],
                file_path=blob_url,
                content_type=session.get("content_type"),
                size=int(session.get("total_size") or 0),
                process=bool(session.get("process", True)),
                file_metadata=session.get("metadata") or {},
                db=db,
            )

            if bool(session.get("process", True)):
                pseudo_upload = SimpleNamespace(content_type=session.get("content_type"))
                if background_tasks and process_in_background:
                    background_tasks.add_task(
                        process_uploaded_file,
                        request,
                        pseudo_upload,
                        blob_url,
                        file_item,
                        session.get("metadata") or {},
                        user,
                    )
                else:
                    process_uploaded_file(
                        request,
                        pseudo_upload,
                        blob_url,
                        file_item,
                        session.get("metadata") or {},
                        user,
                        db=db,
                    )

            return {"status": True, **file_item.model_dump()}

        store.assemble(
            session_id,
            user_id=user.id,
            output_path=assembled_path,
        )

        with open(assembled_path, "rb") as assembled_file:
            pseudo_upload = SimpleNamespace(
                filename=session["filename"],
                content_type=session.get("content_type"),
                file=assembled_file,
            )
            result = upload_file_handler(
                request,
                file=pseudo_upload,
                metadata=session.get("metadata") or {},
                process=bool(session.get("process", True)),
                process_in_background=process_in_background,
                user=user,
                background_tasks=background_tasks,
                db=db,
            )
        return result
    except UploadSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(exc)),
        )
    finally:
        try:
            if os.path.exists(assembled_path):
                os.remove(assembled_path)
        except OSError:
            log.warning("Failed to remove assembled upload file: %s", assembled_path)
        store.cleanup(session_id)


def _process_video_with_indexer(
    request, file_item, file_path, file_metadata, user, db_session, media_type="video"
):
    """
    Upload a video to Azure AI Video Indexer, poll until processed,
    extract transcript + insights, and feed the content into the
    existing RAG pipeline (chunk → embed → vector store).
    """
    try:
        client = build_client_from_config(request.app.state.config)
        file_path = _resolve_blob_to_local(request, file_path, file_item)
        resolved_path = Storage.get_file(file_path)

        language = (
            (file_metadata or {}).get("language")
            or request.app.state.config.VIDEO_INDEXER_LANGUAGE
            or "en-US"
        )
        preset = request.app.state.config.VIDEO_INDEXER_INDEXING_PRESET or "Default"
        if media_type == "audio":
            preset = "AudioOnly"

        # 1. Upload to Video Indexer
        # Notify frontend that upload is in progress
        Files.update_file_data_by_id(
            file_item.id,
            {"status": "pending", "progress": "Uploading to Video Indexer..."},
            db=db_session,
        )

        video_id = client.upload_video(
            file_path=resolved_path,
            video_name=file_item.filename,
            language=language,
            indexing_preset=preset,
        )

        # Build the VI player URL
        player_url = client.get_player_url(video_id)

        # Store VI video_id in file metadata
        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        meta["analyzer_provider"] = "azure_video_indexer"
        meta["analyzer"] = {
            "provider": "azure_video_indexer",
            "video_id": video_id,
            "state": "Processing",
            "progress": "0%",
            "player_url": player_url,
        }
        meta["video_indexer"] = {
            "video_id": video_id,
            "state": "Processing",
            "progress": "0%",
            "player_url": player_url,
        }
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)

        # 2. Check if the video is already processed (e.g. 409 duplicate reuse)
        status_info = client.get_video_status(video_id)
        current_state = status_info.get("state", "")

        if current_state == "Processed":
            log.info(f"Video {video_id} already processed — skipping polling")
            meta["video_indexer"]["progress"] = "100%"
            meta["video_indexer"]["state"] = "Processed"
            meta["analyzer"]["progress"] = "100%"
            meta["analyzer"]["state"] = "Processed"
            Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
            Files.update_file_data_by_id(
                file_item.id,
                {"status": "pending", "progress": "100%"},
                db=db_session,
            )
        else:
            # Poll until indexing completes, updating progress in file.data
            log.info(f"Waiting for VI indexing to complete for video_id={video_id}")
            import time as _time

            poll_interval = 15
            timeout = 3600
            start = _time.time()
            while True:
                elapsed = _time.time() - start
                if elapsed > timeout:
                    raise Exception(
                        f"Indexing timed out after {timeout}s for video {video_id}"
                    )

                status_info = client.get_video_status(video_id)
                state = status_info["state"]
                progress = status_info.get("processingProgress", "0%")
                log.info(
                    f"VI video {video_id}: state={state}, progress={progress}, "
                    f"elapsed={int(elapsed)}s"
                )

                # Persist progress so the SSE endpoint can relay it
                meta["video_indexer"]["state"] = state
                meta["video_indexer"]["progress"] = progress
                meta["analyzer"]["state"] = state
                meta["analyzer"]["progress"] = progress
                Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
                Files.update_file_data_by_id(
                    file_item.id,
                    {"status": "pending", "progress": progress},
                    db=db_session,
                )

                if state == "Processed":
                    log.info(f"VI indexing complete for video_id={video_id}")
                    break
                if state == "Failed":
                    raise Exception(
                        f"Video indexing failed for {video_id}"
                    )

                _time.sleep(poll_interval)

        # 3. Fetch insights + transcript
        index_data = client.get_video_index(video_id)
        log.info(
            f"VI index_data for video_id={video_id}: "
            f"state={index_data.get('state')}, "
            f"num_videos={len(index_data.get('videos', []))}, "
            f"has_summarizedInsights={'summarizedInsights' in index_data}"
        )

        transcript_text = client.get_transcript(video_id, fmt="txt")
        log.info(
            f"VI transcript for video_id={video_id}: "
            f"{len(transcript_text)} chars"
        )

        structured_content = VideoIndexerClient.extract_structured_content(index_data)
        log.info(
            f"VI structured_content for video_id={video_id}: "
            f"{len(structured_content)} chars"
        )

        # Prefer structured content (transcript + keywords + topics etc.)
        # over raw plain transcript for richer RAG retrieval.
        # If structured_content is empty (no insights extracted), fall back
        # to the plain transcript from the Captions API.
        if structured_content:
            final_content = structured_content
        elif transcript_text and transcript_text.strip():
            final_content = f"## Video Transcript\n\n{transcript_text.strip()}"
        else:
            final_content = "(No insights or transcript could be extracted from this video)"

        # Prepend a "Video Source" section with the VI player URL
        player_url = client.get_player_url(video_id)
        source_section = (
            f"## Video Source\n\n"
            f"**File:** {file_item.filename}\n"
            f"**Azure Video Indexer:** {player_url}\n"
        )
        final_content = source_section + "\n\n" + final_content

        # 4. Persist insights in file metadata
        meta["video_indexer"]["state"] = "Processed"
        meta["video_indexer"]["progress"] = "100%"
        meta["video_indexer"]["insights"] = index_data.get("summarizedInsights", {})
        meta["analyzer"]["state"] = "Processed"
        meta["analyzer"]["progress"] = "100%"
        meta["analyzer"]["insights"] = index_data.get("summarizedInsights", {})
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)

        # 5. Send the extracted text through the RAG pipeline
        process_file(
            request,
            ProcessFileForm(
                file_id=file_item.id,
                content=final_content,
            ),
            user=user,
            db=db_session,
        )
        log.info(
            f"Video Indexer processing complete for file {file_item.id} "
            f"(VI video_id={video_id})"
        )

    except VideoIndexerError as exc:
        log.error(f"Video Indexer error for file {file_item.id}: {exc}")
        # Update metadata with failure state
        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        analyzer = meta.get("analyzer", {})
        analyzer["provider"] = "azure_video_indexer"
        analyzer["state"] = "Failed"
        analyzer["error"] = str(exc)
        meta["analyzer"] = analyzer
        vi = meta.get("video_indexer", {})
        vi["state"] = "Failed"
        vi["error"] = str(exc)
        meta["video_indexer"] = vi
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
        raise Exception(f"Video Indexer processing failed: {exc}")

    except Exception as exc:
        log.error(f"Unexpected error in Video Indexer for file {file_item.id}: {exc}")
        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        analyzer = meta.get("analyzer", {})
        analyzer["provider"] = "azure_video_indexer"
        analyzer["state"] = "Failed"
        analyzer["error"] = str(exc)
        meta["analyzer"] = analyzer
        vi = meta.get("video_indexer", {})
        vi["state"] = "Failed"
        vi["error"] = str(exc)
        meta["video_indexer"] = vi
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
        raise


def _process_media_with_soniox(
    request, file_item, file_path, file_metadata, user, db_session
):
    normalized_path = None
    resolved_path = None
    try:
        client = build_soniox_client_from_config(request.app.state.config)
        file_path = _resolve_blob_to_local(request, file_path, file_item)
        resolved_path = Storage.get_file(file_path)
        content_type = (
            (file_item.meta or {}).get("content_type")
            if isinstance(file_item.meta, dict)
            else None
        )

        source_suffix = Path(file_item.filename or resolved_path).suffix.lower()
        is_video_input = (content_type or "").lower().startswith("video/") or source_suffix in {
            ".mp4",
            ".m4v",
            ".mov",
            ".qt",
            ".mkv",
            ".avi",
            ".wmv",
            ".flv",
            ".mpeg",
            ".mpg",
        }

        preprocessing = {
            "mode": "video_to_audio" if is_video_input else "passthrough",
            "state": "skipped",
        }

        if is_video_input:
            Files.update_file_data_by_id(
                file_item.id,
                {"status": "pending", "progress": "Extracting audio for Soniox..."},
                db=db_session,
            )

        ffmpeg_timeout_seconds = compute_ffmpeg_timeout_seconds(
            resolved_path,
            base_timeout_seconds=int(
                getattr(
                    request.app.state.config,
                    "SONIOX_VIDEO_FFMPEG_BASE_TIMEOUT_SECONDS",
                    300,
                )
                or 300
            ),
            timeout_per_mb_seconds=float(
                getattr(
                    request.app.state.config,
                    "SONIOX_VIDEO_FFMPEG_TIMEOUT_PER_MB_SECONDS",
                    0.5,
                )
                or 0.5
            ),
            max_timeout_seconds=int(
                getattr(
                    request.app.state.config,
                    "SONIOX_VIDEO_FFMPEG_MAX_TIMEOUT_SECONDS",
                    7200,
                )
                or 7200
            ),
        )

        try:
            normalized_path, normalized_filename = normalize_audio_file_for_soniox(
                resolved_path,
                filename=file_item.filename,
                content_type=content_type,
                ffmpeg_timeout_seconds=ffmpeg_timeout_seconds,
            )
            if is_video_input:
                preprocessing["state"] = "completed"
                preprocessing["extracted_file"] = normalized_filename
                preprocessing["ffmpeg_timeout_seconds"] = ffmpeg_timeout_seconds
        except SonioxError as exc:
            if not is_video_input:
                raise

            log.warning(
                "Audio extraction failed for %s, falling back to original upload: %s",
                file_item.id,
                exc,
            )
            normalized_path, normalized_filename = resolved_path, file_item.filename
            preprocessing["state"] = "fallback"
            preprocessing["error"] = str(exc)

        Files.update_file_data_by_id(
            file_item.id,
            {"status": "pending", "progress": "Uploading to Soniox..."},
            db=db_session,
        )

        def _on_soniox_progress(status_value, progress, elapsed):
            if progress is not None:
                progress_str = f"{progress}%"
            elif status_value == "queued":
                progress_str = "Queued for transcription..."
            else:
                progress_str = f"Transcribing... ({int(elapsed)}s)"
            log.info(
                f"Soniox transcription {file_item.id}: status={status_value}, "
                f"progress={progress_str}, elapsed={int(elapsed)}s"
            )
            meta = file_item.meta if isinstance(file_item.meta, dict) else {}
            analyzer = meta.get("analyzer", {})
            analyzer["state"] = status_value.capitalize()
            analyzer["progress"] = progress_str
            meta["analyzer"] = analyzer
            Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
            Files.update_file_data_by_id(
                file_item.id,
                {"status": "pending", "progress": progress_str},
                db=db_session,
            )

        result = client.transcribe_file(
            file_path=normalized_path,
            filename=normalized_filename,
            client_reference_id=file_item.id,
            poll_interval=10,
            timeout=3600,
            on_progress=_on_soniox_progress,
        )

        transcript_text = result.get("text") or ""
        if not transcript_text:
            transcript_text = "(No transcript returned by Soniox)"

        language_info = result.get("languages") or []
        language_label = ", ".join(language_info) if language_info else "unknown"

        speakers = result.get("speakers") or []
        speaker_segments = result.get("speaker_segments") or []
        translated_text = result.get("translated_text") or ""

        # Build rich content for RAG pipeline
        header = f"[Source: {file_item.filename} | Provider: Soniox | Languages: {language_label}"
        if speakers:
            header += f" | Speakers: {len(speakers)}"
        header += "]"

        content_parts = [header, ""]

        if speaker_segments:
            content_parts.append("## Transcript (by speaker)\n")
            for seg in speaker_segments:
                start = seg.get("start_s")
                ts = f" [{start:.1f}s]" if start is not None else ""
                content_parts.append(f"**Speaker {seg['speaker']}**{ts}: {seg['text']}")
            content_parts.append("")
        else:
            content_parts.append(transcript_text)

        if translated_text:
            content_parts.append("\n## Translation\n")
            content_parts.append(translated_text)

        final_content = "\n".join(content_parts)

        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        meta["analyzer_provider"] = "soniox"
        meta["analyzer"] = {
            "provider": "soniox",
            "file_id": result.get("file_id"),
            "transcription_id": result.get("transcription_id"),
            "state": str(result.get("status", "completed")).capitalize(),
            "progress": "100%",
            "preprocessing": preprocessing,
            "insights": {
                "languages": language_info,
                "speakers": speakers,
                "speaker_segments": speaker_segments,
                "translated_text": translated_text,
                "tokens": result.get("tokens") or [],
                "transcription": result.get("transcription") or {},
                "transcript": result.get("transcript") or {},
            },
        }
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)

        process_file(
            request,
            ProcessFileForm(
                file_id=file_item.id,
                content=final_content,
            ),
            user=user,
            db=db_session,
        )
        log.info(
            f"Soniox processing complete for file {file_item.id} "
            f"(transcription_id={result.get('transcription_id')})"
        )
    except SonioxError as exc:
        log.error(f"Soniox error for file {file_item.id}: {exc}")
        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        meta["analyzer_provider"] = "soniox"
        analyzer = meta.get("analyzer", {})
        analyzer["provider"] = "soniox"
        analyzer["state"] = "Failed"
        analyzer["error"] = str(exc)
        meta["analyzer"] = analyzer
        Files.update_file_metadata_by_id(file_item.id, meta, db=db_session)
        raise Exception(f"Soniox processing failed: {exc}")
    finally:
        if normalized_path and resolved_path and normalized_path != resolved_path:
            try:
                os.remove(normalized_path)
            except OSError:
                log.warning(
                    "Failed to delete temporary Soniox normalized file: %s",
                    normalized_path,
                )


def _process_media_with_analyzer(
    request,
    file_item,
    file_path,
    file_metadata,
    user,
    db_session,
    content_type,
    provider,
):
    if provider == "soniox":
        _process_media_with_soniox(
            request,
            file_item,
            file_path,
            file_metadata,
            user,
            db_session,
        )
        return

    media_type = "audio" if content_type.startswith("audio/") else "video"
    _process_video_with_indexer(
        request,
        file_item,
        file_path,
        file_metadata,
        user,
        db_session,
        media_type=media_type,
    )


@router.post("/", response_model=FileModelResponse)
def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    metadata: Optional[dict | str] = Form(None),
    process: bool = Query(True),
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    return upload_file_handler(
        request,
        file=file,
        metadata=metadata,
        process=process,
        process_in_background=process_in_background,
        user=user,
        background_tasks=background_tasks,
        db=db,
    )


def upload_file_handler(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[dict | str] = Form(None),
    process: bool = Query(True),
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    background_tasks: Optional[BackgroundTasks] = None,
    db: Optional[Session] = None,
):
    log.info(f"file.content_type: {file.content_type} {process}")

    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Invalid metadata format"),
            )
    file_metadata = metadata if metadata else {}

    try:
        unsanitized_filename = file.filename
        filename = os.path.basename(unsanitized_filename)

        file_extension = os.path.splitext(filename)[1]
        # Remove the leading dot from the file extension
        file_extension = file_extension[1:] if file_extension else ""

        if process and request.app.state.config.ALLOWED_FILE_EXTENSIONS:
            request.app.state.config.ALLOWED_FILE_EXTENSIONS = [
                ext for ext in request.app.state.config.ALLOWED_FILE_EXTENSIONS if ext
            ]

            if file_extension not in request.app.state.config.ALLOWED_FILE_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(
                        f"File type {file_extension} is not allowed"
                    ),
                )

        # replace filename with uuid
        id = str(uuid.uuid4())
        name = filename
        filename = f"{id}_{filename}"
        contents, file_path = Storage.upload_file(
            file.file,
            filename,
            {
                "OpenWebUI-User-Email": user.email,
                "OpenWebUI-User-Id": user.id,
                "OpenWebUI-User-Name": user.name,
                "OpenWebUI-File-Id": id,
            },
        )

        file_item = Files.insert_new_file(
            user.id,
            FileForm(
                **{
                    "id": id,
                    "filename": name,
                    "path": file_path,
                    "data": {
                        **({"status": "pending"} if process else {}),
                    },
                    "meta": {
                        "name": name,
                        "content_type": (
                            file.content_type
                            if isinstance(file.content_type, str)
                            else None
                        ),
                        "size": len(contents),
                        "data": file_metadata,
                    },
                }
            ),
            db=db,
        )

        if "channel_id" in file_metadata:
            channel = Channels.get_channel_by_id_and_user_id(
                file_metadata["channel_id"], user.id, db=db
            )
            if channel:
                Channels.add_file_to_channel_by_id(
                    channel.id, file_item.id, user.id, db=db
                )

        if process:
            if background_tasks and process_in_background:
                background_tasks.add_task(
                    process_uploaded_file,
                    request,
                    file,
                    file_path,
                    file_item,
                    file_metadata,
                    user,
                )
                return {"status": True, **file_item.model_dump()}
            else:
                process_uploaded_file(
                    request,
                    file,
                    file_path,
                    file_item,
                    file_metadata,
                    user,
                    db=db,
                )
                return {"status": True, **file_item.model_dump()}
        else:
            if file_item:
                return file_item
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error uploading file"),
                )

    except HTTPException as e:
        raise e
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error uploading file"),
        )


############################
# List Files
############################


@router.get("/", response_model=list[FileModelResponse])
async def list_files(
    user=Depends(get_verified_user),
    content: bool = Query(True),
    db: Session = Depends(get_session),
):
    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        files = Files.get_files(db=db)
    else:
        files = Files.get_files_by_user_id(user.id, db=db)

    if not content:
        for file in files:
            if "content" in file.data:
                del file.data["content"]

    return files


############################
# Search Files
############################


@router.get("/search", response_model=list[FileModelResponse])
async def search_files(
    filename: str = Query(
        ...,
        description="Filename pattern to search for. Supports wildcards such as '*.txt'",
    ),
    content: bool = Query(True),
    skip: int = Query(0, ge=0, description="Number of files to skip"),
    limit: int = Query(
        100, ge=1, le=1000, description="Maximum number of files to return"
    ),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    """
    Search for files by filename with support for wildcard patterns.
    Uses SQL-based filtering with pagination for better performance.
    """
    # Determine user_id: null for admin with bypass (search all), user.id otherwise
    user_id = (
        None if (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL) else user.id
    )

    # Use optimized database query with pagination
    files = Files.search_files(
        user_id=user_id,
        filename=filename,
        skip=skip,
        limit=limit,
        db=db,
    )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No files found matching the pattern.",
        )

    if not content:
        for file in files:
            if file.data and "content" in file.data:
                del file.data["content"]

    return files


############################
# Delete All Files
############################


@router.delete("/all")
async def delete_all_files(
    user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    result = Files.delete_all_files(db=db)
    if result:
        try:
            Storage.delete_all_files()
            VECTOR_DB_CLIENT.reset()
        except Exception as e:
            log.exception(e)
            log.error("Error deleting files")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
            )
        return {"message": "All files deleted successfully"}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
        )


############################
# Get File By Id
############################


@router.get("/{id}", response_model=Optional[FileModel])
async def get_file_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        return file
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/{id}/process/status")
async def get_file_process_status(
    id: str,
    stream: bool = Query(False),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        if stream:
            MAX_FILE_PROCESSING_DURATION = 3600 * 2

            async def event_stream(file_id):
                # NOTE: We intentionally do NOT capture the request's db session here.
                # Each poll creates its own short-lived session to avoid holding a
                # connection for hours. A WebSocket push would be more efficient.
                for _ in range(MAX_FILE_PROCESSING_DURATION):
                    file_item = Files.get_file_by_id(file_id)  # Creates own session
                    if file_item:
                        data = file_item.model_dump().get("data", {})
                        status = data.get("status")

                        if status:
                            event = {"status": status}
                            if data.get("progress"):
                                event["progress"] = data["progress"]
                            if status == "failed":
                                event["error"] = data.get("error")

                            yield f"data: {json.dumps(event)}\n\n"
                            if status in ("completed", "failed"):
                                break
                        else:
                            # Legacy
                            break
                    else:
                        yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                        break

                    await asyncio.sleep(1)

            return StreamingResponse(
                event_stream(file.id),
                media_type="text/event-stream",
            )
        else:
            data = file.data or {}
            response = {"status": data.get("status", "pending")}
            if data.get("progress"):
                response["progress"] = data.get("progress")
            if data.get("error"):
                response["error"] = data.get("error")
            return response
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Get File Data Content By Id
############################


@router.get("/{id}/data/content")
async def get_file_data_content_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        return {"content": file.data.get("content", "")}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update File Data Content By Id
############################


class ContentForm(BaseModel):
    content: str


@router.post("/{id}/data/content/update")
def update_file_data_content_by_id(
    request: Request,
    id: str,
    form_data: ContentForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "write", user, db=db)
    ):
        try:
            process_file(
                request,
                ProcessFileForm(file_id=id, content=form_data.content),
                user=user,
                db=db,
            )
            file = Files.get_file_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            log.error(f"Error processing file: {file.id}")

        # Propagate content change to all knowledge collections referencing
        # this file.  Without this the old embeddings remain in the knowledge
        # collection and RAG returns both stale and current data (#20558).
        knowledges = Knowledges.get_knowledges_by_file_id(id, db=db)
        for knowledge in knowledges:
            try:
                # Remove old embeddings for this file from the KB collection
                VECTOR_DB_CLIENT.delete(
                    collection_name=knowledge.id, filter={"file_id": id}
                )
                # Re-add from the now-updated file-{file_id} collection
                process_file(
                    request,
                    ProcessFileForm(file_id=id, collection_name=knowledge.id),
                    user=user,
                    db=db,
                )
            except Exception as e:
                log.warning(
                    f"Failed to update knowledge {knowledge.id} after "
                    f"content change for file {id}: {e}"
                )

        return {"content": file.data.get("content", "")}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Get File Content By Id
############################


@router.get("/{id}/content")
async def get_file_content_by_id(
    id: str,
    user=Depends(get_verified_user),
    attachment: bool = Query(False),
    db: Session = Depends(get_session),
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        try:
            file_path = Storage.get_file(file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                # Handle Unicode filenames
                filename = file.meta.get("name", file.filename)
                encoded_filename = quote(filename)  # RFC5987 encoding

                content_type = file.meta.get("content_type")
                filename = file.meta.get("name", file.filename)
                encoded_filename = quote(filename)
                headers = {}

                if attachment:
                    headers["Content-Disposition"] = (
                        f"attachment; filename*=UTF-8''{encoded_filename}"
                    )
                else:
                    if content_type == "application/pdf" or filename.lower().endswith(
                        ".pdf"
                    ):
                        headers["Content-Disposition"] = (
                            f"inline; filename*=UTF-8''{encoded_filename}"
                        )
                        content_type = "application/pdf"
                    elif content_type != "text/plain":
                        headers["Content-Disposition"] = (
                            f"attachment; filename*=UTF-8''{encoded_filename}"
                        )

                return FileResponse(file_path, headers=headers, media_type=content_type)

            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except HTTPException as e:
            raise e
        except Exception as e:
            log.exception(e)
            log.error("Error getting file content")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error getting file content"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/{id}/content/html")
async def get_html_file_content_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    file_user = Users.get_user_by_id(file.user_id, db=db)
    if not file_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        try:
            file_path = Storage.get_file(file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                log.info(f"file_path: {file_path}")
                return FileResponse(file_path)
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except HTTPException as e:
            raise e
        except Exception as e:
            log.exception(e)
            log.error("Error getting file content")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error getting file content"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/{id}/content/{file_name}")
async def get_file_content_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user, db=db)
    ):
        file_path = file.path

        # Handle Unicode filenames
        filename = file.meta.get("name", file.filename)
        encoded_filename = quote(filename)  # RFC5987 encoding
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }

        if file_path:
            file_path = Storage.get_file(file_path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                return FileResponse(file_path, headers=headers)
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        else:
            # File path doesn’t exist, return the content as .txt if possible
            file_content = file.content.get("content", "")
            file_name = file.filename

            # Create a generator that encodes the file content
            def generator():
                yield file_content.encode("utf-8")

            return StreamingResponse(
                generator(),
                media_type="text/plain",
                headers=headers,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Delete File By Id
############################


@router.delete("/{id}")
async def delete_file_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    file = Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "write", user, db=db)
    ):

        # Clean up KB associations and embeddings before deleting
        knowledges = Knowledges.get_knowledges_by_file_id(id, db=db)
        for knowledge in knowledges:
            # Remove KB-file relationship
            Knowledges.remove_file_from_knowledge_by_id(knowledge.id, id, db=db)
            # Clean KB embeddings (same logic as /knowledge/{id}/file/remove)
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=knowledge.id, filter={"file_id": id}
                )
                if file.hash:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=knowledge.id, filter={"hash": file.hash}
                    )
            except Exception as e:
                log.debug(f"KB embedding cleanup for {knowledge.id}: {e}")

        result = Files.delete_file_by_id(id, db=db)
        if result:
            try:
                Storage.delete_file(file.path)
                VECTOR_DB_CLIENT.delete(collection_name=f"file-{id}")
            except Exception as e:
                log.exception(e)
                log.error("Error deleting files")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
                )
            return {"message": "File deleted successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting file"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
