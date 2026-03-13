"""
Azure AI Video Indexer client utility.

Handles authentication via MSAL (service principal) and wraps the
Video Indexer REST API for uploading, polling, and retrieving insights.
"""

import logging
import re
import time
import json
from typing import Optional

import requests
import msal

log = logging.getLogger(__name__)

API_ENDPOINT = "https://api.videoindexer.ai"
ARM_ENDPOINT = "https://management.azure.com"
API_VERSION = "2024-01-01"


class VideoIndexerError(Exception):
    """Raised when a Video Indexer API call fails."""

    pass


class VideoIndexerClient:
    """
    Thin wrapper around the Azure AI Video Indexer REST API.

    Authentication flow (ARM-based paid accounts):
      1. Acquire an ARM access token via MSAL ``ConfidentialClientApplication``
         using the service principal's client_id / client_secret / tenant_id.
      2. Exchange the ARM token for a Video Indexer scoped access token
         via the ARM ``generateAccessToken`` endpoint.
      3. Use the VI access token (valid 30 min) for all subsequent API calls.
    """

    def __init__(
        self,
        *,
        account_name: str,
        account_id: str,
        resource_group: str,
        subscription_id: str,
        location: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ):
        self.account_name = account_name
        self.account_id = account_id
        self.resource_group = resource_group
        self.subscription_id = subscription_id
        self.location = location
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

        # Cached tokens
        self._arm_token: Optional[str] = None
        self._arm_token_expiry: float = 0
        self._vi_token: Optional[str] = None
        self._vi_token_expiry: float = 0

        # MSAL confidential client (lazy-init)
        self._msal_app: Optional[msal.ConfidentialClientApplication] = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _get_msal_app(self) -> msal.ConfidentialClientApplication:
        if self._msal_app is None:
            authority = f"https://login.microsoftonline.com/{self.tenant_id}"
            self._msal_app = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=authority,
            )
        return self._msal_app

    def _get_arm_token(self) -> str:
        """Acquire or return a cached ARM bearer token."""
        now = time.time()
        if self._arm_token and now < self._arm_token_expiry - 60:
            return self._arm_token

        app = self._get_msal_app()
        scopes = [f"{ARM_ENDPOINT}/.default"]

        result = app.acquire_token_silent(scopes, account=None)
        if not result:
            result = app.acquire_token_for_client(scopes=scopes)

        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "unknown"))
            raise VideoIndexerError(
                f"Failed to acquire ARM token: {error_desc}"
            )

        self._arm_token = result["access_token"]
        # MSAL tokens typically live 60-90 min; be conservative
        self._arm_token_expiry = now + result.get("expires_in", 3599)
        return self._arm_token

    def get_access_token(self) -> str:
        """
        Get a Video Indexer scoped access token.

        Exchanges the ARM bearer token for a VI ``Contributor`` token
        with ``Account`` scope.
        """
        now = time.time()
        if self._vi_token and now < self._vi_token_expiry - 60:
            return self._vi_token

        arm_token = self._get_arm_token()

        url = (
            f"{ARM_ENDPOINT}/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.VideoIndexer/accounts/{self.account_name}"
            f"/generateAccessToken?api-version={API_VERSION}"
        )

        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {arm_token}",
                "Content-Type": "application/json",
            },
            json={
                "permissionType": "Contributor",
                "scope": "Account",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to generate VI access token: {resp.status_code} – {resp.text}"
            )

        self._vi_token = resp.json().get("accessToken")
        if not self._vi_token:
            raise VideoIndexerError("Empty access token in response")

        self._vi_token_expiry = now + 1800  # 30 min
        return self._vi_token

    def get_account_info(self) -> dict:
        """
        Get account details via the ARM endpoint (works for paid accounts).

        This follows the official Azure sample pattern: use the ARM bearer
        token to query the resource, which returns ``accountId``, ``location``
        and other metadata.
        """
        arm_token = self._get_arm_token()
        url = (
            f"{ARM_ENDPOINT}/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.VideoIndexer/accounts/{self.account_name}"
            f"?api-version={API_VERSION}"
        )
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {arm_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to get account info via ARM: {resp.status_code} – {resp.text}"
            )
        return resp.json()

    def verify_connection(self) -> dict:
        """
        Verify that the credentials are valid by:
          1. Getting account info via ARM (validates service principal + resource path)
          2. Cross-checking the configured account_id against the ARM-discovered one
          3. Generating a VI access token (validates the full auth chain)

        Works for both trial and paid (ARM-based) accounts.
        """
        # Step 1: Get account info via ARM (like official Azure sample)
        account_info = self.get_account_info()
        arm_account_id = account_info.get("properties", {}).get("accountId", "")
        arm_location = account_info.get("location", "")

        # Step 2: Cross-check configured account_id
        if arm_account_id and self.account_id and arm_account_id != self.account_id:
            raise VideoIndexerError(
                f"Account ID mismatch: configured '{self.account_id}' "
                f"but ARM returned '{arm_account_id}'. "
                f"Please update your Account ID in settings."
            )

        # Step 3: Get a VI access token to validate the full auth chain
        self.get_access_token()

        return {
            "status": "ok",
            "account_id": arm_account_id or self.account_id,
            "location": arm_location or self.location,
            "account_name": account_info.get("name", self.account_name),
        }

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_video(
        self,
        file_path: str,
        video_name: str,
        language: str = "en-US",
        indexing_preset: str = "Default",
        description: str = "",
        callback_url: Optional[str] = None,
    ) -> str:
        """
        Upload a local video file to Video Indexer for indexing.

        Returns the ``video_id`` assigned by VI.
        """
        token = self.get_access_token()

        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos"
        )

        params: dict = {
            "accessToken": token,
            "name": video_name[:80],
            "privacy": "Private",
            "language": language,
            "indexingPreset": indexing_preset,
            "streamingPreset": "Default",
            "sendSuccessEmail": "false",
        }
        if description:
            params["description"] = description[:2000]
        if callback_url:
            params["callbackUrl"] = callback_url

        with open(file_path, "rb") as video_file:
            resp = requests.post(
                url,
                params=params,
                files={"file": (video_name, video_file)},
                timeout=600,  # large files may take a while
            )

        if resp.status_code == 409:
            # Video already exists – extract video_id from error message
            # Typical message: "... video id: '<uuid>' ..."
            error_text = resp.text
            match = re.search(r"video id:\s*'([^']+)'", error_text, re.IGNORECASE)
            if not match:
                match = re.search(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    error_text,
                    re.IGNORECASE,
                )
            if match:
                existing_id = match.group(1) if match.lastindex else match.group(0)
                log.info(
                    f"Video already exists in VI (409): reusing video_id={existing_id}"
                )
                return existing_id
            raise VideoIndexerError(
                f"Video already exists (409) but could not extract video_id: {error_text}"
            )

        if resp.status_code not in (200, 201):
            raise VideoIndexerError(
                f"Video upload failed: {resp.status_code} – {resp.text}"
            )

        data = resp.json()
        video_id = data.get("id")
        if not video_id:
            raise VideoIndexerError(f"No video id in upload response: {data}")
        log.info(f"Video uploaded to VI: video_id={video_id}")
        return video_id

    # ------------------------------------------------------------------
    # Status / Polling
    # ------------------------------------------------------------------

    def get_video_status(self, video_id: str) -> dict:
        """Return lightweight state info for a video."""
        token = self.get_access_token()
        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos/{video_id}/Index"
            f"?accessToken={token}&includeSummarizedInsights=false"
        )
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to get video status: {resp.status_code} – {resp.text}"
            )
        data = resp.json()
        return {
            "state": data.get("state", "Unknown"),
            "processingProgress": data.get("processingProgress", ""),
        }

    def wait_for_indexing(
        self,
        video_id: str,
        poll_interval: int = 15,
        timeout: int = 3600,
    ) -> str:
        """
        Block until video indexing completes or fails.

        Returns the final state string (``"Processed"`` on success).
        Raises ``VideoIndexerError`` on timeout or failure.
        """
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise VideoIndexerError(
                    f"Indexing timed out after {timeout}s for video {video_id}"
                )

            status = self.get_video_status(video_id)
            state = status["state"]
            progress = status.get("processingProgress", "")
            log.info(
                f"VI video {video_id}: state={state}, progress={progress}, "
                f"elapsed={int(elapsed)}s"
            )

            if state == "Processed":
                return state
            if state == "Failed":
                raise VideoIndexerError(
                    f"Video indexing failed for {video_id}"
                )

            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Insights / Transcript
    # ------------------------------------------------------------------

    def get_video_index(self, video_id: str) -> dict:
        """Get the full index (insights) JSON for a processed video."""
        token = self.get_access_token()
        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos/{video_id}/Index"
            f"?accessToken={token}"
            f"&includeSummarizedInsights=true"
        )
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to get video index: {resp.status_code} – {resp.text}"
            )
        return resp.json()

    def get_transcript(self, video_id: str, fmt: str = "txt") -> str:
        """
        Get the transcript / captions for a processed video.

        ``fmt`` can be ``vtt``, ``srt``, ``txt``, or ``csv``.
        """
        token = self.get_access_token()
        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos/{video_id}/Captions"
            f"?accessToken={token}&format={fmt}&includeSpeakers=true"
        )
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to get transcript: {resp.status_code} – {resp.text}"
            )
        return resp.text

    def get_thumbnail(self, video_id: str, thumbnail_id: str) -> bytes:
        """Return thumbnail JPEG bytes."""
        token = self.get_access_token()
        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos/{video_id}"
            f"/Thumbnails/{thumbnail_id}"
            f"?accessToken={token}&format=Jpeg"
        )
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Failed to get thumbnail: {resp.status_code} – {resp.text}"
            )
        return resp.content

    def search_videos(self, query: str, page_size: int = 25, skip: int = 0) -> dict:
        """Search across all indexed videos in the account."""
        token = self.get_access_token()
        url = (
            f"{API_ENDPOINT}/{self.location}"
            f"/Accounts/{self.account_id}/Videos/Search"
            f"?accessToken={token}&query={query}"
            f"&pageSize={page_size}&skip={skip}"
        )
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            raise VideoIndexerError(
                f"Search failed: {resp.status_code} – {resp.text}"
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Insight extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_structured_content(index_data: dict) -> str:
        """
        Transform the raw Video Indexer index JSON into a structured text
        document suitable for embedding in a RAG pipeline.

        Sections: Transcript, Keywords, Topics, Labels, OCR, Named Entities,
        Faces, Scenes, Audio Effects, Sentiment.

        Returns an empty string if no insights could be extracted.
        """
        sections = []
        videos = index_data.get("videos", [])
        summarized = index_data.get("summarizedInsights", {})

        # -- Transcript --
        transcript_lines = []
        for video in videos:
            insights = video.get("insights", {})
            for block in insights.get("transcript", []):
                text = block.get("text", "").strip()
                speaker = block.get("speakerId", "")
                start = block.get("instances", [{}])[0].get("start", "")
                if text:
                    prefix = f"[{start}]" if start else ""
                    spk = f" Speaker {speaker}:" if speaker else ""
                    transcript_lines.append(f"{prefix}{spk} {text}")
        if transcript_lines:
            sections.append("## Video Transcript\n\n" + "\n".join(transcript_lines))

        # -- Keywords --
        keywords = [k.get("name", "") for k in summarized.get("keywords", [])]
        if keywords:
            sections.append("## Keywords\n\n" + ", ".join(keywords))

        # -- Topics --
        topics = [t.get("name", "") for t in summarized.get("topics", [])]
        if topics:
            sections.append("## Topics\n\n" + ", ".join(topics))

        # -- Labels (visual labels detected in source) --
        labels = [lb.get("name", "") for lb in summarized.get("labels", [])]
        if labels:
            sections.append("## Visual Labels\n\n" + ", ".join(labels))

        # -- OCR (on-screen text) --
        ocr_lines = []
        for video in videos:
            insights = video.get("insights", {})
            for block in insights.get("ocr", []):
                text = block.get("text", "").strip()
                if text:
                    ocr_lines.append(text)
        if ocr_lines:
            # Deduplicate while preserving order
            seen = set()
            unique_ocr = []
            for line in ocr_lines:
                if line not in seen:
                    seen.add(line)
                    unique_ocr.append(line)
            sections.append("## On-Screen Text (OCR)\n\n" + "\n".join(unique_ocr))

        # -- Named Entities --
        entities = []
        for ne in summarized.get("namedLocations", []):
            entities.append(f"{ne.get('name', '')} (Location)")
        for ne in summarized.get("namedPeople", []):
            entities.append(f"{ne.get('name', '')} (Person)")
        for ne in summarized.get("brands", []):
            entities.append(f"{ne.get('name', '')} (Brand)")
        if entities:
            sections.append("## Named Entities\n\n" + ", ".join(entities))

        # -- Faces / Speakers --
        faces = [f.get("name", "") for f in summarized.get("faces", []) if f.get("name")]
        if faces:
            sections.append("## Identified Faces\n\n" + ", ".join(faces))

        # -- Scenes --
        scene_lines = []
        for video in videos:
            insights = video.get("insights", {})
            for scene in insights.get("scenes", []):
                scene_id = scene.get("id", "")
                instances = scene.get("instances", [])
                if instances:
                    start = instances[0].get("start", "")
                    end = instances[0].get("end", "")
                    scene_lines.append(f"Scene {scene_id}: {start} - {end}")
        if scene_lines:
            sections.append("## Scenes\n\n" + "\n".join(scene_lines))

        # -- Audio Effects --
        audio_effects = []
        for video in videos:
            insights = video.get("insights", {})
            for ae in insights.get("audioEffects", []):
                name = ae.get("audioEffectKey", "") or ae.get("type", "")
                if name:
                    audio_effects.append(name)
        if audio_effects:
            unique_ae = list(dict.fromkeys(audio_effects))
            sections.append("## Audio Effects\n\n" + ", ".join(unique_ae))

        # -- Sentiment --
        sentiments = summarized.get("sentiments", [])
        if sentiments:
            sent_parts = []
            for s in sentiments:
                kind = s.get("sentimentKey", "")
                pct = s.get("seenDurationRatio", 0)
                sent_parts.append(f"{kind}: {pct:.0%}")
            sections.append("## Sentiment\n\n" + " | ".join(sent_parts))

        return "\n\n".join(sections)


def build_client_from_config(config) -> VideoIndexerClient:
    """
    Construct a ``VideoIndexerClient`` from the ``request.app.state.config``
    namespace.  Raises ``VideoIndexerError`` if any required field is missing.
    """
    required_fields = {
        "VIDEO_INDEXER_ACCOUNT_NAME": getattr(config, "VIDEO_INDEXER_ACCOUNT_NAME", ""),
        "VIDEO_INDEXER_ACCOUNT_ID": getattr(config, "VIDEO_INDEXER_ACCOUNT_ID", ""),
        "VIDEO_INDEXER_RESOURCE_GROUP": getattr(config, "VIDEO_INDEXER_RESOURCE_GROUP", ""),
        "VIDEO_INDEXER_SUBSCRIPTION_ID": getattr(config, "VIDEO_INDEXER_SUBSCRIPTION_ID", ""),
        "VIDEO_INDEXER_LOCATION": getattr(config, "VIDEO_INDEXER_LOCATION", ""),
        "VIDEO_INDEXER_TENANT_ID": getattr(config, "VIDEO_INDEXER_TENANT_ID", ""),
        "VIDEO_INDEXER_CLIENT_ID": getattr(config, "VIDEO_INDEXER_CLIENT_ID", ""),
        "VIDEO_INDEXER_CLIENT_SECRET": getattr(config, "VIDEO_INDEXER_CLIENT_SECRET", ""),
    }

    missing = [k for k, v in required_fields.items() if not v]
    if missing:
        raise VideoIndexerError(
            f"Missing Video Indexer configuration: {', '.join(missing)}"
        )

    return VideoIndexerClient(
        account_name=required_fields["VIDEO_INDEXER_ACCOUNT_NAME"],
        account_id=required_fields["VIDEO_INDEXER_ACCOUNT_ID"],
        resource_group=required_fields["VIDEO_INDEXER_RESOURCE_GROUP"],
        subscription_id=required_fields["VIDEO_INDEXER_SUBSCRIPTION_ID"],
        location=required_fields["VIDEO_INDEXER_LOCATION"],
        tenant_id=required_fields["VIDEO_INDEXER_TENANT_ID"],
        client_id=required_fields["VIDEO_INDEXER_CLIENT_ID"],
        client_secret=required_fields["VIDEO_INDEXER_CLIENT_SECRET"],
    )
