"""Utility for generating short-lived Azure Blob user delegation SAS tokens.

Uses DefaultAzureCredential (managed identity) to request a user delegation
key and then scopes a SAS token to a single blob with write-only permissions.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    generate_blob_sas,
)

log = logging.getLogger(__name__)

# Module-level cache so we reuse the same credential across calls.
_credential: Optional[DefaultAzureCredential] = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def generate_upload_sas(
    account_url: str,
    container_name: str,
    blob_name: str,
    expiry_minutes: int = 120,
) -> str:
    """Generate a user-delegation SAS scoped to a single blob (write/create only).

    Parameters
    ----------
    account_url:
        The storage account URL, e.g. ``https://acct.blob.core.windows.net``.
    container_name:
        Target container.
    blob_name:
        Target blob path inside the container.
    expiry_minutes:
        How long the SAS stays valid (default 120 min).

    Returns
    -------
    str
        The SAS query string (without leading ``?``).

    Raises
    ------
    Exception
        If the credential or delegation key request fails.
    """
    credential = _get_credential()
    blob_service_client = BlobServiceClient(
        account_url=account_url,
        credential=credential,
    )

    now = datetime.now(timezone.utc)
    # Delegation key validity window covers the full SAS lifetime plus a
    # small buffer so the key does not expire before the SAS does.
    key_start = now - timedelta(minutes=5)
    key_expiry = now + timedelta(minutes=expiry_minutes + 5)

    user_delegation_key = blob_service_client.get_user_delegation_key(
        key_start_time=key_start,
        key_expiry_time=key_expiry,
    )

    # Extract account name from the URL (https://acct.blob.core.windows.net)
    account_name = account_url.rstrip("/").split("//")[1].split(".")[0]

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        user_delegation_key=user_delegation_key,
        permission=BlobSasPermissions(write=True, create=True),
        expiry=now + timedelta(minutes=expiry_minutes),
        start=key_start,
        protocol="https",
    )

    log.info(
        "Generated user-delegation SAS: blob=%s/%s, expiry_minutes=%d",
        container_name,
        blob_name,
        expiry_minutes,
    )
    return sas_token
