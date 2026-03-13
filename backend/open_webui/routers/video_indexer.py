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
    VideoIndexerClient,
    VideoIndexerError,
    build_client_from_config,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────


class VideoIndexerConfigForm(BaseModel):
    ENABLED: bool = False
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


# ──────────────────────────────────────────────
# Admin config endpoints
# ──────────────────────────────────────────────


@router.get("/config")
async def get_video_indexer_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLED": request.app.state.config.VIDEO_INDEXER_ENABLED,
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
    }


@router.post("/config/update")
async def update_video_indexer_config(
    request: Request,
    form_data: VideoIndexerConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.VIDEO_INDEXER_ENABLED = form_data.ENABLED
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

    return {
        "ENABLED": request.app.state.config.VIDEO_INDEXER_ENABLED,
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
        client = build_client_from_config(request.app.state.config)
        info = client.verify_connection()
        return {"status": "ok", "account": info}
    except VideoIndexerError as exc:
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
    vi_meta = meta.get("video_indexer", {})
    return {
        "file_id": file_id,
        "video_id": vi_meta.get("video_id"),
        "state": vi_meta.get("state", "unknown"),
        "progress": vi_meta.get("progress", ""),
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
    vi_meta = meta.get("video_indexer", {})
    insights = vi_meta.get("insights")
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
    try:
        client = build_client_from_config(request.app.state.config)
        results = client.search_videos(query, page_size=page_size, skip=skip)
        return results
    except VideoIndexerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
