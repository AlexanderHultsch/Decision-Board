#!/usr/bin/env python3
"""CLI entry point for the AI Board (decision 0005).

    programmind board
    programmind serve [--port N] [--no-browser]

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

    from programmind.ai.opencode_client import OpenCodeError
    from programmind.ai.provider import AiNotConfiguredError, build_provider
    from programmind.agents.board.board import BoardConversation, ask_follow_up, render, render_follow_up, run_board
    from programmind.agents.board.roles import load_board

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
    from programmind.agents.board.roles import RolesUnavailable
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


def cmd_enrich(config: dict, args: argparse.Namespace) -> int:
    """``phases`` and ``aliases`` on the task pages, a ``summary`` on every
    page (spec 5.1). Proposes first; writes only with --write after a yes."""
    from datetime import date
    from programmind.knowledge import enrich
    from programmind.knowledge import knowledge
    from programmind.ai.provider import build_provider

    vault_path = _get(config, "knowledge.vault_path")
    if not vault_path:
        print("No knowledge source configured (knowledge.vault_path).", file=sys.stderr)
        return 1
    vault = Path(str(vault_path)).expanduser()
    try:
        notes = knowledge.load_vault(vault)
    except knowledge.KnowledgeUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    provider = None if args.no_summaries else build_provider(config)
    proposal = enrich.propose(notes, provider, summaries=not args.no_summaries, refresh=args.refresh)
    print(enrich.describe(proposal))
    if not proposal.changes:
        return 0
    if not args.write:
        print(f"\n{len(proposal.changes)} change(s) proposed. Run again with --write to write them.")
        return 0
    if not args.yes:
        answer = input(f"Write these {len(proposal.changes)} change(s) into the vault? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing written.")
            return 0
    written = enrich.apply(vault, proposal, date.today().isoformat())
    print(f"Written: {len(written)} page(s).")
    return 0


def cmd_eval_knowledge(config: dict, args: argparse.Namespace) -> int:
    """Hit rate of the knowledge selection over the evaluation set (spec 5.1)."""
    from programmind.knowledge import evaluate
    from programmind.ai.provider import build_provider

    path = Path(args.file) if args.file else evaluate.DEFAULT_SET
    try:
        questions = evaluate.load_set(path)
    except (OSError, ValueError) as exc:
        print(f"Cannot read the evaluation set {path}: {exc}", file=sys.stderr)
        return 1
    provider = build_provider(config) if args.selection == "ai" else None
    try:
        outcomes = evaluate.evaluate(config, questions, selection=args.selection, provider=provider, budget=args.budget)
    except Exception as exc:    # noqa: BLE001 - a report, not a run
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(evaluate.report(outcomes, args.selection))
    return 0


def cmd_serve(config: dict, args: argparse.Namespace) -> int:
    from programmind.shell.server import serve

    return serve(config, args.config, port=args.port, open_browser=not args.no_browser)


def cmd_probe_stream(config: dict) -> int:
    """Does ``opencode run --format json`` print the model's text as it
    arrives, or only when it is done? Decided 11 September 2026 (a live
    answer on the page needs the first): the same command, environment
    and model as every real call, a counting prompt, and one line per
    event as it comes in, with the seconds since the start."""
    import subprocess
    import time

    from programmind.ai.opencode_client import OpenCodeProvider, opencode_environment
    from programmind.ai.provider import TASK_BOARD, resolve_model

    model = resolve_model(config, TASK_BOARD)
    if not model:
        print("No model configured (provider.models.board).", file=sys.stderr)
        return 2
    provider = OpenCodeProvider(config)
    command = provider._build_command(model)
    prompt = "Count from 1 to 40, one number per line, and write one short sentence about the weather after every ten numbers."
    print("command:", " ".join(command[:-1]), "(the prompt on standard input)")
    started = time.monotonic()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=opencode_environment(config))
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(prompt)
    proc.stdin.close()
    text_events: list[float] = []
    for line in proc.stdout:
        at = time.monotonic() - started
        line = line.rstrip("\n")
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"{at:6.1f}s  {line[:160]}")
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        kind = str(event.get("type", "?"))
        text = part.get("text") if isinstance(part.get("text"), str) else ""
        if kind == "text" or part.get("type") == "text":
            text_events.append(at)
            print(f"{at:6.1f}s  text  {len(text):5d} chars  {text[:70]!r}")
        else:
            print(f"{at:6.1f}s  {kind}")
    proc.wait()
    total = time.monotonic() - started
    print()
    if not text_events:
        print(f"No text event in {total:.1f}s: the run failed; see the lines above.")
        return 1
    spread = text_events[-1] - text_events[0]
    if len(text_events) > 1 and spread > 1.0:
        print(f"{len(text_events)} text events over {spread:.1f}s of a {total:.1f}s run: JSON mode streams. A live answer is possible.")
    else:
        print(f"{len(text_events)} text event(s), the first at {text_events[0]:.1f}s of a {total:.1f}s run: "
              "JSON mode prints the text when it is done. A live answer needs OpenCode's server mode.")
    return 0


COMMANDS: dict[str, Callable[..., int]] = {
    "board": cmd_board,        # "serve" takes the parsed arguments and is dispatched in main()
    "probe-stream": cmd_probe_stream,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="programmind",
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
    enrich_parser = subparsers.add_parser("enrich", help="phases, aliases and AI summaries on the vault pages (spec 5.1)")
    enrich_parser.add_argument("--write", action="store_true", help="write the proposals (after a yes)")
    enrich_parser.add_argument("--yes", action="store_true", help="do not ask before writing")
    enrich_parser.add_argument("--no-summaries", action="store_true", help="phases and aliases only, no model call")
    enrich_parser.add_argument("--refresh", action="store_true", help="rewrite every summary, not only the missing ones")
    subparsers.add_parser("probe-stream", help="does opencode run print the model's text as it arrives? (one short call)")
    eval_parser = subparsers.add_parser("eval-knowledge", help="hit rate of the knowledge selection over the evaluation set")
    eval_parser.add_argument("--file", default=None, help="the question set (default: tests/knowledge_eval/questions.json)")
    eval_parser.add_argument("--selection", choices=("python", "ai"), default="python")
    eval_parser.add_argument("--budget", type=int, default=None, help="knowledge tokens per member (default: the configured budget)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "serve":
        if args.port is None:
            args.port = int(_get(config, "server.port", 8765))
        return cmd_serve(config, args)
    if args.command == "enrich":
        return cmd_enrich(config, args)
    if args.command == "eval-knowledge":
        return cmd_eval_knowledge(config, args)
    return COMMANDS[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
