"""Tests for the file_id additions to sticker_cache."""
import json
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_cache(tmp_path):
    cache_path = tmp_path / "sticker_cache.json"
    with patch("gateway.sticker_cache.CACHE_PATH", cache_path):
        yield cache_path


class TestCacheStoresFileId:
    def test_file_id_stored_and_retrieved(self, tmp_cache):
        from gateway.sticker_cache import cache_sticker_description, get_cached_description

        cache_sticker_description(
            file_unique_id="unique123",
            description="a waving cat",
            emoji="😸",
            set_name="CatPack",
            file_id="CAACAgIABC",
        )
        entry = get_cached_description("unique123")
        assert entry["file_id"] == "CAACAgIABC"

    def test_missing_file_id_defaults_to_empty(self, tmp_cache):
        from gateway.sticker_cache import cache_sticker_description, get_cached_description

        cache_sticker_description(
            file_unique_id="unique456",
            description="a dancing dog",
        )
        entry = get_cached_description("unique456")
        assert entry.get("file_id", "") == ""

    def test_existing_cache_entries_without_file_id_still_load(self, tmp_cache):
        legacy = {
            "unique789": {
                "description": "old sticker",
                "emoji": "😀",
                "set_name": "",
                "cached_at": time.time(),
            }
        }
        tmp_cache.write_text(json.dumps(legacy))

        from gateway.sticker_cache import get_cached_description

        entry = get_cached_description("unique789")
        assert entry["description"] == "old sticker"
        assert entry.get("file_id", "") == ""


class TestBuildStickerInjectionIncludesFileId:
    def test_file_id_present_in_injection(self):
        from gateway.sticker_cache import build_sticker_injection

        text = build_sticker_injection(
            description="a cat waving",
            emoji="😸",
            set_name="CatPack",
            file_id="CAACAgIABC",
        )
        assert "CAACAgIABC" in text
        assert "a cat waving" in text

    def test_no_file_id_omits_hint(self):
        from gateway.sticker_cache import build_sticker_injection

        text = build_sticker_injection(description="a dog sitting")
        assert "file_id" not in text

    def test_backward_compatible_call_without_file_id_kwarg(self):
        from gateway.sticker_cache import build_sticker_injection

        text = build_sticker_injection("some sticker", "😀", "MyPack")
        assert "some sticker" in text
