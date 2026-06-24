"""Telegram final-response brevity guard.

This module owns only delivery-edge shortening logic. It does not mutate
conversation history or agent prompts; callers pass the already-produced
outgoing text and receive either a shorter delivery text or the original.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from gateway.config import Platform

logger = logging.getLogger(__name__)


DEFAULT_BREVITY_GUARD: dict[str, Any] = {
    "enabled": False,
    "user_name": "user",
    "soft_chars": 1600,
    "hard_chars": 3000,
    "target_chars": 900,
    "skip_if_user_asked_detail": True,
    "skip_code_blocks": True,
    "skip_media_messages": True,
}

_BOOL_KEYS = {"enabled", "skip_if_user_asked_detail", "skip_code_blocks", "skip_media_messages"}
_INT_KEYS = {"soft_chars", "hard_chars", "target_chars"}

_DETAIL_PATTERNS = (
    r"\bdeep\s+dive\b",
    r"\bdetailed\b",
    r"\bfull\s+report\b",
    r"\brfc\b",
    r"\bimplementation\s+plan\b",
    r"подробн",
    r"развернут",
    r"развёрнут",
    r"полный\s+анализ",
    r"постмортем",
    r"\bплан\b",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        return default
    if isinstance(value, int):
        return value > 0
    return default


def _coerce_positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_user_name(value: Any, default: str = "user") -> str:
    if value is None:
        return default
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized or default


def resolve_brevity_guard_config(user_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resolve and normalize Telegram brevity-guard config.

    Canonical config lives at ``telegram.brevity_guard``. The older
    ``gateway.telegram.brevity_guard`` shape is accepted as compatibility
    input, with canonical values taking precedence when both are present.
    """

    root = _as_mapping(user_config)
    gateway_guard = _as_mapping(_as_mapping(_as_mapping(root.get("gateway")).get("telegram")).get("brevity_guard"))
    telegram_guard = _as_mapping(_as_mapping(root.get("telegram")).get("brevity_guard"))

    resolved = dict(DEFAULT_BREVITY_GUARD)
    resolved.update(gateway_guard)
    resolved.update(telegram_guard)

    for key in _BOOL_KEYS:
        resolved[key] = _coerce_bool(resolved.get(key), DEFAULT_BREVITY_GUARD[key])
    for key in _INT_KEYS:
        resolved[key] = _coerce_positive_int(resolved.get(key), DEFAULT_BREVITY_GUARD[key])
    resolved["user_name"] = _coerce_user_name(
        resolved.get("user_name"), DEFAULT_BREVITY_GUARD["user_name"]
    )

    return resolved


def _is_telegram_platform(platform: Any) -> bool:
    if platform == Platform.TELEGRAM:
        return True
    value = getattr(platform, "value", platform)
    return isinstance(value, str) and value.strip().lower() == Platform.TELEGRAM.value


def _looks_like_media_message(text: str) -> bool:
    return bool(re.search(r"(^|\s)MEDIA:/\S+", text))


def _has_fenced_code_block(text: str) -> bool:
    return bool(re.search(r"```[\s\S]*?```", text))


def _line_looks_like_code(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(
        r"^(async\s+def|def|class|if|elif|else:|for|while|try:|except|finally:|with|return|raise|"
        r"import|from|const|let|var|function|export|interface|type|public|private|protected|func|package)\b",
        stripped,
    ):
        return True
    if re.match(r"^[\w.$\[\]'\"-]+\s*(=|:=|\+=|-=|\*=|/=)\s*\S", stripped):
        return True
    if re.search(r"(=>|->|==|!=|<=|>=|\{|\}|;)", stripped):
        return True
    if re.search(r"\w+\([^)]*\)\s*(:|\{|;)?$", stripped):
        return True
    return False


def _line_looks_like_assignment_or_call(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^[\w.$\[\]'\"-]+\s*(=|:=|\+=|-=|\*=|/=)\s*\S", stripped):
        return True
    return bool(re.match(r"^[\w.]+\([^)]*\)\s*(#.*)?$", stripped))


def _is_short_exact_content_intro(line: str) -> bool:
    stripped = line.strip().strip("`").strip()
    if not stripped or len(stripped) > 80:
        return False
    return bool(
        re.match(
            r"^(?:вот\s+)?(?:команда|command|run|запусти|sql|query|запрос|пример|example|use|используй)\s*:?\s*$",
            stripped,
            flags=re.IGNORECASE,
        )
        or stripped.endswith(":")
    )


def _strip_exact_line_wrappers(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^\s*(?:[-*]\s+|>\s*)", "", stripped)
    stripped = re.sub(
        r"^(?:вот\s+)?(?:команда|command|run|запусти|sql|query|запрос|пример|example|use|используй)\s*:\s*",
        "",
        stripped,
        flags=re.IGNORECASE,
    )
    return stripped.strip().strip("`").strip()


def _single_line_looks_like_exact_command_or_code(line: str) -> bool:
    stripped = _strip_exact_line_wrappers(line)
    if not stripped or len(stripped) < 80:
        return False

    command = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)+", "", stripped)
    first_token = command.split(None, 1)[0].strip("`\"'")
    command_names = {
        "arc",
        "awk",
        "bash",
        "cat",
        "chmod",
        "chown",
        "cp",
        "curl",
        "dd",
        "docker",
        "env",
        "find",
        "git",
        "grep",
        "hermes",
        "journalctl",
        "jq",
        "kubectl",
        "mysql",
        "node",
        "npm",
        "npx",
        "openssl",
        "pip",
        "pip3",
        "pnpm",
        "psql",
        "python",
        "python3",
        "rg",
        "rm",
        "rsync",
        "scp",
        "sed",
        "sh",
        "sqlite3",
        "ssh",
        "sudo",
        "systemctl",
        "tar",
        "wget",
        "xargs",
        "ya",
        "yarn",
    }
    if first_token in command_names or first_token.startswith(("./", "../", "/")):
        return True

    lowered = stripped.lower()
    if re.match(r"^\s*(select\b.+\bfrom\b|insert\s+into\b|update\b.+\bset\b|delete\s+from\b)", lowered):
        return True
    if re.match(r"^\s*(create|alter|drop)\s+(table|index|view|schema)\b", lowered):
        return True
    if re.match(r"^\s*with\b.+\bselect\b.+\bfrom\b", lowered):
        return True

    return _line_looks_like_assignment_or_call(stripped) and bool(re.search(r"(\(|\)|=|:=)", stripped))


def _looks_like_single_line_exact_command_or_code(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if "\n" not in stripped:
        return _single_line_looks_like_exact_command_or_code(stripped)

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) > 3:
        return False
    exact_lines = [line for line in lines if _single_line_looks_like_exact_command_or_code(line)]
    if len(exact_lines) != 1:
        return False
    return all(
        line in exact_lines or _is_short_exact_content_intro(line)
        for line in lines
    )


def _has_indented_code_block(text: str) -> bool:
    lines = text.splitlines()
    indented_code_lines = [
        line
        for line in lines
        if (line.startswith("    ") or line.startswith("\t")) and _line_looks_like_code(line)
    ]
    return len(indented_code_lines) >= 3


def _looks_like_plain_code_snippet(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 5:
        return False
    code_lines = sum(1 for line in lines if _line_looks_like_code(line))
    structural_lines = sum(
        1
        for line in lines
        if re.match(
            r"^\s*(async\s+def|def|class|function|if|for|while|try|except|import|from|const|let|var|"
            r"export|interface|type|func|package)\b",
            line,
        )
    )
    assignment_or_call_lines = sum(1 for line in lines if _line_looks_like_assignment_or_call(line))
    if code_lines >= 4 and code_lines / len(lines) >= 0.6 and structural_lines >= 1:
        return True
    return assignment_or_call_lines >= 6 and assignment_or_call_lines / len(lines) >= 0.75


def _looks_like_code_block_or_snippet(text: str) -> bool:
    return _has_fenced_code_block(text) or _has_indented_code_block(text) or _looks_like_plain_code_snippet(text)


def _user_asked_for_detail(user_message: str) -> bool:
    normalized = (user_message or "").lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _DETAIL_PATTERNS)


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except (TypeError, ValueError):
        return False
    return True


def _looks_like_diff_or_patch(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.startswith(("diff --git ", "Index: ")):
        return True
    lines = text.splitlines()
    if any(line.startswith(("--- ", "+++ ")) for line in lines) and any(line.startswith("@@") for line in lines):
        return True
    if len(lines) >= 3 and any(line.startswith("@@") for line in lines):
        changed = sum(1 for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
        return changed >= 2
    return False


def _looks_like_yamlish_multiline(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    key_value_lines = sum(
        1
        for line in lines
        if re.match(r"^\s*[-\w.]+\s*:\s+\S", line) or re.match(r"^\s*-\s+[-\w.]+\s*:\s*\S?", line)
    )
    list_lines = sum(1 for line in lines if re.match(r"^\s*-\s+\S", line))
    return key_value_lines >= 3 and (key_value_lines + list_lines) / len(lines) >= 0.6


def _looks_like_python_traceback(text: str) -> bool:
    if "Traceback (most recent call last):" not in text:
        return False
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    has_frame = any(re.match(r'^\s*File "[^"]+", line \d+', line) for line in lines)
    final_line = lines[-1].strip() if lines else ""
    has_exception = bool(re.match(r"^[A-Za-z_][\w.]*(:\s+.*)?$", final_line))
    return has_frame and has_exception


def _looks_like_log_or_command_output(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    log_lines = sum(
        1
        for line in lines
        if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", line)
        or re.match(r"^\[[^\]]*(INFO|WARN|WARNING|ERROR|DEBUG|TRACE)[^\]]*\]", line, flags=re.IGNORECASE)
        or re.match(r"^(INFO|WARN|WARNING|ERROR|DEBUG|TRACE)\b", line)
        or line.startswith(("Traceback (most recent call last):", "$ ", "> ", ">>> "))
    )
    return log_lines >= 3 and log_lines / len(lines) >= 0.5


def _looks_like_exact_content(text: str) -> bool:
    return (
        _looks_like_diff_or_patch(text)
        or _looks_like_json(text)
        or _looks_like_yamlish_multiline(text)
        or _looks_like_python_traceback(text)
        or _looks_like_log_or_command_output(text)
        or _looks_like_single_line_exact_command_or_code(text)
    )


def should_skip_telegram_brevity_guard(
    platform: Any,
    outgoing_text: str,
    user_message: str,
    user_config: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Return whether the Telegram brevity guard should skip this message."""

    if not _is_telegram_platform(platform):
        return True, "non_telegram"

    config = resolve_brevity_guard_config(user_config)
    if not config["enabled"]:
        return True, "disabled"

    text = outgoing_text or ""
    if len(text) <= config["soft_chars"]:
        return True, "below_soft_limit"

    if config["skip_media_messages"] and _looks_like_media_message(text):
        return True, "media_message"

    if config["skip_code_blocks"] and _looks_like_code_block_or_snippet(text):
        return True, "code_block"

    if config["skip_if_user_asked_detail"] and _user_asked_for_detail(user_message):
        return True, "user_asked_detail"

    if _looks_like_exact_content(text):
        return True, "exact_content"

    return False, "eligible"


def build_telegram_brevity_prompt(
    user_message: str,
    draft_answer: str,
    target_chars: int,
    hard_limit: bool,
    user_name: str = "user",
) -> list[dict[str, str]]:
    """Build the auxiliary rewrite prompt for Telegram delivery."""

    limit_instruction = (
        f"Hard limit mode: the draft is over the hard threshold. You must compress aggressively, "
        f"stay within <= {target_chars} chars if at all possible, and keep only the actionable core."
        if hard_limit
        else f"Target <= {target_chars} chars."
    )
    user_name = _coerce_user_name(user_name)
    system = (
        f"Rewrite the outgoing Telegram message for {user_name}. Keep it short, alive, direct, and actionable. "
        "Match the user's language. Preserve decisions, numbers, filenames, commands, warnings, and concrete "
        "conclusions. Do not add new facts, do not invent status, and do not change meaning. "
        "Use an optional offer to expand only when useful. Return only the rewritten message. "
        f"{limit_instruction}"
    )
    user = (
        "User message:\n"
        f"{user_message or ''}\n\n"
        "Draft answer to shorten:\n"
        f"{draft_answer or ''}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_message_content(response: Any) -> str:
    choice = response.choices[0]
    message = getattr(choice, "message", None)
    if isinstance(message, Mapping):
        return str(message.get("content") or "").strip()
    return str(getattr(message, "content", "") or "").strip()


async def _call_llm(llm_call: Callable[..., Any], **kwargs: Any) -> Any:
    result = llm_call(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def maybe_rewrite_for_telegram_brevity(
    platform: Any,
    outgoing_text: str,
    user_message: str,
    user_config: Mapping[str, Any] | None,
    main_runtime: Mapping[str, Any] | None = None,
    llm_call: Callable[..., Awaitable[Any] | Any] | None = None,
) -> str:
    """Maybe rewrite a long Telegram response, failing open to the original."""

    skip, reason = should_skip_telegram_brevity_guard(platform, outgoing_text, user_message, user_config)
    if skip:
        logger.debug("Skipping Telegram brevity guard: %s", reason)
        return outgoing_text

    config = resolve_brevity_guard_config(user_config)
    target_chars = config["target_chars"]
    messages = build_telegram_brevity_prompt(
        user_message=user_message,
        draft_answer=outgoing_text,
        target_chars=target_chars,
        hard_limit=len(outgoing_text or "") > config["hard_chars"],
        user_name=config["user_name"],
    )
    max_tokens = max(64, min(1200, int(target_chars / 3) + 80))

    if llm_call is None:
        from agent.auxiliary_client import async_call_llm

        llm_call = async_call_llm

    try:
        response = await _call_llm(
            llm_call,
            task="telegram_brevity_guard",
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
            main_runtime=dict(main_runtime) if main_runtime is not None else None,
        )
        rewritten = _extract_message_content(response)
    except Exception as exc:
        logger.debug("Telegram brevity guard failed; delivering original text: %s", exc, exc_info=True)
        return outgoing_text

    if not rewritten:
        logger.debug("Telegram brevity guard returned empty text; delivering original")
        return outgoing_text
    if len(rewritten) >= len(outgoing_text or ""):
        logger.debug("Telegram brevity guard returned non-shorter text; delivering original")
        return outgoing_text
    return rewritten
