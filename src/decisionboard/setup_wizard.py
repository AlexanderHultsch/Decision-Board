"""The setup wizard: ``python scripts/setup.py``.

Checks the machine, writes ``config/config.local.json`` with everything
preselected, lets Alex pick the knowledge source with the native folder
dialog, and makes one real test call through OpenCode so that "is the
model reachable" is answered before the board is ever asked anything.

Every check prints what it ran and what came back. A failed test call
shows OpenCode's complete stdout and stderr, because the board's own error
message is a summary and the first real failure on a new machine needs the
whole thing.

Standard library only. Runs on Windows, macOS and Linux; the folder dialog
needs tkinter, which ships with Python on Windows, and falls back to a
typed path everywhere else.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
EXAMPLE_CONFIG = CONFIG_DIR / "config.example.json"
LOCAL_CONFIG = CONFIG_DIR / "config.local.json"
DEFAULT_MODEL = "azure/Opencode-Kimi-K2.7"
TEST_PROMPT = "Reply with the single word OK and nothing else."

OK = "  [ok]  "
WARN = "  [!!]  "
FAIL = "  [XX]  "


class Wizard:
    def __init__(self, *, interactive: bool, vault: str | None, model: str | None,
                 run_test: bool, out=None, opencode_config: str | None = None) -> None:
        self.interactive = interactive
        self.vault_arg = vault
        self.model_arg = model
        self.opencode_config_arg = opencode_config
        self.env: dict[str, str] = dict(os.environ)
        self.run_test = run_test
        self.out = out or sys.stdout
        self.problems: list[str] = []
        self.failures: list[str] = []
        self.opencode = shutil.which("opencode")

    # -- output ------------------------------------------------------------

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def ok(self, text: str) -> None:
        self.say(OK + text)

    def warn(self, text: str) -> None:
        self.say(WARN + text)
        self.problems.append(text)

    def fail(self, text: str) -> None:
        self.say(FAIL + text)
        self.problems.append(text)
        self.failures.append(text)

    def ask(self, prompt: str, default: str = "") -> str:
        if not self.interactive:
            return default
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            return default
        return answer or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        answer = self.ask(f"{prompt} (y/n)", "y" if default else "n").lower()
        return answer.startswith("y")

    # -- steps -------------------------------------------------------------

    def step_environment(self) -> None:
        self.say("1. Environment")
        self.say(f"        repository  {REPO_ROOT}")
        self.say(f"        platform    {platform.system()} {platform.release()}, Python {platform.python_version()}")
        if sys.version_info < (3, 11):
            self.fail("Python 3.11 or newer is required.")
        else:
            self.ok("Python version is sufficient.")
        git = shutil.which("git")
        if git:
            self.ok(f"git found: {git}")
        else:
            self.warn("git is not on PATH. Updates need `git pull`; install Git from https://git-scm.com")
        try:
            import tkinter  # noqa: F401
            self.ok("tkinter available: the folder dialog will open.")
        except Exception:
            self.warn("tkinter is not available: paths are typed instead of picked.")

    def step_opencode(self) -> dict:
        self.say("\n2. OpenCode")
        info = {"found": False, "version": "", "flags": [], "models": ""}
        if not self.opencode:
            self.fail("opencode is not on PATH. Install it (see https://opencode.ai/docs) and open a new terminal.")
            return info
        info["found"] = True
        self.ok(f"opencode found: {self.opencode}")
        version = self._run([self.opencode, "--version"], timeout=30)
        info["version"] = (version.stdout or version.stderr or "").strip().splitlines()[0] if (version.stdout or version.stderr) else ""
        if version.returncode == 0:
            self.ok(f"version: {info['version'] or '(printed nothing)'}")
        else:
            self.warn(f"`opencode --version` exited {version.returncode}: {(version.stderr or version.stdout).strip()[:300]}")
        helptext = self._run([self.opencode, "run", "--help"], timeout=30)
        text = (helptext.stdout or "") + (helptext.stderr or "")
        info["flags"] = [flag for flag in ("--auto", "--dir", "--format", "--model") if flag in text]
        self.ok(f"`opencode run` flags this version knows of the ones we use: {', '.join(info['flags']) or 'none'}")
        auth = self._run([self.opencode, "auth", "list"], timeout=30)
        auth_text = ((auth.stdout or "") + (auth.stderr or "")).strip()
        if auth.returncode == 0 and auth_text:
            self.say("        logins (`opencode auth list`):")
            for line in auth_text.splitlines()[:10]:
                self.say(f"          {line}")
            if "0 credentials" in auth_text:
                self.warn("OpenCode has no stored credentials. If the model needs a login, run `opencode auth login`.")
        else:
            self.warn("`opencode auth list` printed nothing - if the test call fails, run `opencode auth login`.")
        models = self._run([self.opencode, "models"], timeout=60)
        models_text = (models.stdout or "").strip()
        if models.returncode == 0 and models_text:
            lines = models_text.splitlines()
            info["models"] = models_text
            self.say(f"        {len(lines)} model string(s) available (`opencode models`), first few:")
            for line in lines[:8]:
                self.say(f"          {line}")
        else:
            self.warn("`opencode models` printed nothing - the model string is not verified before the test call.")
        return info

    def step_config(self, info: dict) -> dict:
        self.say("\n3. Configuration")
        if LOCAL_CONFIG.exists():
            config = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
            self.ok(f"existing {LOCAL_CONFIG.name} kept; values below are updated in place.")
        else:
            config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            self.ok(f"created from {EXAMPLE_CONFIG.name}.")
        config.setdefault("storage", {})["pc_name"] = os.environ.get("COMPUTERNAME") or platform.node()
        runtime = config.setdefault("runtime", {})
        if not runtime.get("audit_folder") or "<you>" in str(runtime.get("audit_folder")):
            runtime["audit_folder"] = str(REPO_ROOT / "audit")
        knowledge = config.setdefault("knowledge", {})
        knowledge.setdefault("token_budget", 6000)
        config.setdefault("server", {}).setdefault("port", 8765)
        config.setdefault("ui", {}).setdefault("theme", "system")
        provider = config.setdefault("provider", {})
        provider.setdefault("opencode", {}).setdefault("auto_approve", True)
        provider.setdefault("token_limits", {}).setdefault("board", None)

        opencode_cfg = provider.setdefault("opencode", {})
        config_file = self.step_opencode_config(opencode_cfg.get("config_file") or "")
        opencode_cfg["config_file"] = config_file
        defined = self.describe_opencode_config(config_file) if config_file else []
        if config_file:
            self.env["OPENCODE_CONFIG"] = config_file
            models_now = self._run([self.opencode, "models"], timeout=60) if self.opencode else None
            if models_now is not None and models_now.returncode == 0 and (models_now.stdout or "").strip():
                info["models"] = models_now.stdout.strip()
                self.ok(f"`opencode models` with that file: {len(info['models'].splitlines())} model string(s)")

        current_model = provider.setdefault("models", {}).get("board") or (defined[0] if defined else DEFAULT_MODEL)
        if "<" in current_model or "big-pickle" in current_model and defined:
            current_model = defined[0]
        model = self.model_arg or self.ask("Model string (provider/model)", current_model)
        if defined and model not in defined:
            self.warn(f"{model} is not defined in {Path(config_file).name} (defined: {', '.join(defined)}).")
        elif info.get("models") and model not in info["models"]:
            self.warn(f"{model} is not in `opencode models` output - the test call will tell.")
        provider["models"]["board"] = model
        if "/" in model and config_file:
            provider["endpoint"] = f"{model.split('/')[0]} (company gateway, see opencode.config_file)"

        vault = self.vault_arg
        current_vault = knowledge.get("vault_path") or ""
        if "<you" in current_vault or "<your" in current_vault:
            current_vault = ""
        if vault is None:
            if self.interactive and self.confirm("Choose the knowledge source (Obsidian vault folder) now?", True):
                vault = self.pick_folder(current_vault) or self.ask("Vault folder path", current_vault)
            else:
                vault = current_vault
        knowledge["vault_path"] = vault or ""
        self.check_vault(vault)

        LOCAL_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.ok(f"written: {LOCAL_CONFIG}")
        self.say(f"        model {model}, budget {knowledge['token_budget']} tokens, port {config['server']['port']}, "
                 f"audit {runtime['audit_folder']}")
        return config

    def step_opencode_config(self, current: str) -> str:
        """Where the company-provided ``opencode.json`` lives, so OpenCode
        finds its provider definition from any working directory."""
        if self.opencode_config_arg is not None:
            candidate = self.opencode_config_arg
        else:
            if "<you>" in current:
                current = ""
            found = current or self.find_opencode_config()
            if self.interactive:
                if self.confirm(f"Use a company opencode.json? {'(found: ' + found + ')' if found else ''}", bool(found)):
                    candidate = self.pick_file(found) or self.ask("Path to opencode.json", found)
                else:
                    candidate = ""
            else:
                candidate = found
        candidate = str(Path(candidate).expanduser()) if candidate else ""
        if candidate and not Path(candidate).is_file():
            self.fail(f"opencode.json not found: {candidate}")
            return ""
        if candidate:
            self.ok(f"OpenCode configuration: {candidate}")
        else:
            self.say("        no company opencode.json - OpenCode uses its own global configuration.")
        return candidate

    def find_opencode_config(self) -> str:
        """Likely locations: OPENCODE_CONFIG, OpenCode's global file, and an
        'Opencode' folder next to the repository's parent."""
        candidates = [os.environ.get("OPENCODE_CONFIG", "")]
        candidates.append(str(Path.home() / ".config" / "opencode" / "opencode.json"))
        for parent in (REPO_ROOT.parent, REPO_ROOT.parent.parent):
            for name in ("Opencode", "OpenCode", "opencode", "Opnecode"):
                candidates.append(str(parent / name / "opencode.json"))
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return ""

    def describe_opencode_config(self, path: str) -> list[str]:
        """The provider/model strings the file defines, printed with the
        gateway they point at, plus what the gateway itself lists (a chance
        to see a stronger model behind the same approved endpoint)."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.fail(f"opencode.json could not be read: {exc}")
            return []
        defined: list[str] = []
        for provider_id, provider in (data.get("provider") or {}).items():
            if not isinstance(provider, dict):
                continue
            options = provider.get("options") or {}
            base_url = str(options.get("baseURL") or "")
            self.say(f"        provider '{provider_id}' -> {base_url or '(no baseURL)'}"
                     + ("  [plain http: only acceptable inside the company network]" if base_url.startswith("http://") else ""))
            for model_id in (provider.get("models") or {}):
                defined.append(f"{provider_id}/{model_id}")
                self.say(f"          model string: {provider_id}/{model_id}")
            api_key = _resolve_env_placeholders(str(options.get("apiKey") or ""))
            if base_url and api_key and api_key.lower() not in ("xxx", "<key>"):
                for line in self.gateway_models(base_url, api_key):
                    self.say(f"          {line}")
            elif base_url:
                self.warn(f"provider '{provider_id}': apiKey is a placeholder - put the real key in the file "
                          "(or use \"apiKey\": \"{env:LITELLM_API_KEY}\" and set that variable).")
        default_model = data.get("model")
        if default_model:
            self.say(f"        default model in the file: {default_model}")
        return defined

    def gateway_models(self, base_url: str, api_key: str) -> list[str]:
        """``GET <baseURL>/models`` on the gateway: every model the company
        endpoint serves, not only the one the file defines."""
        url = base_url.rstrip("/") + "/models"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            return [f"gateway model list not reachable ({exc}) - only the model(s) defined above are known."]
        ids = sorted(str(item.get("id")) for item in (payload.get("data") or []) if isinstance(item, dict) and item.get("id"))
        if not ids:
            return ["gateway lists no models for this key."]
        lines = [f"gateway serves {len(ids)} model(s); add any of them under 'models' in opencode.json to use it:"]
        lines.extend(f"  - {model_id}" for model_id in ids[:40])
        if len(ids) > 40:
            lines.append(f"  - ... and {len(ids) - 40} more")
        return lines

    def pick_file(self, initial: str) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return None
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title="Choose the OpenCode configuration (opencode.json)",
                                          initialdir=str(Path(initial).parent) if initial else None,
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        root.destroy()
        return path or None

    def check_vault(self, vault: str | None) -> None:
        if not vault:
            self.warn("no knowledge source set - the board answers from the question alone until you set one in Options.")
            return
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from decisionboard.knowledge import KnowledgeUnavailable, load_vault
        try:
            notes = load_vault(vault)
        except KnowledgeUnavailable as exc:
            self.fail(str(exc))
            return
        self.ok(f"knowledge source: {vault} ({len(notes)} notes)")

    def pick_folder(self, initial: str) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return None
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Choose the knowledge source (Obsidian vault)",
                                       initialdir=initial or None, mustexist=True)
        root.destroy()
        return path or None

    def step_test_call(self, config: dict, info: dict) -> None:
        self.say("\n4. Test call through OpenCode")
        if not self.run_test:
            self.say("        skipped (--no-test).")
            return
        if not info.get("found"):
            self.fail("skipped: opencode not found.")
            return
        model = config["provider"]["models"]["board"]
        command = [self.opencode, "run", "--format", "json", "--model", model]
        if self.env.get("OPENCODE_CONFIG"):
            self.say(f"        OPENCODE_CONFIG={self.env['OPENCODE_CONFIG']}")
        if config["provider"]["opencode"].get("auto_approve") and "--auto" in info.get("flags", []):
            command.append("--auto")
        command.append(TEST_PROMPT)
        self.say("        " + " ".join(f'"{c}"' if " " in c else c for c in command))
        started = time.monotonic()
        result = self._run(command, timeout=180)
        duration = time.monotonic() - started
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from decisionboard.agent.opencode_client import describe_failure
        if result.returncode != 0:
            self.fail(f"exited {result.returncode} after {duration:.1f}s: {describe_failure(result.stdout, result.stderr)}")
            self._dump("stdout", result.stdout)
            self._dump("stderr", result.stderr)
            for line in diagnose(result.stdout, result.stderr):
                self.say(f"        {line}")
            return
        text_parts, tokens_in, tokens_out = [], 0, 0
        for line in (result.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text":
                text_parts.append(event.get("part", {}).get("text", ""))
            elif event.get("type") == "step_finish":
                tokens = event.get("part", {}).get("tokens", {})
                tokens_in += tokens.get("input") or 0
                tokens_out += tokens.get("output") or 0
        answer = "".join(text_parts).strip()
        if not answer:
            self.fail("exited 0 but produced no text event - the board would refuse this (OC-5).")
            self._dump("stdout", result.stdout)
            return
        self.ok(f"model answered in {duration:.1f}s: {answer[:80]!r}  ({tokens_in} in / {tokens_out} out tokens)")

    def _dump(self, name: str, text: str | None) -> None:
        text = (text or "").strip()
        self.say(f"        --- {name} {'(empty)' if not text else ''}")
        for line in text.splitlines()[:40]:
            self.say(f"        {line[:300]}")

    def _run(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, env=self.env)
        except FileNotFoundError:
            return subprocess.CompletedProcess(command, 127, "", f"{command[0]} not found")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 124, "", f"timed out after {timeout}s")

    # -- run ----------------------------------------------------------------

    def run(self) -> int:
        self.say("Decision Board setup")
        self.say("====================")
        self.step_environment()
        info = self.step_opencode()
        config = self.step_config(info)
        self.step_test_call(config, info)
        self.say("\n5. Summary")
        if self.problems:
            for problem in self.problems:
                self.say(f"        - {problem}")
            self.say("\n        Fix the items above, then run this script again.")
        else:
            self.ok("everything checked out.")
        self.say("\n        Start the board:   python scripts/run_board.py serve")
        if self.interactive and not self.problems and self.confirm("Start the board now?", True):
            from decisionboard.server import serve
            return serve(config, LOCAL_CONFIG, port=int(config["server"]["port"]))
        return 1 if self.failures else 0


def _resolve_env_placeholders(value: str) -> str:
    """OpenCode's ``{env:NAME}`` substitution, so the wizard can use the
    same key the file resolves at runtime."""
    return re.sub(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), value)


def diagnose(stdout: str | None, stderr: str | None) -> list[str]:
    """What a failed test call most likely means, from signatures seen on
    real machines. Falls back to the generic list."""
    text = (stdout or "") + (stderr or "")
    if "SQLiteError" in text or "migration" in text.lower():
        return [
            "This is OpenCode's own local database, not the board: its schema does not match the",
            "installed OpenCode version (seen 8 September 2026 on 1.17.7 as 'no such column: replacement_seq').",
            "Fix: run `opencode upgrade` to the current version and repeat this script. If it still fails,",
            "close OpenCode, rename %USERPROFILE%\\.local\\share\\opencode\\opencode.db to opencode.db.bak",
            "(sessions history only; auth.json keeps the logins) and repeat.",
        ]
    if "auth" in text.lower() or "api key" in text.lower() or "unauthorized" in text.lower() or "401" in text:
        return ["Not logged in for this provider: run `opencode auth login`, pick the provider, then repeat."]
    if "model" in text.lower() and ("not found" in text.lower() or "unknown" in text.lower()):
        return ["The model string is not one this OpenCode can use: run `opencode models` and pick one of those."]
    return [
        "Likely causes: not logged in (`opencode auth login`), a model string this account cannot use",
        "(`opencode models`), or no network to the provider.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decision Board setup: check the machine, write the config, test the model.")
    parser.add_argument("--vault", help="knowledge source folder (skips the dialog)")
    parser.add_argument("--model", help="provider/model string (skips the question)")
    parser.add_argument("--opencode-config", help="path to the company opencode.json ('' for none; skips the dialog)")
    parser.add_argument("--no-test", action="store_true", help="do not make the test call")
    parser.add_argument("--yes", action="store_true", help="no questions: take defaults and arguments")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    wizard = Wizard(interactive=not args.yes, vault=args.vault, model=args.model, run_test=not args.no_test,
                    opencode_config=args.opencode_config)
    return wizard.run()
