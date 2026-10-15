from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

MAX_OUTPUT_BYTES = 1_048_576
MAX_RESULT_BYTES = 65_536
MAX_PROMPT_BYTES = 262_144

_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "CODEX_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "no_proxy",
    }
)
_DISABLED_FEATURES = (
    "shell_tool",
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "multi_agent",
    "goals",
    "view_image",
    "image_generation",
    "browser_use",
    "computer_use",
    "skill_search",
    "shell_snapshot",
    "memories",
    "tool_suggest",
    "sleep_tool",
    "code_mode",
    "code_mode_host",
    "unbounded_connection_retries",
)
_CONFIG = (
    'web_search="disabled"',
    "project_doc_max_bytes=0",
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "tools.update_plan.enabled=false",
    "tools.experimental_request_user_input.enabled=false",
    "agents.enabled=false",
    'model_reasoning_effort="low"',
    "analytics.enabled=false",
    'history.persistence="none"',
)


class BackendError(RuntimeError):
    """A bounded, non-sensitive error safe to show in hook diagnostics."""

    def __init__(self, message: str, *, usage: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.usage = dict(usage or {})


class ReviewResponse(dict[str, Any]):
    def __init__(self, response: dict[str, Any], usage: dict[str, int]) -> None:
        super().__init__(response)
        self.usage = usage


def _terminate(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def _stop(process: subprocess.Popen[bytes]) -> None:
    # The npm launcher can exit before its child; always signal the process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _failure(error: object) -> str:
    message = error.get("message", "") if isinstance(error, dict) else ""
    message = message.lower() if isinstance(message, str) else ""
    categories = (
        (
            ("unauthorized", "authentication", "invalid_api_key", "401", "token could not"),
            "Codex authentication failed; run `codex login` before another check",
        ),
        (
            ("rate limit", "rate_limit", "usage limit", "usage_limit", "quota", "429"),
            "Codex usage or rate limit reached; wait for the limit to reset",
        ),
        (
            ("model_not_found", "model is not supported", "model does not exist"),
            "Codex reviewer model is unavailable; choose an available model in settings",
        ),
        (
            ("invalid_json_schema", "invalid schema", "response_format"),
            "Codex rejected the review schema; check plugin and model compatibility",
        ),
        (
            ("context_length_exceeded", "context length", "maximum context"),
            "Codex review exceeds the model context limit; reduce max_context_chars",
        ),
        (
            (
                "stream disconnected",
                "reconnect",
                "connection",
                "network",
                "error sending request",
                "timed out",
                "bad gateway",
                "service unavailable",
            ),
            "Codex connection failed; check network and service availability",
        ),
    )
    for needles, diagnostic in categories:
        if any(needle in message for needle in needles):
            return diagnostic
    return "Codex review failed without a recognized cause; check Codex CLI diagnostics"


def _event(line: bytes, usage: dict[str, int] | None = None) -> tuple[bool, str | None]:
    try:
        event = json.loads(line)
    except (UnicodeError, ValueError) as exc:
        raise BackendError("Codex returned an invalid event stream") from exc
    if not isinstance(event, dict):
        raise BackendError("Codex returned an invalid event stream")
    kind = event.get("type")
    if kind == "turn.failed":
        raise BackendError(_failure(event.get("error")))
    if kind == "error":
        # Codex keeps running after error notifications and may recover before turn.completed.
        return False, _failure(event)
    if kind in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item")
        if not isinstance(item, dict):
            raise BackendError("Codex returned an invalid item")
        # Events report some actions after execution; this is rejection, not prevention.
        if item.get("type") not in {"agent_message", "reasoning", "error"}:
            raise BackendError("Codex attempted tool use; review discarded")
    elif kind not in {"thread.started", "turn.started", "turn.completed"}:
        raise BackendError("Codex returned an unsupported event")
    if kind == "turn.completed" and usage is not None:
        reported = event.get("usage")
        if isinstance(reported, dict):
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                value = reported.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key] = usage.get(key, 0) + value
    return kind == "turn.completed", None


def _run(command: list[str], prompt: bytes, workspace: Path, timeout: float) -> dict[str, int]:
    environment = {key: value for key, value in os.environ.items() if key in _ENVIRONMENT}
    environment["YSK_REVIEWER_ACTIVE"] = "1"
    pending = bytearray()
    total = 0
    completed = False
    last_error = None
    usage: dict[str, int] = {}
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as input_file:
        input_file.write(prompt)
        input_file.seek(0)
        try:
            process = subprocess.Popen(
                command,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workspace,
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendError("Could not start Codex CLI") from exc
        previous_sigterm = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.signal(signal.SIGTERM, _terminate)
        try:
            assert process.stdout is not None
            assert process.stderr is not None
            with process.stdout, process.stderr, selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        detail = f". {last_error}" if last_error else ""
                        raise BackendError(f"Codex review timed out{detail}")
                    for key, _ in selector.select(min(remaining, 0.2)):
                        chunk = os.read(key.fd, 65_536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise BackendError("Codex review exceeded its output limit")
                        if key.data:
                            pending.extend(chunk)
                            while b"\n" in pending:
                                line, _, rest = pending.partition(b"\n")
                                pending = bytearray(rest)
                                if line.strip():
                                    finished, diagnostic = _event(bytes(line), usage)
                                    completed = finished or completed
                                    last_error = diagnostic or last_error
                if pending.strip():
                    finished, diagnostic = _event(bytes(pending), usage)
                    completed = finished or completed
                    last_error = diagnostic or last_error
                try:
                    return_code = process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise BackendError("Codex review timed out") from exc
                if return_code:
                    if last_error:
                        raise BackendError(f"{last_error} (CLI status {return_code})")
                    raise BackendError(f"Codex CLI exited with status {return_code}")
                if not completed:
                    raise BackendError(last_error or "Codex did not complete the review")
        except BackendError as error:
            error.usage = dict(usage)
            raise
        finally:
            try:
                _stop(process)
            finally:
                if previous_sigterm is not None:
                    signal.signal(signal.SIGTERM, previous_sigterm)
    return usage


def review(
    prompt: str,
    schema_path: Path,
    model: str | None = None,
    timeout: float = 60.0,
) -> ReviewResponse:
    """Run an independent restricted CLI review; the caller validates its schema."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise BackendError("Review timeout must be positive and finite")
    encoded = prompt.encode("utf-8")
    if not encoded or len(encoded) > MAX_PROMPT_BYTES:
        raise BackendError("Review prompt is empty or exceeds its size limit")
    executable = shutil.which("codex")
    if not executable:
        raise BackendError("Codex CLI was not found on PATH")
    try:
        schema = schema_path.resolve(strict=True)
    except OSError as exc:
        raise BackendError("Review schema is unavailable") from exc
    with tempfile.TemporaryDirectory(prefix="codex-ysk-") as temporary:
        workspace = Path(temporary)
        output = workspace / "review.json"
        command = [
            executable,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output),
            "--json",
            "--color",
            "never",
            "--cd",
            str(workspace),
        ]
        for feature in _DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.extend(("--enable", "skip_host_skill_discovery"))
        for setting in _CONFIG:
            command.extend(("-c", setting))
        if model:
            command.extend(("--model", model))
        command.append("-")
        usage = _run(command, encoded, workspace, timeout)
        try:
            with output.open("rb") as result_file:
                raw = result_file.read(MAX_RESULT_BYTES + 1)
            if len(raw) > MAX_RESULT_BYTES:
                raise BackendError("Codex review exceeded its result limit", usage=usage)
            result = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BackendError("Codex returned no valid JSON review", usage=usage) from exc
        if not isinstance(result, dict):
            raise BackendError("Codex review must be a JSON object", usage=usage)
        return ReviewResponse(result, usage)
