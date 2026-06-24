"""Tests for Telegram final-response brevity guard."""

from __future__ import annotations

import pytest

from gateway.config import Platform
from gateway.telegram_brevity_guard import (
    build_telegram_brevity_prompt,
    maybe_rewrite_for_telegram_brevity,
    should_skip_telegram_brevity_guard,
)


def _cfg(**overrides):
    guard = {
        "enabled": True,
        "user_name": "user",
        "soft_chars": 80,
        "hard_chars": 160,
        "target_chars": 60,
        "skip_if_user_asked_detail": True,
        "skip_code_blocks": True,
        "skip_media_messages": True,
    }
    guard.update(overrides)
    return {"telegram": {"brevity_guard": guard}}


def _long_text(marker: str = "важная деталь") -> str:
    return (
        f"Вердикт: делаем вариант B, потому что {marker}. "
        "Файл config.yaml менять не надо. Команда: hermes gateway restart. "
        "Риск: не трогать ручные настройки без проверки. "
    ) * 4


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


@pytest.mark.asyncio
async def test_short_telegram_message_is_unchanged():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("rewritten")

    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text="Коротко: готово.",
        user_message="что там?",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == "Коротко: готово."
    assert calls == []


@pytest.mark.asyncio
async def test_long_telegram_message_is_rewritten():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("Вариант B. config.yaml не трогаем. `hermes gateway restart`. Разверну, если надо.")

    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=_long_text("не ломает кэш"),
        user_message="что делать?",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result.startswith("Вариант B.")
    assert "hermes gateway restart" in result
    assert len(calls) == 1
    assert calls[0]["task"] == "telegram_brevity_guard"


@pytest.mark.asyncio
async def test_long_detail_request_is_unchanged():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = _long_text()
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="подробно распиши полный анализ",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_long_code_block_is_unchanged_when_configured():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = "Вот файл:\n\n```python\n" + "\n".join(f"print({i})" for i in range(80)) + "\n```"
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="покажи код",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_long_indented_code_block_is_unchanged_when_configured():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = (
        "Вот фрагмент:\n\n"
        "    def handle(event):\n"
        "        if event.ready:\n"
        "            return run(event.payload)\n"
        "        raise RuntimeError('not ready')\n"
    ) * 6
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="покажи код",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_plain_assignment_call_code_snippet_is_unchanged_when_configured():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = "\n".join(
        [
            "config = load_config(path)",
            "client = make_client(config)",
            'result = client.fetch("telegram")',
            "print(result)",
            "config = load_config(path)",
            "client = make_client(config)",
            'result = client.fetch("telegram")',
            "print(result)",
        ]
    )
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="покажи скрипт",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_long_media_marker_is_unchanged_when_configured():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = _long_text() + "Вложение: MEDIA:/tmp/report.png"
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="что делать?",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_non_telegram_platform_is_unchanged():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("short")

    draft = _long_text()
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.DISCORD,
        outgoing_text=draft,
        user_message="что делать?",
        user_config=_cfg(),
        llm_call=fake_llm,
    )

    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_rewrite_failure_delivers_original():
    async def failing_llm(**kwargs):
        raise RuntimeError("no auxiliary provider")

    draft = _long_text()
    result = await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=draft,
        user_message="что делать?",
        user_config=_cfg(),
        llm_call=failing_llm,
    )

    assert result == draft


def test_prompt_preserves_key_facts_and_hard_instruction():
    draft = (
        "Итог: выбрать вариант B. Число: 42. Файл: config.yaml. "
        "Команда: `hermes gateway restart`."
    )

    messages = build_telegram_brevity_prompt(
        user_message="что делать?",
        draft_answer=draft,
        target_chars=90,
        hard_limit=True,
    )

    combined = "\n".join(m["content"] for m in messages)
    assert "config.yaml" in combined
    assert "hermes gateway restart" in combined
    assert "42" in combined
    assert "telegram" in combined.lower()
    assert "90" in combined
    assert any(word in combined.lower() for word in ("hard", "strict", "must", "limit"))


def test_prompt_uses_configured_user_name_instead_of_hardcoded_personal_name():
    messages = build_telegram_brevity_prompt(
        user_message="summarize",
        draft_answer="Do the safe option.",
        target_chars=90,
        hard_limit=False,
        user_name="Alex",
    )

    system = messages[0]["content"]
    old_hardcoded_name = "S" + "ergey"
    assert "for Alex" in system
    assert old_hardcoded_name not in system


@pytest.mark.asyncio
async def test_rewrite_prompt_uses_user_name_from_config():
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("Shorter answer")

    await maybe_rewrite_for_telegram_brevity(
        platform=Platform.TELEGRAM,
        outgoing_text=_long_text("custom user"),
        user_message="summarize",
        user_config=_cfg(user_name="Alex"),
        llm_call=fake_llm,
    )

    system = calls[0]["messages"][0]["content"]
    old_hardcoded_name = "S" + "ergey"
    assert "for Alex" in system
    assert old_hardcoded_name not in system

def test_exact_content_skips_patch_and_json():
    patch_text = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n" + ("x" * 120)
    json_text = '{"status": "ok", "items": [1, 2, 3]}' + (" " * 120)

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=patch_text,
        user_message="пришли diff",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True
    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=json_text,
        user_message="пришли json",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_python_traceback():
    traceback_text = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/app.py", line 12, in <module>\n'
        "    main()\n"
        '  File "/tmp/app.py", line 8, in main\n'
        "    raise RuntimeError('boom')\n"
        "RuntimeError: boom\n"
    ) + ("x" * 120)

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=traceback_text,
        user_message="что за ошибка?",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_stop_iteration_traceback():
    traceback_text = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/app.py", line 12, in <module>\n'
        "    next(items)\n"
        "StopIteration\n"
    ) + ("x" * 120)

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=traceback_text,
        user_message="что за ошибка?",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_single_line_shell_command():
    command = (
        "curl -H 'Authorization: Bearer token' "
        "https://example.com/api/items?project=hermes-agent&limit=100 "
        "--data '{\"filename\":\"config.yaml\",\"command\":\"hermes gateway restart\"}'"
    )

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=command,
        user_message="пришли команду",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_short_intro_plus_shell_command():
    command = (
        "Вот команда:\n\n"
        "curl -H 'Authorization: Bearer token' "
        "https://example.com/api/items?project=hermes-agent&limit=100 "
        "--data '{\"filename\":\"config.yaml\",\"command\":\"hermes gateway restart\"}'"
    )

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=command,
        user_message="пришли команду",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_labeled_inline_shell_command():
    command = (
        "Command: `curl -H 'Authorization: Bearer token' "
        "https://example.com/api/items?project=hermes-agent&limit=100 "
        "--data '{\"filename\":\"config.yaml\",\"command\":\"hermes gateway restart\"}'`"
    )

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=command,
        user_message="send the command",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


@pytest.mark.parametrize(
    "command",
    [
        (
            "sudo bash -lc 'cd /srv/hermes-agent && "
            "HERMES_HOME=/tmp/hermes-agent-test hermes gateway restart --profile telegram-sergey'"
        ),
        (
            "bash -lc 'source .venv/bin/activate && "
            "python -m pytest tests/gateway/test_telegram_brevity_guard.py -q --disable-warnings'"
        ),
        (
            "env HERMES_HOME=/tmp/hermes-agent-test sh -c "
            "'hermes gateway run --platform telegram --config /tmp/config.yaml'"
        ),
    ],
)
def test_exact_content_skips_shell_wrapper_commands(command):
    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=f"Command: `{command}`",
        user_message="send the command",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True


def test_exact_content_skips_single_line_sql():
    sql = (
        "SELECT id, filename, command, created_at FROM gateway_events "
        "WHERE filename = 'config.yaml' AND command = 'hermes gateway restart' "
        "ORDER BY created_at DESC LIMIT 50"
    )

    assert should_skip_telegram_brevity_guard(
        platform=Platform.TELEGRAM,
        outgoing_text=sql,
        user_message="пришли sql",
        user_config=_cfg(skip_if_user_asked_detail=False),
    )[0] is True
