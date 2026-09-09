#!/usr/bin/env python3
"""CLI entry point for the AI Board (decision 0005).

    decisionboard board
    decisionboard serve [--port N] [--no-browser]

``board`` loads the local configuration and runs the board on a topic
entered at the prompt (``board.py``, docs/spec.md chapter 9): isolated
member calls, then one synthesis call (FR-3.3a) - it cannot degrade to a
partial answer without a model and refuses clearly instead (AI-4, K-7).

``serve`` starts the local browser interface (``server.py``, spec section
10) on 127.0.0.1 and opens it in the default browser. Both front ends call
the same ``run_board``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict:
    """The local configuration, or a clear instruction to create it.

    A fresh clone has no ``config.local.json`` - it is git-ignored, because
    it holds machine paths and, in a company setup, the endpoint that was
    approved. Every entry point therefore has to name the one command that
    writes it, rather than reporting a missing file (decided 9 September
    2026, after a fresh clone left a user with nothing to go on)."""
    if not path.exists():
        script = "python scripts\\setup.py" if sys.platform == "win32" else "python3 scripts/setup.py"
        print(
            f"No configuration yet at {path}.\n"
            f"\n"
            f"Run the setup wizard once to create it:\n"
            f"    {script}\n"
            f"\n"
            f"It checks this machine, asks where your model and your notes are, and writes\n"
            f"the configuration for you. Running it again later keeps what is already set.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        print(f"{path} is not valid JSON ({exc}). Fix it, or delete it and run the setup wizard again.",
              file=sys.stderr)
        raise SystemExit(2) from None
    except OSError as exc:
        print(f"{path} could not be read ({exc}).", file=sys.stderr)
        raise SystemExit(2) from None
    if not isinstance(data, dict):
        print(f"{path} must hold a JSON object. Delete it and run the setup wizard again.", file=sys.stderr)
        raise SystemExit(2)
    return data


def _get(config: dict, dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node not in ("", None) else default


def cmd_board(config: dict) -> int:
    print("Decision Board")

    from .agent.opencode_client import OpenCodeError
    from .agent.provider import AiNotConfiguredError, build_provider
    from .board import BoardConversation, ask_follow_up, render, render_follow_up, run_board
    from .roles import load_board

    topic = input("Topic: ").strip()
    context = input("Context: ").strip()
    print("Options under consideration - one per line, blank line to finish:")
    options = []
    while True:
        line = input().strip()
        if not line:
            break
        options.append(line)
    print("Hard constraints - one per line, blank line to finish:")
    constraints = []
    while True:
        line = input().strip()
        if not line:
            break
        constraints.append(line)

    provider = build_provider(config)
    from .roles import RolesUnavailable
    try:
        board = load_board(config)   # section 3.4: fresh on every run, this is the board
    except RolesUnavailable as exc:
        print(f"Cannot run the AI Board: {exc}", file=sys.stderr)
        return 1
    profiles = board.profiles
    print(f"Board of {len(profiles)}: {', '.join(profiles)}  (roles from {board.folder})")
    if board.skipped:
        print("Not on the board: " + "; ".join(f"{m} ({r})" for m, r in board.skipped))
    try:
        result = run_board(
            config, provider, topic=topic, context=context,
            options=options, constraints=constraints, board=board,
        )
    except AiNotConfiguredError as exc:
        print(f"Cannot run the AI Board: {exc}", file=sys.stderr)
        return 1

    print()
    print(render(result))
    if result.failed_members:
        print(f"\nFailed member(s): {len(result.failed_members)}.")

    # On the command line a follow-up is the one-call form: the synthesis
    # over the original assessments (the browser can ask members again).
    conversation = BoardConversation(result=result, turns=[], roles=profiles)
    print(
        f"\nFollow-up question - blank line ends the conversation. The first "
        f"round cost {result.llm_calls} model call(s); each follow-up costs one."
    )
    while True:
        question = input("Follow-up: ").strip()
        if not question:
            break
        try:
            answer = ask_follow_up(config, provider, conversation, question)
        except (AiNotConfiguredError, OpenCodeError, ValueError) as exc:
            print(f"  Could not answer: {exc}")
            continue
        print()
        print(render_follow_up(question, answer))

    print(f"\nLLM call(s): {result.llm_calls + conversation.llm_calls}.")
    return 0


def cmd_serve(config: dict, args: argparse.Namespace) -> int:
    from .server import serve

    return serve(config, args.config, port=args.port, open_browser=not args.no_browser)


COMMANDS: dict[str, Callable[..., int]] = {
    "board": cmd_board,        # "serve" takes the parsed arguments and is dispatched in main()
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decisionboard",
        description="Decision Board - standalone AI Board tool (decision 0005).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config" / "config.local.json",
        help="path to the local configuration (default: config/config.local.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("board", help="AI Board on the command line")
    serve_parser = subparsers.add_parser("serve", help="AI Board in the browser (local only)")
    serve_parser.add_argument("--port", type=int, default=None,
                              help="port on 127.0.0.1 (default: server.port from the config, else 8765)")
    serve_parser.add_argument("--no-browser", action="store_true",
                              help="do not open the default browser after starting")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "serve":
        if args.port is None:
            args.port = int(_get(config, "server.port", 8765))
        return cmd_serve(config, args)
    return COMMANDS[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
