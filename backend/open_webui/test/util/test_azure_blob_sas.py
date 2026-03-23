"""Tests for azure_blob_sas.generate_upload_sas utility."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from open_webui.utils.azure_blob_sas import generate_upload_sas


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_mock_delegation_key():
    key = MagicMock()
    key.signed_oid = "oid-123"
    key.signed_tid = "tid-456"
    key.signed_start = "2026-03-23T00:00:00Z"
    key.signed_expiry = "2026-03-23T03:00:00Z"
    key.signed_service = "b"
    key.signed_version = "2023-11-03"
    key.value = "mock-key-value"
    return key


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestGenerateUploadSas:
    @patch("open_webui.utils.azure_blob_sas.BlobServiceClient")
    @patch("open_webui.utils.azure_blob_sas.generate_blob_sas")
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_returns_sas_token(
        self, mock_cred_cls, mock_gen_sas, mock_bsc_cls
    ):
        # Reset module-level credential cache
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_cred = MagicMock()
        mock_cred_cls.return_value = mock_cred

        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc

        delegation_key = _make_mock_delegation_key()
        mock_bsc.get_user_delegation_key.return_value = delegation_key

        mock_gen_sas.return_value = "sv=2023-11-03&sr=b&sig=abc123"

        result = generate_upload_sas(
            account_url="https://myaccount.blob.core.windows.net",
            container_name="uploads",
            blob_name="session1_video.mov",
            expiry_minutes=60,
        )

        assert result == "sv=2023-11-03&sr=b&sig=abc123"

        # Verify BlobServiceClient was created with the credential
        mock_bsc_cls.assert_called_once_with(
            account_url="https://myaccount.blob.core.windows.net",
            credential=mock_cred,
        )

        # Verify delegation key was requested
        mock_bsc.get_user_delegation_key.assert_called_once()
        call_args = mock_bsc.get_user_delegation_key.call_args
        assert call_args.kwargs["key_start_time"] is not None
        assert call_args.kwargs["key_expiry_time"] is not None

        # Verify generate_blob_sas was called with correct params
        mock_gen_sas.assert_called_once()
        sas_kwargs = mock_gen_sas.call_args.kwargs
        assert sas_kwargs["account_name"] == "myaccount"
        assert sas_kwargs["container_name"] == "uploads"
        assert sas_kwargs["blob_name"] == "session1_video.mov"
        assert sas_kwargs["user_delegation_key"] == delegation_key
        assert sas_kwargs["protocol"] == "https"

    @patch("open_webui.utils.azure_blob_sas.BlobServiceClient")
    @patch("open_webui.utils.azure_blob_sas.generate_blob_sas")
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_sas_permissions_are_write_create_only(
        self, mock_cred_cls, mock_gen_sas, mock_bsc_cls
    ):
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc
        mock_bsc.get_user_delegation_key.return_value = _make_mock_delegation_key()
        mock_gen_sas.return_value = "sv=test"

        generate_upload_sas(
            account_url="https://acct.blob.core.windows.net",
            container_name="c",
            blob_name="b",
        )

        sas_kwargs = mock_gen_sas.call_args.kwargs
        perm = sas_kwargs["permission"]
        # BlobSasPermissions has attributes for each permission type
        assert perm.write is True
        assert perm.create is True
        assert perm.read is False
        assert perm.delete is False

    @patch("open_webui.utils.azure_blob_sas.BlobServiceClient")
    @patch("open_webui.utils.azure_blob_sas.generate_blob_sas")
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_sas_expiry_matches_requested(
        self, mock_cred_cls, mock_gen_sas, mock_bsc_cls
    ):
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc
        mock_bsc.get_user_delegation_key.return_value = _make_mock_delegation_key()
        mock_gen_sas.return_value = "sv=test"

        before = datetime.now(timezone.utc)
        generate_upload_sas(
            account_url="https://acct.blob.core.windows.net",
            container_name="c",
            blob_name="b",
            expiry_minutes=90,
        )
        after = datetime.now(timezone.utc)

        sas_kwargs = mock_gen_sas.call_args.kwargs
        expiry = sas_kwargs["expiry"]
        # Expiry should be close to now + 90 min
        expected_low = before + timedelta(minutes=90)
        expected_high = after + timedelta(minutes=90)
        assert expected_low <= expiry <= expected_high

    @patch("open_webui.utils.azure_blob_sas.BlobServiceClient")
    @patch("open_webui.utils.azure_blob_sas.generate_blob_sas")
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_account_name_extracted_from_url(
        self, mock_cred_cls, mock_gen_sas, mock_bsc_cls
    ):
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc
        mock_bsc.get_user_delegation_key.return_value = _make_mock_delegation_key()
        mock_gen_sas.return_value = "sv=test"

        generate_upload_sas(
            account_url="https://mystorageacct.blob.core.windows.net/",
            container_name="c",
            blob_name="b",
        )

        sas_kwargs = mock_gen_sas.call_args.kwargs
        assert sas_kwargs["account_name"] == "mystorageacct"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestGenerateUploadSasFailures:
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_raises_when_credential_fails(self, mock_cred_cls):
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_cred_cls.side_effect = Exception("No managed identity")

        with pytest.raises(Exception, match="No managed identity"):
            generate_upload_sas(
                account_url="https://acct.blob.core.windows.net",
                container_name="c",
                blob_name="b",
            )

    @patch("open_webui.utils.azure_blob_sas.BlobServiceClient")
    @patch("open_webui.utils.azure_blob_sas.DefaultAzureCredential")
    def test_raises_when_delegation_key_fails(self, mock_cred_cls, mock_bsc_cls):
        import open_webui.utils.azure_blob_sas as mod
        mod._credential = None

        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc
        mock_bsc.get_user_delegation_key.side_effect = Exception(
            "Storage Blob Delegator role required"
        )

        with pytest.raises(Exception, match="Delegator"):
            generate_upload_sas(
                account_url="https://acct.blob.core.windows.net",
                container_name="c",
                blob_name="b",
            )
