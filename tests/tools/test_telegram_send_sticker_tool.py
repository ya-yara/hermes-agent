"""Tests for telegram_send_sticker_tool."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestCallSendSticker:
    def _make_mock_session(self, response_json: dict):
        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value=response_json)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    def test_success_response(self):
        from tools.telegram_send_sticker_tool import _call_send_sticker

        mock_session = self._make_mock_session(
            {"ok": True, "result": {"message_id": 55, "sticker": {"file_id": "CAACAgIABC"}}}
        )
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = _run(_call_send_sticker("TOKEN", "123", "CAACAgIABC", reply_to=None))

        assert result["success"] is True
        assert result["message_id"] == 55

    def test_api_error_propagated(self):
        from tools.telegram_send_sticker_tool import _call_send_sticker

        mock_session = self._make_mock_session({"ok": False, "description": "STICKER_ID_INVALID"})
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = _run(_call_send_sticker("TOKEN", "123", "BAD_ID", reply_to=None))

        assert "error" in result
        assert "STICKER_ID_INVALID" in result["error"]

    def test_reply_to_included_in_payload(self):
        from tools.telegram_send_sticker_tool import _call_send_sticker

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 56}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            _run(_call_send_sticker("TOKEN", "123", "CAACAgIABC", reply_to="42"))

        call_args = mock_session.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload.get("reply_parameters", {}).get("message_id") == 42


class TestSendStickerTool:
    def test_missing_file_id_returns_error(self):
        from tools.telegram_send_sticker_tool import send_sticker_tool

        result = json.loads(send_sticker_tool({}))
        assert result == {"error": "file_id is required"}

    def test_no_token_returns_error(self):
        with patch("tools.telegram_send_sticker_tool._get_telegram_token", return_value=None):
            from tools.telegram_send_sticker_tool import send_sticker_tool
            result = json.loads(send_sticker_tool({"file_id": "CAACAgI", "chat_id": "1"}))
        assert "configured" in result["error"]

    def test_no_chat_id_returns_error(self):
        with (
            patch("tools.telegram_send_sticker_tool._get_telegram_token", return_value="tok"),
            patch("tools.telegram_send_sticker_tool._get_current_chat_id", return_value=None),
        ):
            from tools.telegram_send_sticker_tool import send_sticker_tool
            result = json.loads(send_sticker_tool({"file_id": "CAACAgI"}))
        assert "chat_id" in result["error"]

    def test_uses_session_context_for_chat_id(self):
        mock_send = AsyncMock(return_value={"success": True, "message_id": 10})
        with (
            patch("tools.telegram_send_sticker_tool._get_telegram_token", return_value="tok"),
            patch("tools.telegram_send_sticker_tool._get_current_chat_id", return_value="456") as ctx_mock,
            patch("tools.telegram_send_sticker_tool._call_send_sticker", mock_send),
        ):
            from tools.telegram_send_sticker_tool import send_sticker_tool
            result = json.loads(send_sticker_tool({"file_id": "CAACAgI"}))

        ctx_mock.assert_called_once()
        assert result["success"] is True

    def test_explicit_chat_id_skips_context(self):
        mock_send = AsyncMock(return_value={"success": True, "message_id": 11})
        with (
            patch("tools.telegram_send_sticker_tool._get_telegram_token", return_value="tok"),
            patch("tools.telegram_send_sticker_tool._get_current_chat_id") as ctx_mock,
            patch("tools.telegram_send_sticker_tool._call_send_sticker", mock_send),
        ):
            from tools.telegram_send_sticker_tool import send_sticker_tool
            result = json.loads(send_sticker_tool({"file_id": "CAACAgI", "chat_id": "999"}))

        ctx_mock.assert_not_called()
        assert result["success"] is True


class TestRegistration:
    def test_tool_registered_in_messaging_toolset(self):
        import tools.telegram_send_sticker_tool  # noqa: F401
        from tools.registry import registry

        tool = registry.get_entry("send_sticker")
        assert tool is not None
        assert tool.toolset == "messaging"

    def test_schema_requires_file_id(self):
        import tools.telegram_send_sticker_tool  # noqa: F401
        from tools.registry import registry

        schema = registry.get_entry("send_sticker").schema
        assert "file_id" in schema["parameters"]["required"]
