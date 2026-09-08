#!/usr/bin/env python3
"""CLI entry point for the AI Board (decision 0005).

    decisionboard board

Loads the local configuration and runs the board on a topic entered at the
prompt (``board.py``, docs/spec.md chapter 9): six isolated member calls,
then one synthesis call (FR-3.3a) - it cannot degrade to a partial answer
without a model and refuses clearly instead (AI-4, K-7).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict:
    if not path.exists():
        print(f"No configuration found at {path}.", file=sys.stderr)
        raise SystemExit(2)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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
    try:
        result = run_board(
            config, provider, topic=topic, context=context,
            options=options, constraints=constraints,
        )
    except AiNotConfiguredError as exc:
        print(f"Cannot run the AI Board: {exc}", file=sys.stderr)
        return 1

    print()
    print(render(result))
    if result.failed_members:
        print(f"\nFailed member(s): {len(result.failed_members)}.")

    # The owner decided the conversation lives in the synthesis only (six
    # members are not polled again) and ends when he stops answering.
    conversation = BoardConversation(result=result, turns=[])
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


COMMANDS: dict[str, Callable[[dict], int]] = {
    "board": cmd_board,
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
    subparsers.add_parser("board", help="AI Board")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    return COMMANDS[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
