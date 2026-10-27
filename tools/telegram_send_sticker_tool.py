"""Send a Telegram sticker from the agent without any text reply."""

import json
import logging
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

_SCHEMA = {
    "name": "send_sticker",
    "description": (
        "Send a Telegram sticker to the current chat. "
        "Valid as the *only* response — no text needed. "
        "Use file_id from a sticker the user sent you "
        "(visible as [file_id: ...] in the sticker injection). "
        "Only available when Telegram gateway is configured.\n\n"
        "Examples:\n"
        "  Re-send a received sticker: send_sticker(file_id='CAACAgI...')\n"
        "  Reply with sticker: send_sticker(file_id='CAACAgI...', reply_to_message_id='42')"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": (
                    "Telegram file_id of the sticker. "
                    "When a user sends you a sticker, its file_id appears in the "
                    "'[file_id: ...]' hint in your context."
                ),
            },
            "chat_id": {
                "type": "string",
                "description": "Target chat ID. Defaults to the current session chat.",
            },
            "reply_to_message_id": {
                "type": "string",
                "description": "Message ID to reply to (optional).",
            },
        },
        "required": ["file_id"],
    },
}


def _get_telegram_token() -> Optional[str]:
    try:
        from gateway.config import load_gateway_config, Platform

        config = load_gateway_config()
        for pconfig in config.platforms:
            if pconfig.platform == Platform.TELEGRAM and pconfig.token:
                return pconfig.token
    except Exception:
        pass
    return None


def _get_current_chat_id() -> Optional[str]:
    try:
        from gateway.session_context import get_session_env

        return get_session_env("HERMES_SESSION_CHAT_ID") or None
    except Exception:
        pass
    return None


async def _call_send_sticker(
    token: str,
    chat_id: str,
    file_id: str,
    reply_to: Optional[str],
) -> dict:
    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp not installed — run: pip install aiohttp"}

    try:
        from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp

        _proxy = resolve_proxy_url()
        _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
    except Exception:
        _sess_kw, _req_kw = {}, {}

    url = f"https://api.telegram.org/bot{token}/sendSticker"
    payload: dict = {"chat_id": chat_id, "sticker": file_id}
    if reply_to:
        payload["reply_parameters"] = {"message_id": int(reply_to)}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15), **_sess_kw
        ) as session:
            async with session.post(url, json=payload, **_req_kw) as resp:
                data = await resp.json()
                if data.get("ok"):
                    msg = data["result"]
                    return {
                        "success": True,
                        "message_id": msg.get("message_id"),
                        "chat_id": chat_id,
                    }
                return {"error": f"Telegram API error: {data.get('description', 'unknown')}"}
    except Exception as exc:
        return {"error": f"Request failed: {exc}"}


def send_sticker_tool(args: dict, **kwargs) -> str:
    file_id: str = (args.get("file_id") or "").strip()
    if not file_id:
        return json.dumps({"error": "file_id is required"})

    chat_id: Optional[str] = args.get("chat_id") or None
    if not chat_id:
        chat_id = _get_current_chat_id()

    if not chat_id:
        return json.dumps({"error": "chat_id could not be determined — pass chat_id= explicitly"})

    token = _get_telegram_token()
    if not token:
        return json.dumps({"error": "Telegram gateway not configured or no bot token found"})

    reply_to: Optional[str] = args.get("reply_to_message_id") or None

    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _call_send_sticker(token, chat_id, file_id, reply_to))
                result = future.result(timeout=20)
        else:
            result = loop.run_until_complete(_call_send_sticker(token, chat_id, file_id, reply_to))
    except Exception as exc:
        result = {"error": f"Async execution failed: {exc}"}

    return json.dumps(result)


registry.register(
    name="send_sticker",
    toolset="messaging",
    schema=_SCHEMA,
    handler=lambda args, **kw: send_sticker_tool(args, **kw),
    check_fn=lambda: _get_telegram_token() is not None,
    emoji="🎭",
)
