"""
Soniox Speech-to-Text client utility.

Implements async file transcription via Soniox REST API:
  1. Upload file
  2. Create transcription job
  3. Poll status until completion
  4. Fetch transcript text and token metadata
"""

import logging
import time
import os
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.soniox.com/v1"
DEFAULT_MODEL = "stt-async-v4"


class SonioxError(Exception):
    """Raised when a Soniox API call fails."""


def normalize_audio_file_for_soniox(
    file_path: str,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> tuple[str, str]:
    """
    Normalize audio files that Soniox may reject (for example webm/opus)
    by converting them to mp3 prior to upload.

    Returns a tuple of (normalized_file_path, normalized_filename).
    """
    original = Path(file_path)
    if not original.exists():
        raise SonioxError(f"File does not exist: {file_path}")

    upload_name = filename or original.name
    suffix = original.suffix.lower()
    ctype = (content_type or "").lower()

    needs_conversion = suffix in {".webm", ".ogg", ".oga", ".opus"} or any(
        marker in ctype for marker in ["webm", "ogg", "opus"]
    )

    if not needs_conversion:
        return file_path, upload_name

    converted_path = os.path.splitext(file_path)[0] + ".mp3"
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(file_path)
        audio.export(converted_path, format="mp3")
    except Exception as exc:
        raise SonioxError(
            f"Failed to convert audio for Soniox upload: {exc}"
        ) from exc

    converted_name = str(Path(upload_name).with_suffix(".mp3"))
    log.info(
        "Converted Soniox upload from unsupported format: %s -> %s",
        file_path,
        converted_path,
    )
    return converted_path, converted_name


class SonioxClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        enable_language_identification: bool = True,
        language_hints: Optional[list[str]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enable_language_identification = enable_language_identification
        self.language_hints = language_hints or []

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _extract_error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return (
                    payload.get("message")
                    or payload.get("error")
                    or payload.get("detail")
                    or str(payload)
                )
        except Exception:
            pass
        return response.text

    def verify_connection(self) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/models",
            headers=self._headers,
            timeout=30,
        )
        if resp.status_code != 200:
            raise SonioxError(
                f"Failed to verify Soniox connection: {resp.status_code} - {self._extract_error_message(resp)}"
            )

        payload = resp.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return {
            "status": "ok",
            "model": self.model,
            "available_models": [m.get("name") for m in models if isinstance(m, dict)],
        }

    def upload_file(self, file_path: str, filename: Optional[str] = None) -> str:
        resolved = Path(file_path)
        if not resolved.exists():
            raise SonioxError(f"File does not exist: {file_path}")

        upload_name = filename or resolved.name
        with open(resolved, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/files",
                headers=self._headers,
                files={"file": (upload_name, f)},
                timeout=300,
            )

        if resp.status_code not in (200, 201):
            raise SonioxError(
                f"Failed to upload file to Soniox: {resp.status_code} - {self._extract_error_message(resp)}"
            )

        payload = resp.json()
        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not file_id:
            raise SonioxError(f"No file id in Soniox upload response: {payload}")

        return file_id

    def create_transcription(
        self,
        *,
        file_id: str,
        client_reference_id: Optional[str] = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "file_id": file_id,
            "enable_language_identification": self.enable_language_identification,
        }

        if self.language_hints:
            body["language_hints"] = self.language_hints

        if client_reference_id:
            body["client_reference_id"] = client_reference_id

        resp = requests.post(
            f"{self.base_url}/transcriptions",
            headers={**self._headers, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )

        if resp.status_code not in (200, 201):
            raise SonioxError(
                f"Failed to create Soniox transcription: {resp.status_code} - {self._extract_error_message(resp)}"
            )

        payload = resp.json()
        transcription_id = payload.get("id") if isinstance(payload, dict) else None
        if not transcription_id:
            raise SonioxError(f"No transcription id in Soniox response: {payload}")

        return transcription_id

    def get_transcription(self, transcription_id: str) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/transcriptions/{transcription_id}",
            headers=self._headers,
            timeout=30,
        )

        if resp.status_code != 200:
            raise SonioxError(
                f"Failed to get Soniox transcription: {resp.status_code} - {self._extract_error_message(resp)}"
            )

        payload = resp.json()
        if not isinstance(payload, dict):
            raise SonioxError(f"Unexpected Soniox transcription payload: {payload}")

        return payload

    def wait_for_completion(
        self,
        transcription_id: str,
        *,
        poll_interval: int = 10,
        timeout: int = 3600,
        on_progress: Optional["callable"] = None,
    ) -> dict[str, Any]:
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise SonioxError(
                    f"Soniox transcription timed out after {timeout}s for id {transcription_id}"
                )

            info = self.get_transcription(transcription_id)
            status_value = str(info.get("status", "")).lower()

            if on_progress:
                progress = info.get("progress") or info.get("percent_complete")
                on_progress(status_value, progress, elapsed)

            if status_value in {"completed", "done", "succeeded", "success"}:
                return info
            if status_value in {"error", "failed", "failure"}:
                raise SonioxError(
                    f"Soniox transcription failed for {transcription_id}: {info}"
                )

            time.sleep(poll_interval)

    def get_transcript(self, transcription_id: str) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/transcriptions/{transcription_id}/transcript",
            headers=self._headers,
            timeout=60,
        )

        if resp.status_code != 200:
            raise SonioxError(
                f"Failed to get Soniox transcript: {resp.status_code} - {self._extract_error_message(resp)}"
            )

        payload = resp.json()
        if not isinstance(payload, dict):
            raise SonioxError(f"Unexpected Soniox transcript payload: {payload}")

        return payload

    @staticmethod
    def extract_text_and_languages(transcript_payload: dict[str, Any]) -> dict[str, Any]:
        text = ""
        tokens = transcript_payload.get("tokens")

        if isinstance(transcript_payload.get("text"), str):
            text = transcript_payload["text"]
        elif isinstance(transcript_payload.get("transcript"), str):
            text = transcript_payload["transcript"]

        if not text and isinstance(tokens, list):
            text = "".join(
                token.get("text", "")
                for token in tokens
                if isinstance(token, dict)
            )

        languages: list[str] = []
        if isinstance(tokens, list):
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                language = token.get("language")
                if language and language not in languages:
                    languages.append(language)

        return {
            "text": text.strip(),
            "tokens": tokens if isinstance(tokens, list) else [],
            "languages": languages,
        }

    def transcribe_file(
        self,
        file_path: str,
        *,
        filename: Optional[str] = None,
        client_reference_id: Optional[str] = None,
        poll_interval: int = 10,
        timeout: int = 3600,
        on_progress: Optional["callable"] = None,
    ) -> dict[str, Any]:
        file_id = self.upload_file(file_path=file_path, filename=filename)
        transcription_id = self.create_transcription(
            file_id=file_id,
            client_reference_id=client_reference_id,
        )

        transcription_info = self.wait_for_completion(
            transcription_id,
            poll_interval=poll_interval,
            timeout=timeout,
            on_progress=on_progress,
        )
        transcript_payload = self.get_transcript(transcription_id)
        extracted = self.extract_text_and_languages(transcript_payload)

        return {
            "file_id": file_id,
            "transcription_id": transcription_id,
            "status": transcription_info.get("status", "completed"),
            "text": extracted["text"],
            "tokens": extracted["tokens"],
            "languages": extracted["languages"],
            "transcription": transcription_info,
            "transcript": transcript_payload,
        }


def build_soniox_client_from_config(config) -> SonioxClient:
    api_key = getattr(config, "SONIOX_API_KEY", "")
    if not api_key:
        raise SonioxError("Missing Soniox configuration: SONIOX_API_KEY")

    base_url = getattr(config, "SONIOX_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    model = getattr(config, "SONIOX_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    enable_lid = bool(getattr(config, "SONIOX_ENABLE_LANGUAGE_IDENTIFICATION", True))
    language_hints = getattr(config, "SONIOX_LANGUAGE_HINTS", []) or []

    if isinstance(language_hints, str):
        language_hints = [
            item.strip() for item in language_hints.split(",") if item.strip()
        ]

    return SonioxClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        enable_language_identification=enable_lid,
        language_hints=language_hints,
    )
