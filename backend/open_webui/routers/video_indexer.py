"""
Video Indexer router – admin configuration and processing endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.models.files import Files
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.video_indexer import (
    VideoIndexerError,
    build_client_from_config,
)
from open_webui.utils.soniox import SonioxError, build_soniox_client_from_config

log = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────


class VideoIndexerConfigForm(BaseModel):
    ENABLED: bool = False
    PROVIDER: str = "azure_video_indexer"
    ACCOUNT_NAME: str = ""
    ACCOUNT_ID: str = ""
    RESOURCE_GROUP: str = ""
    SUBSCRIPTION_ID: str = ""
    LOCATION: str = ""
    TENANT_ID: str = ""
    CLIENT_ID: str = ""
    CLIENT_SECRET: str = ""
    INDEXING_PRESET: str = "Default"
    LANGUAGE: str = "en-US"
    VIDEO_INDEXER_AZURE_BLOB_SAS: str = ""
    VIDEO_INDEXER_AZURE_BLOB_ENDPOINT: str = ""
    VIDEO_INDEXER_AZURE_BLOB_CONTAINER: str = ""
    SONIOX_API_KEY: str = ""
    SONIOX_BASE_URL: str = "https://api.soniox.com/v1"
    SONIOX_MODEL: str = "stt-async-v4"
    SONIOX_ENABLE_LANGUAGE_IDENTIFICATION: bool = True
    SONIOX_LANGUAGE_HINTS: list[str] = []
    SONIOX_ENABLE_SPEAKER_DIARIZATION: bool = True
    SONIOX_ENABLE_TRANSLATION: bool = False
    SONIOX_TRANSLATION_MODE: str = "two_way"
    SONIOX_TRANSLATION_TARGET_LANGUAGE: str = "en"
    SONIOX_TRANSLATION_SECOND_LANGUAGE: str = "en"
    SONIOX_CONTEXT_TERMS: list[str] = []
    SONIOX_CONTEXT_TEXT: str = ""


# ──────────────────────────────────────────────
# Admin config endpoints
# ──────────────────────────────────────────────


@router.get("/config")
async def get_video_indexer_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLED": request.app.state.config.VIDEO_INDEXER_ENABLED,
        "PROVIDER": request.app.state.config.VIDEO_INDEXER_PROVIDER,
        "ACCOUNT_NAME": request.app.state.config.VIDEO_INDEXER_ACCOUNT_NAME,
        "ACCOUNT_ID": request.app.state.config.VIDEO_INDEXER_ACCOUNT_ID,
        "RESOURCE_GROUP": request.app.state.config.VIDEO_INDEXER_RESOURCE_GROUP,
        "SUBSCRIPTION_ID": request.app.state.config.VIDEO_INDEXER_SUBSCRIPTION_ID,
        "LOCATION": request.app.state.config.VIDEO_INDEXER_LOCATION,
        "TENANT_ID": request.app.state.config.VIDEO_INDEXER_TENANT_ID,
        "CLIENT_ID": request.app.state.config.VIDEO_INDEXER_CLIENT_ID,
        "CLIENT_SECRET": request.app.state.config.VIDEO_INDEXER_CLIENT_SECRET,
        "INDEXING_PRESET": request.app.state.config.VIDEO_INDEXER_INDEXING_PRESET,
        "LANGUAGE": request.app.state.config.VIDEO_INDEXER_LANGUAGE,
        "VIDEO_INDEXER_AZURE_BLOB_SAS": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_SAS,
        "VIDEO_INDEXER_AZURE_BLOB_ENDPOINT": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_ENDPOINT,
        "VIDEO_INDEXER_AZURE_BLOB_CONTAINER": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_CONTAINER,
        "SONIOX_API_KEY": request.app.state.config.SONIOX_API_KEY,
        "SONIOX_BASE_URL": request.app.state.config.SONIOX_BASE_URL,
        "SONIOX_MODEL": request.app.state.config.SONIOX_MODEL,
        "SONIOX_ENABLE_LANGUAGE_IDENTIFICATION": request.app.state.config.SONIOX_ENABLE_LANGUAGE_IDENTIFICATION,
        "SONIOX_LANGUAGE_HINTS": request.app.state.config.SONIOX_LANGUAGE_HINTS,
        "SONIOX_ENABLE_SPEAKER_DIARIZATION": request.app.state.config.SONIOX_ENABLE_SPEAKER_DIARIZATION,
        "SONIOX_ENABLE_TRANSLATION": request.app.state.config.SONIOX_ENABLE_TRANSLATION,
        "SONIOX_TRANSLATION_MODE": request.app.state.config.SONIOX_TRANSLATION_MODE,
        "SONIOX_TRANSLATION_TARGET_LANGUAGE": request.app.state.config.SONIOX_TRANSLATION_TARGET_LANGUAGE,
        "SONIOX_TRANSLATION_SECOND_LANGUAGE": request.app.state.config.SONIOX_TRANSLATION_SECOND_LANGUAGE,
        "SONIOX_CONTEXT_TERMS": request.app.state.config.SONIOX_CONTEXT_TERMS,
        "SONIOX_CONTEXT_TEXT": request.app.state.config.SONIOX_CONTEXT_TEXT,
    }


@router.post("/config/update")
async def update_video_indexer_config(
    request: Request,
    form_data: VideoIndexerConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.VIDEO_INDEXER_ENABLED = form_data.ENABLED
    request.app.state.config.VIDEO_INDEXER_PROVIDER = form_data.PROVIDER
    request.app.state.config.VIDEO_INDEXER_ACCOUNT_NAME = form_data.ACCOUNT_NAME
    request.app.state.config.VIDEO_INDEXER_ACCOUNT_ID = form_data.ACCOUNT_ID
    request.app.state.config.VIDEO_INDEXER_RESOURCE_GROUP = form_data.RESOURCE_GROUP
    request.app.state.config.VIDEO_INDEXER_SUBSCRIPTION_ID = form_data.SUBSCRIPTION_ID
    request.app.state.config.VIDEO_INDEXER_LOCATION = form_data.LOCATION
    request.app.state.config.VIDEO_INDEXER_TENANT_ID = form_data.TENANT_ID
    request.app.state.config.VIDEO_INDEXER_CLIENT_ID = form_data.CLIENT_ID
    request.app.state.config.VIDEO_INDEXER_CLIENT_SECRET = form_data.CLIENT_SECRET
    request.app.state.config.VIDEO_INDEXER_INDEXING_PRESET = form_data.INDEXING_PRESET
    request.app.state.config.VIDEO_INDEXER_LANGUAGE = form_data.LANGUAGE
    request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_SAS = (
        form_data.VIDEO_INDEXER_AZURE_BLOB_SAS
    )
    request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_ENDPOINT = (
        form_data.VIDEO_INDEXER_AZURE_BLOB_ENDPOINT
    )
    request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_CONTAINER = (
        form_data.VIDEO_INDEXER_AZURE_BLOB_CONTAINER
    )
    request.app.state.config.SONIOX_API_KEY = form_data.SONIOX_API_KEY
    request.app.state.config.SONIOX_BASE_URL = form_data.SONIOX_BASE_URL
    request.app.state.config.SONIOX_MODEL = form_data.SONIOX_MODEL
    request.app.state.config.SONIOX_ENABLE_LANGUAGE_IDENTIFICATION = (
        form_data.SONIOX_ENABLE_LANGUAGE_IDENTIFICATION
    )
    request.app.state.config.SONIOX_LANGUAGE_HINTS = form_data.SONIOX_LANGUAGE_HINTS
    request.app.state.config.SONIOX_ENABLE_SPEAKER_DIARIZATION = (
        form_data.SONIOX_ENABLE_SPEAKER_DIARIZATION
    )
    request.app.state.config.SONIOX_ENABLE_TRANSLATION = (
        form_data.SONIOX_ENABLE_TRANSLATION
    )
    request.app.state.config.SONIOX_TRANSLATION_MODE = form_data.SONIOX_TRANSLATION_MODE
    request.app.state.config.SONIOX_TRANSLATION_TARGET_LANGUAGE = (
        form_data.SONIOX_TRANSLATION_TARGET_LANGUAGE
    )
    request.app.state.config.SONIOX_TRANSLATION_SECOND_LANGUAGE = (
        form_data.SONIOX_TRANSLATION_SECOND_LANGUAGE
    )
    request.app.state.config.SONIOX_CONTEXT_TERMS = form_data.SONIOX_CONTEXT_TERMS
    request.app.state.config.SONIOX_CONTEXT_TEXT = form_data.SONIOX_CONTEXT_TEXT

    return {
        "ENABLED": request.app.state.config.VIDEO_INDEXER_ENABLED,
        "PROVIDER": request.app.state.config.VIDEO_INDEXER_PROVIDER,
        "ACCOUNT_NAME": request.app.state.config.VIDEO_INDEXER_ACCOUNT_NAME,
        "ACCOUNT_ID": request.app.state.config.VIDEO_INDEXER_ACCOUNT_ID,
        "RESOURCE_GROUP": request.app.state.config.VIDEO_INDEXER_RESOURCE_GROUP,
        "SUBSCRIPTION_ID": request.app.state.config.VIDEO_INDEXER_SUBSCRIPTION_ID,
        "LOCATION": request.app.state.config.VIDEO_INDEXER_LOCATION,
        "TENANT_ID": request.app.state.config.VIDEO_INDEXER_TENANT_ID,
        "CLIENT_ID": request.app.state.config.VIDEO_INDEXER_CLIENT_ID,
        "CLIENT_SECRET": request.app.state.config.VIDEO_INDEXER_CLIENT_SECRET,
        "INDEXING_PRESET": request.app.state.config.VIDEO_INDEXER_INDEXING_PRESET,
        "LANGUAGE": request.app.state.config.VIDEO_INDEXER_LANGUAGE,
        "VIDEO_INDEXER_AZURE_BLOB_SAS": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_SAS,
        "VIDEO_INDEXER_AZURE_BLOB_ENDPOINT": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_ENDPOINT,
        "VIDEO_INDEXER_AZURE_BLOB_CONTAINER": request.app.state.config.VIDEO_INDEXER_AZURE_BLOB_CONTAINER,
        "SONIOX_API_KEY": request.app.state.config.SONIOX_API_KEY,
        "SONIOX_BASE_URL": request.app.state.config.SONIOX_BASE_URL,
        "SONIOX_MODEL": request.app.state.config.SONIOX_MODEL,
        "SONIOX_ENABLE_LANGUAGE_IDENTIFICATION": request.app.state.config.SONIOX_ENABLE_LANGUAGE_IDENTIFICATION,
        "SONIOX_LANGUAGE_HINTS": request.app.state.config.SONIOX_LANGUAGE_HINTS,
        "SONIOX_ENABLE_SPEAKER_DIARIZATION": request.app.state.config.SONIOX_ENABLE_SPEAKER_DIARIZATION,
        "SONIOX_ENABLE_TRANSLATION": request.app.state.config.SONIOX_ENABLE_TRANSLATION,
        "SONIOX_TRANSLATION_MODE": request.app.state.config.SONIOX_TRANSLATION_MODE,
        "SONIOX_TRANSLATION_TARGET_LANGUAGE": request.app.state.config.SONIOX_TRANSLATION_TARGET_LANGUAGE,
        "SONIOX_TRANSLATION_SECOND_LANGUAGE": request.app.state.config.SONIOX_TRANSLATION_SECOND_LANGUAGE,
        "SONIOX_CONTEXT_TERMS": request.app.state.config.SONIOX_CONTEXT_TERMS,
        "SONIOX_CONTEXT_TEXT": request.app.state.config.SONIOX_CONTEXT_TEXT,
    }


# ──────────────────────────────────────────────
# Connection test
# ──────────────────────────────────────────────


@router.post("/verify")
async def verify_video_indexer_connection(
    request: Request, user=Depends(get_admin_user)
):
    """Test that the current config can authenticate and reach VI."""
    try:
        provider = getattr(
            request.app.state.config,
            "VIDEO_INDEXER_PROVIDER",
            "azure_video_indexer",
        )
        if provider == "soniox":
            client = build_soniox_client_from_config(request.app.state.config)
        else:
            client = build_client_from_config(request.app.state.config)
        info = client.verify_connection()
        return {"status": "ok", "provider": provider, "account": info}
    except (VideoIndexerError, SonioxError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        log.exception("Video Indexer verify failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


# ──────────────────────────────────────────────
# Per-file status & insights
# ──────────────────────────────────────────────


@router.get("/status/{file_id}")
async def get_video_indexer_status(
    file_id: str, request: Request, user=Depends(get_verified_user)
):
    """Return the Video Indexer processing status stored in file metadata."""
    file = Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    meta = file.meta or {}
    provider = meta.get("analyzer_provider") or "azure_video_indexer"
    analyzer_meta = meta.get("analyzer", {})

    if not analyzer_meta:
        analyzer_meta = meta.get("video_indexer", {})
        provider = "azure_video_indexer"

    return {
        "file_id": file_id,
        "provider": provider,
        "video_id": analyzer_meta.get("video_id"),
        "transcription_id": analyzer_meta.get("transcription_id"),
        "state": analyzer_meta.get("state", "unknown"),
        "progress": analyzer_meta.get("progress", ""),
    }


@router.get("/insights/{file_id}")
async def get_video_indexer_insights(
    file_id: str, request: Request, user=Depends(get_verified_user)
):
    """Return the full insights JSON stored in file metadata."""
    file = Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    meta = file.meta or {}
    analyzer_meta = meta.get("analyzer") or meta.get("video_indexer", {})
    insights = analyzer_meta.get("insights")
    if not insights:
        raise HTTPException(status_code=404, detail="No insights available yet")
    return insights


@router.get("/transcript/{file_id}")
async def get_video_indexer_transcript(
    file_id: str, request: Request, user=Depends(get_verified_user)
):
    """Return the transcript text stored in file data."""
    file = Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    data = file.data or {}
    content = data.get("content", "")
    if not content:
        raise HTTPException(status_code=404, detail="No transcript available yet")
    return {"file_id": file_id, "transcript": content}


@router.get("/search")
async def search_video_indexer(
    query: str,
    request: Request,
    user=Depends(get_verified_user),
    page_size: int = 25,
    skip: int = 0,
):
    """Proxy search to the VI account."""
    if not request.app.state.config.VIDEO_INDEXER_ENABLED:
        raise HTTPException(status_code=400, detail="Video Indexer is not enabled")

    if getattr(request.app.state.config, "VIDEO_INDEXER_PROVIDER", "azure_video_indexer") != "azure_video_indexer":
        raise HTTPException(status_code=400, detail="Search is only supported for Azure Video Indexer provider")

    try:
        client = build_client_from_config(request.app.state.config)
        results = client.search_videos(query, page_size=page_size, skip=skip)
        return results
    except VideoIndexerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
