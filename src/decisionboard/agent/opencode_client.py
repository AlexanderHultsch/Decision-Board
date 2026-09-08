"""OpenCode client behind the ``AiProvider`` interface (spec 3.8).

Spec 3.8 is the contract this module implements, verified against OpenCode
1.18.11 on the target machine. ``opencode run --format json`` prints **JSON
Lines** - one JSON object per line, not a JSON array - so the client parses
line by line rather than feeding the whole of stdout to ``json.loads``.

Spec 3.8 also records the cost characteristic that makes batching (AP-3)
worth keeping: the verification run - a four-word prompt, a two-token
answer - reported 8 025 input tokens of fixed overhead (OpenCode's own
system prompt and tool definitions), charged on every call regardless of
prompt size.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .provider import (
    AiNotConfiguredError,
    AiProvider,
    AiResult,
    TASK_MODEL_KEYS,
    resolve_model,
    token_limit,
)

_STDERR_TRIM = 2000
_LINE_TRIM = 200
# Windows' CreateProcess rejects a command line above 32,767 characters;
# the prompt is passed as one argument, so a large knowledge block can
# reach it. Refuse with a clear message before that happens.
_WINDOWS_COMMAND_LIMIT = 30000
_OPTIONAL_FLAGS = ("--auto", "--dir")


class OpenCodeError(RuntimeError):
    """An ``opencode run`` invocation failed or returned an unusable result.

    Raised rather than swallowed (NFR-8) for every failure mode the client
    recognises: the binary is missing, the run timed out, it exited
    non-zero, its output contains a line that is not valid JSON (OC-1), or
    it produced no ``text`` event at all (OC-5).
    """


def describe_failure(stdout: str | None, stderr: str | None) -> str:
    """What a failed ``opencode run`` actually said. With ``--format json``
    OpenCode reports errors on **stdout**, as JSON events, and often writes
    nothing to stderr at all - seen on the target machine on 8 September
    2026 as an error message that ended after the exit code. So the
    message is assembled from every ``error``-like event's text, then from
    whatever else stdout and stderr carry, never from stderr alone."""
    messages: list[str] = []
    other_lines: list[str] = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            other_lines.append(line.strip())
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        if "error" in event_type.lower():
            messages.append(_error_text(event))
        elif event_type == "text":
            other_lines.append(str(event.get("part", {}).get("text", "")).strip())
    stderr_text = (stderr or "").strip()
    parts = [m for m in messages if m]
    if stderr_text:
        parts.append(stderr_text[:_STDERR_TRIM])
    if not parts and other_lines:
        parts.append(" ".join(other_lines)[:_STDERR_TRIM])
    if not parts:
        return "(no output - run `opencode auth list` and `opencode models` to check login and model name)"
    return " | ".join(parts)


def _error_text(event: dict) -> str:
    """The human-readable part of an error event, whatever nesting the
    OpenCode version used."""
    for key in ("message", "error", "part", "data", "properties"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = _error_text(value)
            if inner:
                return inner
    name = event.get("name")
    return str(name) if name else json.dumps(event, ensure_ascii=False)[:_LINE_TRIM]


def _config_key(config: dict, dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


class OpenCodeProvider(AiProvider):
    """Runs a task through the ``opencode run`` CLI (spec 3.8)."""

    def __init__(
        self,
        config: dict,
        *,
        binary: str = "opencode",
        cwd: Path | str | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self._config = config
        self._binary = binary
        self._cwd = cwd
        self._timeout_seconds = timeout_seconds
        self._supported: frozenset[str] | None = None

    def complete(self, task: str, prompt: str) -> AiResult:
        """Runs ``task`` through ``opencode run`` and returns its ``AiResult``.

        AI-1's configured token limit (``provider.token_limits.<key>``) is
        observed here, never enforced: nothing is truncated and the run is
        never aborted for exceeding it - a run that did its work must not be
        thrown away for going over a budget, and OpenCode's own flag for
        capping tokens is unverified, so passing one would be a guess. What
        the limit buys is that going over it is visible afterwards, in
        ``AiResult.over_token_limit`` and from there in the Audit Log.
        """
        model_string = resolve_model(self._config, task)
        if model_string is None:
            key = TASK_MODEL_KEYS.get(task, task)
            raise AiNotConfiguredError(
                f"task {task!r} has no model configured - set provider.models.{key}"
            )

        command = self._build_command(model_string, prompt)
        stdout, _duration = self._run(command)
        text, input_tokens, output_tokens = self._parse_output(stdout)

        parts = model_string.split("/", 1)
        provider_name, model_name = parts if len(parts) == 2 else ("", parts[0])
        result = AiResult(
            text=text,
            provider=provider_name,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=_duration,
        )

        limit = token_limit(self._config, task)
        if limit is not None and result.total_tokens is not None and result.total_tokens > limit:
            result = AiResult(
                text=result.text,
                provider=result.provider,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_seconds=result.duration_seconds,
                over_token_limit=True,
            )
        return result

    def _supported_flags(self) -> frozenset[str]:
        """Which of the optional flags this OpenCode version accepts, read
        once from ``opencode run --help`` (OC-7). A version that does not
        know a flag prints its usage text and exits 1 instead of running -
        seen on the target machine on 8 September 2026, where ``--auto``
        was not in the list - so a flag is passed only when the installed
        binary lists it. If the probe itself fails, no optional flag is
        passed and the run proceeds on the flags every version has."""
        if self._supported is None:
            try:
                probe = subprocess.run(
                    [self._binary, "run", "--help"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60,
                )
                help_text = (probe.stdout or "") + (probe.stderr or "")
            except (OSError, subprocess.TimeoutExpired):
                help_text = ""
            self._supported = frozenset(flag for flag in _OPTIONAL_FLAGS if flag in help_text)
        return self._supported

    def _build_command(self, model_string: str, prompt: str) -> list[str]:
        command = [self._binary, "run", "--format", "json", "--model", model_string]
        supported = self._supported_flags()
        if self._cwd is not None and "--dir" in supported:
            command += ["--dir", str(self._cwd)]
        # Auto-approval defaults to True: a headless run that stops to ask
        # for permission would hang (OC-6). Spec 3.8 records why that is
        # safe here - the MCP surface exposes nothing above action class B,
        # and outlook.save_draft / outlook.send are not registered at all,
        # only propose_* variants that change nothing. This default is
        # configuration for this tool surface, not a licence to extend the
        # same treatment to a future tool of class C or above. The flag is
        # only passed when the installed version accepts it (OC-7).
        auto_approve = _config_key(self._config, "provider.opencode.auto_approve", True)
        if auto_approve and "--auto" in supported:
            command += ["--auto"]
        command.append(prompt)
        if sys.platform == "win32":
            length = sum(len(part) + 3 for part in command)
            if length > _WINDOWS_COMMAND_LIMIT:
                raise OpenCodeError(
                    f"the prompt is too long to pass to opencode on Windows ({length:,} characters; "
                    f"the limit is about {_WINDOWS_COMMAND_LIMIT:,}). Lower the knowledge token budget "
                    "in Options."
                )
        return command

    def _run(self, command: list[str]) -> tuple[str, float]:
        started = time.monotonic()
        try:
            # OpenCode writes UTF-8; without saying so, Windows decodes it as
            # cp1252 and a German umlaut in a member's answer becomes mojibake
            # or, for some byte values, a UnicodeDecodeError that takes the
            # run down.
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise OpenCodeError(
                f"{self._binary!r} is not on PATH - OpenCode must be installed on this "
                "machine (OP-4)"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenCodeError(
                f"opencode run did not finish within {self._timeout_seconds} seconds"
            ) from exc
        duration = time.monotonic() - started

        if result.returncode != 0:
            raise OpenCodeError(
                f"opencode run exited with code {result.returncode}: "
                + describe_failure(result.stdout, result.stderr)
            )
        return result.stdout, duration

    def _parse_output(self, stdout: str) -> tuple[str, int | None, int | None]:
        text_parts: list[str] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        saw_text = False

        for line_number, line in enumerate(stdout.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                snippet = line if len(line) <= _LINE_TRIM else line[:_LINE_TRIM] + "..."
                raise OpenCodeError(
                    f"opencode run produced invalid JSON on line {line_number}: {snippet}"
                ) from exc

            event_type = event.get("type")
            if event_type == "text":
                saw_text = True
                text_parts.append(event.get("part", {}).get("text", ""))
            elif event_type == "step_finish":
                tokens = event.get("part", {}).get("tokens", {})
                step_input = tokens.get("input")
                step_output = tokens.get("output")
                if step_input is not None:
                    input_tokens = (input_tokens or 0) + step_input
                if step_output is not None:
                    output_tokens = (output_tokens or 0) + step_output
            # every other type is ignored entirely (OC-3)

        if not saw_text:
            raise OpenCodeError("opencode run produced no text event - no answer to return")

        return "".join(text_parts), input_tokens, output_tokens
