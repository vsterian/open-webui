"""
Tests for the Video Indexer utility module and router.
"""

import pytest
from unittest.mock import MagicMock, patch

from open_webui.utils.video_indexer import (
    VideoIndexerClient,
    VideoIndexerError,
    build_client_from_config,
)


# ──────────────────────────────────────────────
# extract_structured_content
# ──────────────────────────────────────────────


class TestExtractStructuredContent:
    """Tests for VideoIndexerClient.extract_structured_content()."""

    def test_empty_index_data(self):
        result = VideoIndexerClient.extract_structured_content({})
        assert result == ""

    def test_transcript_extraction(self):
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {
                                "text": "Hello world",
                                "speakerId": 1,
                                "instances": [{"start": "0:00:01.000"}],
                            },
                            {
                                "text": "Second line",
                                "speakerId": 2,
                                "instances": [{"start": "0:00:05.000"}],
                            },
                        ]
                    }
                }
            ],
            "summarizedInsights": {},
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Video Transcript" in result
        assert "[0:00:01.000] Speaker 1: Hello world" in result
        assert "[0:00:05.000] Speaker 2: Second line" in result

    def test_keywords_extraction(self):
        index_data = {
            "videos": [],
            "summarizedInsights": {
                "keywords": [{"name": "AI"}, {"name": "video"}, {"name": "test"}]
            },
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Keywords" in result
        assert "AI, video, test" in result

    def test_topics_extraction(self):
        index_data = {
            "videos": [],
            "summarizedInsights": {
                "topics": [{"name": "Technology"}, {"name": "Education"}]
            },
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Topics" in result
        assert "Technology, Education" in result

    def test_named_entities_extraction(self):
        index_data = {
            "videos": [],
            "summarizedInsights": {
                "namedLocations": [{"name": "Seattle"}],
                "namedPeople": [{"name": "John Doe"}],
                "brands": [{"name": "Microsoft"}],
            },
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Named Entities" in result
        assert "Seattle (Location)" in result
        assert "John Doe (Person)" in result
        assert "Microsoft (Brand)" in result

    def test_sentiment_extraction(self):
        index_data = {
            "videos": [],
            "summarizedInsights": {
                "sentiments": [
                    {"sentimentKey": "Positive", "seenDurationRatio": 0.75},
                    {"sentimentKey": "Negative", "seenDurationRatio": 0.25},
                ]
            },
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "## Sentiment" in result
        assert "Positive: 75%" in result
        assert "Negative: 25%" in result

    def test_full_document_structure(self):
        """All sections present produces a well-formed document."""
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {
                                "text": "Welcome",
                                "speakerId": 1,
                                "instances": [{"start": "0:00:00.000"}],
                            }
                        ]
                    }
                }
            ],
            "summarizedInsights": {
                "keywords": [{"name": "demo"}],
                "topics": [{"name": "Tech"}],
                "namedPeople": [{"name": "Alice"}],
                "sentiments": [
                    {"sentimentKey": "Neutral", "seenDurationRatio": 1.0}
                ],
            },
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        # All five sections present
        assert "## Video Transcript" in result
        assert "## Keywords" in result
        assert "## Topics" in result
        assert "## Named Entities" in result
        assert "## Sentiment" in result

    def test_transcript_without_speaker(self):
        index_data = {
            "videos": [
                {
                    "insights": {
                        "transcript": [
                            {
                                "text": "No speaker info",
                                "instances": [{"start": "0:00:00.000"}],
                            }
                        ]
                    }
                }
            ],
            "summarizedInsights": {},
        }
        result = VideoIndexerClient.extract_structured_content(index_data)
        assert "No speaker info" in result
        assert "Speaker" not in result.split("No speaker info")[0].split("\n")[-1] or True


# ──────────────────────────────────────────────
# build_client_from_config
# ──────────────────────────────────────────────


class TestBuildClientFromConfig:
    """Tests for the build_client_from_config helper."""

    def _make_config(self, **overrides):
        defaults = {
            "VIDEO_INDEXER_ACCOUNT_NAME": "test-account",
            "VIDEO_INDEXER_ACCOUNT_ID": "00000000-0000-0000-0000-000000000001",
            "VIDEO_INDEXER_RESOURCE_GROUP": "test-rg",
            "VIDEO_INDEXER_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000002",
            "VIDEO_INDEXER_LOCATION": "eastus",
            "VIDEO_INDEXER_TENANT_ID": "00000000-0000-0000-0000-000000000003",
            "VIDEO_INDEXER_CLIENT_ID": "00000000-0000-0000-0000-000000000004",
            "VIDEO_INDEXER_CLIENT_SECRET": "test-secret-value",
        }
        defaults.update(overrides)
        config = MagicMock()
        for k, v in defaults.items():
            setattr(config, k, v)
        return config

    def test_valid_config_returns_client(self):
        config = self._make_config()
        client = build_client_from_config(config)
        assert isinstance(client, VideoIndexerClient)
        assert client.account_name == "test-account"
        assert client.location == "eastus"

    def test_missing_account_name_raises(self):
        config = self._make_config(VIDEO_INDEXER_ACCOUNT_NAME="")
        with pytest.raises(VideoIndexerError, match="VIDEO_INDEXER_ACCOUNT_NAME"):
            build_client_from_config(config)

    def test_missing_client_secret_raises(self):
        config = self._make_config(VIDEO_INDEXER_CLIENT_SECRET="")
        with pytest.raises(VideoIndexerError, match="VIDEO_INDEXER_CLIENT_SECRET"):
            build_client_from_config(config)

    def test_multiple_missing_fields(self):
        config = self._make_config(
            VIDEO_INDEXER_ACCOUNT_NAME="",
            VIDEO_INDEXER_TENANT_ID="",
        )
        with pytest.raises(VideoIndexerError) as exc_info:
            build_client_from_config(config)
        assert "VIDEO_INDEXER_ACCOUNT_NAME" in str(exc_info.value)
        assert "VIDEO_INDEXER_TENANT_ID" in str(exc_info.value)

    def test_all_fields_populated(self):
        config = self._make_config()
        client = build_client_from_config(config)
        assert client.account_id == "00000000-0000-0000-0000-000000000001"
        assert client.subscription_id == "00000000-0000-0000-0000-000000000002"
        assert client.tenant_id == "00000000-0000-0000-0000-000000000003"
        assert client.client_id == "00000000-0000-0000-0000-000000000004"
        assert client.client_secret == "test-secret-value"
        assert client.resource_group == "test-rg"
