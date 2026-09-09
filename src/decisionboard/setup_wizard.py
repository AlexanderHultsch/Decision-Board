"""The setup wizard: ``python scripts/setup.py``.

Checks the machine, writes ``config/config.local.json`` with everything
preselected, lets the user pick the knowledge source with the native folder
dialog, and makes two real test calls through OpenCode so that "is the model
reachable" is answered before the board is ever asked anything.

It is written for any machine, not one: a **company** setup points OpenCode
at an IT-provided ``opencode.json`` (an internal gateway, no data leaving
approved infrastructure), a **private** setup logs in to a provider with
OpenCode's own ``auth login``. Everything after that step - knowledge source,
model choice, test calls - is the same in both. Windows, macOS and Linux are
all supported; the folder dialog needs tkinter and falls back to a typed
path.

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
DEFAULT_MODEL = ""
TEST_PROMPT = "Reply with the single word OK and nothing else."

OK = "  [ok]  "
WARN = "  [!!]  "
FAIL = "  [XX]  "


class Wizard:
    def __init__(self, *, interactive: bool, vault: str | None, model: str | None,
                 run_test: bool, out=None, opencode_config: str | None = None,
                 profile: str | None = None, roles: str | None = None) -> None:
        self.interactive = interactive
        self.vault_arg = vault
        self.model_arg = model
        self.opencode_config_arg = opencode_config
        self.roles_arg = roles
        self.roles_folder = ""
        self.profile_arg = profile
        self.profile = profile or ""
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
            self.say("        no tkinter: the folder dialog is unavailable, paths are typed instead.")

    def step_profile(self, config_seed: dict | None = None) -> str:
        """Company or private. The one question that changes what follows:
        where the model lives and who may see the prompts."""
        self.say("\n2. How will you use Decision Board?")
        if self.profile_arg:
            self.profile = self.profile_arg
            self.ok(f"profile: {self.profile} (given on the command line)")
            return self.profile
        if self.opencode_config_arg:
            # A company file named on the command line settles the question.
            self.profile = "company"
            self.ok(f"profile: company (an opencode.json was given: {self.opencode_config_arg})")
            return self.profile
        stored = (config_seed or {}).get("setup", {}).get("profile", "")
        found_company_file = self.find_opencode_config()
        default = stored or ("company" if found_company_file else "private")
        self.say("        1. Company or organisation - your IT provides an opencode.json that points")
        self.say("           OpenCode at an internal gateway. Prompts and notes stay on approved")
        self.say("           infrastructure. Nothing to log in to; the file carries the key.")
        self.say("        2. Private or own account - OpenCode logs in to a provider you choose")
        self.say("           (Anthropic, OpenAI, and others). Your question and the notes the board")
        self.say("           selects are sent to that provider, so use it only with content you may")
        self.say("           send there.")
        if found_company_file:
            self.say(f"        (a company opencode.json is already on this machine: {found_company_file})")
        if not self.interactive:
            self.profile = default
        else:
            while True:
                answer = self.ask("Choose 1 or 2", "1" if default == "company" else "2")
                if answer in ("1", "company"):
                    self.profile = "company"
                    break
                if answer in ("2", "private"):
                    self.profile = "private"
                    break
                self.say("        Type 1 or 2.")
        self.ok(f"profile: {self.profile}")
        if self.profile == "private":
            self.say("        Reminder: with a private account, everything the board sends leaves your")
            self.say("        machine. Point the knowledge source at a vault you may share with the provider.")
        return self.profile

    def step_login(self, info: dict) -> None:
        """Private setups only: make sure OpenCode has a provider login."""
        self.say("\n4. Provider login")
        if not info.get("found"):
            self.say("        skipped: opencode not found.")
            return
        if info.get("credentials"):
            self.ok("OpenCode has stored credentials - no login needed.")
            return
        self.say("        OpenCode has no stored credentials. `opencode auth login` opens a menu:")
        self.say("        pick your provider, then paste the API key (or complete the browser login).")
        if not self.interactive:
            self.warn("no provider login found - run `opencode auth login` before using the board.")
            return
        if not self.confirm("Run `opencode auth login` now?", True):
            self.warn("no provider login yet - run `opencode auth login` before using the board.")
            return
        try:
            # No capture: this is an interactive menu the user has to see.
            subprocess.run([self.opencode, "auth", "login"], env=self.env, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.fail(f"`opencode auth login` could not be run: {exc}")
            return
        auth = self._run([self.opencode, "auth", "list"], timeout=30)
        text = ((auth.stdout or "") + (auth.stderr or "")).strip()
        if "0 credentials" in text or not text:
            self.warn("still no credentials stored - the test call will show whether it works anyway.")
        else:
            self.ok("credentials stored.")
        listed = self._run([self.opencode, "models"], timeout=60)
        if listed.returncode == 0 and (listed.stdout or "").strip():
            info["models"] = listed.stdout.strip()
            self.ok(f"`opencode models` now lists {len(info['models'].splitlines())} model string(s).")

    def install_hint(self) -> list[str]:
        """How to install OpenCode on this machine."""
        if sys.platform == "win32":
            return ["Install OpenCode:  winget install opencode   (or: npm install -g opencode-ai)",
                    "then open a NEW terminal so PATH picks it up."]
        if sys.platform == "darwin":
            return ["Install OpenCode:  brew install sst/tap/opencode   (or: npm install -g opencode-ai)",
                    "then open a new terminal."]
        return ["Install OpenCode:  curl -fsSL https://opencode.ai/install | bash   (or: npm install -g opencode-ai)",
                "then open a new terminal."]

    def step_opencode(self) -> dict:
        self.say("\n3. OpenCode")
        info = {"found": False, "version": "", "flags": [], "models": ""}
        if not self.opencode:
            self.fail("opencode is not on PATH.")
            for line in self.install_hint():
                self.say(f"        {line}")
            return info
        info["found"] = True
        self.ok(f"opencode found: {self.opencode}")
        mine = str(Path(self.opencode).resolve()).lower()
        mine = str(Path(self.opencode).resolve()).lower()
        others = [p for p in _all_on_path("opencode") if str(Path(p).resolve()).lower() != mine]
        if others:
            # Listed, never started: the npm package ships a stub that
            # Windows refuses with a modal "Unsupported 16-Bit Application"
            # dialog (8 September 2026), and a setup script must not open
            # dialogs behind its own output.
            self.say("        other opencode(s) on PATH, not used (the first one wins): " + ", ".join(others))
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
        if self.profile == "company":
            info["credentials"] = False
            self.say("        logins: not checked - a company opencode.json carries its own key.")
        else:
            auth = self._run([self.opencode, "auth", "list"], timeout=30)
            auth_text = ((auth.stdout or "") + (auth.stderr or "")).strip()
            if auth.returncode == 0 and auth_text:
                self.say("        logins (`opencode auth list`):")
                for line in auth_text.splitlines()[:10]:
                    self.say(f"          {line}")
                info["credentials"] = "0 credentials" not in auth_text
                if not info["credentials"]:
                    self.say("        no stored credentials yet - the next step offers to log in.")
            else:
                info["credentials"] = False
                self.say("        `opencode auth list` printed nothing.")
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
        self.say("\n5. Configuration")
        if LOCAL_CONFIG.exists():
            try:
                config = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self.warn(f"{LOCAL_CONFIG.name} is not valid JSON ({exc}) - starting from the example instead; "
                          f"the old file is kept as {LOCAL_CONFIG.name}.bak")
                LOCAL_CONFIG.replace(LOCAL_CONFIG.with_suffix(LOCAL_CONFIG.suffix + ".bak"))
                config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            else:
                # Running the wizard again must never cost someone their
                # settings: everything already set is kept and only the
                # answers given now are changed, with a copy of the previous
                # file next to it.
                backup = LOCAL_CONFIG.with_suffix(LOCAL_CONFIG.suffix + ".bak")
                try:
                    backup.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                except OSError:
                    backup = None
                self.ok(f"existing {LOCAL_CONFIG.name} kept; only what you answer now changes."
                        + (f" Previous version saved as {backup.name}." if backup else ""))
        else:
            config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            self.ok(f"no configuration yet - creating {LOCAL_CONFIG.name} from {EXAMPLE_CONFIG.name}.")
        self.drop_stale_paths(config)
        config.setdefault("storage", {})["pc_name"] = os.environ.get("COMPUTERNAME") or platform.node()
        runtime = config.setdefault("runtime", {})
        audit = str(runtime.get("audit_folder") or "")
        # A folder that is gone, or whose parent is gone, was written on a
        # machine or in a place that no longer exists (folders get moved).
        if not audit or "<you>" in audit or not Path(audit).parent.is_dir():
            runtime["audit_folder"] = str(REPO_ROOT / "audit")
        knowledge = config.setdefault("knowledge", {})
        knowledge.setdefault("token_budget", 6000)
        config.setdefault("server", {}).setdefault("port", 8765)
        config.setdefault("ui", {}).setdefault("theme", "system")
        provider = config.setdefault("provider", {})
        provider.setdefault("opencode", {}).setdefault("auto_approve", True)
        provider.setdefault("token_limits", {}).setdefault("board", None)

        config.setdefault("setup", {})["profile"] = self.profile
        opencode_cfg = provider.setdefault("opencode", {})
        opencode_cfg.setdefault("extra_args", [])
        if self.profile == "company":
            config_file = self.step_opencode_config(opencode_cfg.get("config_file") or "")
        else:
            config_file = ""
            self.say("        private setup: OpenCode uses its own configuration and login.")
        opencode_cfg["config_file"] = config_file
        defined = self.describe_opencode_config(config_file) if config_file else []
        if config_file:
            self.env["OPENCODE_CONFIG"] = config_file
            models_now = self._run([self.opencode, "models"], timeout=60) if self.opencode else None
            if models_now is not None and models_now.returncode == 0 and (models_now.stdout or "").strip():
                info["models"] = models_now.stdout.strip()
                self.ok(f"`opencode models` with that file: {len(info['models'].splitlines())} model string(s)")

        current_model = provider.setdefault("models", {}).get("board") or ""
        if not _looks_like_model_string(current_model):
            current_model = defined[0] if defined else _first_listed_model(info.get("models", "")) or DEFAULT_MODEL
        model = self.model_arg or self.choose_model(current_model, defined, info.get("models", ""))
        if defined and model not in defined:
            self.warn(f"{model} is not defined in {Path(config_file).name} (defined: {', '.join(defined)}).")
        elif info.get("models") and model not in info["models"]:
            self.warn(f"{model} is not in `opencode models` output - the test call will tell.")
        if not model:
            self.fail("no model chosen - set provider.models.board before using the board.")
        provider["models"]["board"] = model
        if "/" in model:
            provider["endpoint"] = (f"{model.split('/')[0]} (company gateway, see opencode.config_file)"
                                    if config_file else f"{model.split('/')[0]} (private account via OpenCode)")

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
        if not vault:
            self.check_roles(None)
        if self.roles_folder:
            knowledge["roles_folder"] = self.roles_folder

        LOCAL_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.ok(f"written: {LOCAL_CONFIG}")
        self.say(f"        model {model}, budget {knowledge['token_budget']} tokens, port {config['server']['port']}, "
                 f"audit {runtime['audit_folder']}")
        return config

    def choose_model(self, current: str, defined: list[str], listed: str) -> str:
        """A numbered choice, never a bare text prompt right after yes/no
        questions: on the target machine a "y" typed here became the model
        string (8 September 2026). Enter keeps the default, a number picks
        from the list, and only text that looks like provider/model is
        accepted as a typed model string."""
        choices = list(defined)
        for line in (listed or "").splitlines():
            line = line.strip()
            if _looks_like_model_string(line) and line not in choices:
                choices.append(line)
        if current not in choices:
            choices.insert(0, current)
        if not self.interactive:
            return current
        self.say("        Models (from opencode.json first, then `opencode models`):")
        shown = choices[:15]
        for index, choice in enumerate(shown, start=1):
            marker = "  <- current" if choice == current else ""
            self.say(f"          {index:2d}. {choice}{marker}")
        if len(choices) > len(shown):
            self.say(f"              ... {len(choices) - len(shown)} more; type one as provider/model")
        while True:
            answer = self.ask("Model: Enter keeps the current one, or a number, or provider/model", "")
            if not answer:
                return current
            if answer.isdigit() and 1 <= int(answer) <= len(shown):
                return shown[int(answer) - 1]
            if _looks_like_model_string(answer):
                return answer
            self.say(f"        '{answer}' is not a model string (needs the form provider/model) - try again.")

    def drop_stale_paths(self, config: dict) -> None:
        """Configured paths that no longer exist are forgotten, not offered.

        Folders move (8 September 2026: the whole tree moved under an ``AI``
        folder), and a saved absolute path then points at nothing. Keeping it
        would make the wizard propose a dead path as the default and fail on
        it; dropping it makes the wizard ask again, which is what a moved
        folder needs."""
        checks = (
            ("knowledge.vault_path", "knowledge source", False),
            ("knowledge.roles_folder", "roles folder", False),
            ("provider.opencode.config_file", "OpenCode configuration", True),
        )
        for dotted, label, is_file in checks:
            value = _get(config, dotted)
            if not isinstance(value, str) or not value or "<you" in value:
                continue
            path = Path(value).expanduser()
            if path.is_file() if is_file else path.is_dir():
                continue
            self.say(f"        {label} no longer at {value} - forgetting it, you will be asked again.")
            _set(config, dotted, "")

    def step_opencode_config(self, current: str) -> str:
        """Where the company-provided ``opencode.json`` lives, so OpenCode
        finds its provider definition from any working directory. In a
        company setup the file is required; the only question is which."""
        if self.opencode_config_arg is not None:
            candidate = self.opencode_config_arg
        else:
            if "<you>" in current or (current and not Path(current).expanduser().is_file()):
                current = ""
            found = current or self.find_opencode_config()
            if not self.interactive:
                candidate = found
            elif found:
                self.say(f"        company opencode.json found: {found}")
                self.say("        1. use it     2. choose another with the file dialog     3. type a path")
                while True:
                    answer = self.ask("Choose 1, 2 or 3", "1")
                    if answer == "1":
                        candidate = found
                        break
                    if answer == "2":
                        candidate = self.pick_file(found) or ""
                        if candidate:
                            break
                        self.say("        no file chosen.")
                        continue
                    if answer == "3":
                        candidate = self.ask("Path to opencode.json", found)
                        break
                    self.say("        Type 1, 2 or 3.")
            else:
                self.say("        no company opencode.json found on this machine. A company setup needs one:")
                self.say("        the file your IT provides, defining the internal gateway as a provider.")
                candidate = self.pick_file("") or self.ask("Path to opencode.json", "")
        candidate = str(Path(candidate).expanduser()) if candidate else ""
        if not candidate:
            self.fail("company setup without an opencode.json - the board cannot reach a model. "
                      "Get the file from IT, or choose the private profile.")
            return ""
        if not Path(candidate).is_file():
            self.fail(f"opencode.json not found: {candidate}")
            return ""
        self.ok(f"OpenCode configuration: {candidate}")
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
        self.check_roles(Path(vault))

    def check_roles(self, vault: Path | None) -> None:
        """The board is whoever has a profile in the roles folder (section
        3.4). One folder, chosen here: ``<vault>/Roles`` by default, any
        other on request. Missing or empty: offer to create it and copy the
        examples in."""
        from decisionboard import roles as roles_mod
        default = (roles_mod.detect_folder(vault) or (vault / roles_mod.DEFAULT_SUBFOLDER)) if vault else None
        if self.roles_arg:
            folder = Path(self.roles_arg).expanduser()
        elif default is None:
            self.fail("no roles folder: the board has no members until one is chosen in Options.")
            return
        elif self.roles_arg is not None:
            folder = default
        else:
            folder = default
            self.say(f"        The board's members are whoever has a role profile in one folder.")
            self.say(f"        Default: {default}")
            if self.interactive and not self.confirm("Use that folder for the role profiles?", True):
                folder = Path(self.pick_folder(str(vault)) or self.ask("Roles folder", str(default))).expanduser()
        self.roles_folder = str(folder)
        members = roles_mod.members_in(folder) if folder.is_dir() else []
        if len(members) >= roles_mod.MIN_MEMBERS:
            self.ok(f"role profiles: board of {len(members)} in {folder}: {', '.join(members)}")
            return
        if not folder.exists():
            self.say(f"        no roles folder yet ({folder}).")
            question = "Create it, with the generic conduct note and a profile template?"
        else:
            self.say(f"        {folder} defines {len(members)} member(s) with content; a board needs at least {roles_mod.MIN_MEMBERS}.")
            question = "Add the generic conduct note and a profile template (nothing is overwritten)?"
        self.say("        A profile is one member's roles and responsibilities - one note per member, or one")
        self.say("        note with several members - edited in Obsidian, read on every run. The conduct note")
        self.say("        holds what is the same for every member: character, how to answer.")
        if self.confirm(question, True):
            with_examples = self.confirm(
                "Also copy the example board in (nine swim-lane profiles of a programme, to edit or delete)?", True)
            try:
                written = roles_mod.install_support_files(folder, examples=with_examples)
            except OSError as exc:
                self.fail(f"could not write to the roles folder: {exc}")
                return
            self.say(f"        written: {', '.join(p.name for p in written) or 'nothing new'}")
            members = roles_mod.members_in(folder)
            if len(members) >= roles_mod.MIN_MEMBERS:
                self.ok(f"role profiles: board of {len(members)} in {folder}: {', '.join(members)}")
                return
        self.fail(f"roles folder {folder} has no board yet: write at least {roles_mod.MIN_MEMBERS} member profiles "
                  f"(see {roles_mod.TEMPLATE_NAME}), then run the board.")

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
        self.say("\n6. Test call through OpenCode")
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
        if result.returncode != 0 and is_database_mismatch(result.stdout, result.stderr) and self.repair_database():
            self.say("        retrying the test call...")
            started = time.monotonic()
            result = self._run(command, timeout=180)
            duration = time.monotonic() - started
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
        self.step_board_call(config)

    def step_board_call(self, config: dict) -> None:
        """A second call shaped like a real board call: the member prompt,
        the knowledge block, and a JSON answer expected. The one-word test
        above passes on prompts the board never sends; this is the call that
        actually has to work (8 September 2026, when the board failed with
        "no answer text" after that test had passed)."""
        self.say("\n6b. Test call shaped like a real board call")
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from decisionboard.agent.opencode_client import OpenCodeError, OpenCodeProvider
        from decisionboard.agent.provider import TASK_BOARD
        from decisionboard.board import _member_prompt
        from decisionboard.knowledge import gather
        from decisionboard.roles import RolesUnavailable, load_roles

        try:
            selection = gather(config, "Should we rework the existing tooling or switch supplier?")
        except Exception as exc:
            self.warn(f"knowledge source could not be read for this test: {exc}")
            return
        context = ("This is a setup test. Answer briefly, in the JSON shape asked for."
                   + ("\n\n" + selection.text if selection.text else ""))
        try:
            profiles = load_roles(config)
        except RolesUnavailable as exc:
            self.fail(f"role profiles: {exc}")
            return
        first = next(iter(profiles.values()))
        prompt = _member_prompt(
            "Setup test: rework the existing tooling or switch supplier?", context,
            ("Rework", "Switch"), ("The date cannot move",), first.member, first,
        )
        self.say(f"        prompt: {len(prompt):,} characters, knowledge {selection.tokens:,} tokens "
                 f"from {len(selection.notes)} note(s)")
        provider = OpenCodeProvider(config, binary=self.opencode)
        started = time.monotonic()
        try:
            result = provider.complete(TASK_BOARD, prompt)
        except OpenCodeError as exc:
            self.fail(f"a board-shaped call failed: {exc}")
            self.say("        The one-word test above passed, so the endpoint and the key are fine;")
            self.say("        this is about the prompt itself. Send the line above and the raw file to Claude.")
            return
        duration = time.monotonic() - started
        answer = result.text.strip()
        try:
            json.loads(answer)
            shape = "valid JSON"
        except json.JSONDecodeError:
            shape = "not JSON - the board would count this member as failed"
        self.ok(f"answered in {duration:.1f}s, {len(answer):,} characters, {shape} "
                f"({result.input_tokens} in / {result.output_tokens} out tokens)")
        if shape.startswith("not"):
            self.warn("the model did not return the JSON the board asks for - the first line was: "
                      + (answer.splitlines() or [""])[0][:160])

    def opencode_data_dir(self) -> Path:
        """Where OpenCode keeps ``auth.json`` and its database: the XDG data
        folder, which OpenCode uses on Windows too (``opencode auth list``
        printed ``~\\.local\\share\\opencode\\auth.json`` on the target machine)."""
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        return base / "opencode"

    def repair_database(self) -> bool:
        """Moves OpenCode's local database aside (``*.db`` plus ``-wal`` and
        ``-shm`` sidecars) so the installed version recreates it. Only the
        session history is in there; ``auth.json`` is untouched. Asks first
        when interactive; returns whether anything was renamed."""
        folder = self.opencode_data_dir()
        candidates = sorted(p for p in folder.glob("*.db*") if p.is_file()) if folder.is_dir() else []
        if not candidates:
            self.warn(f"OpenCode's database was not found under {folder} - rename it by hand where OpenCode keeps it.")
            return False
        self.say("        OpenCode's local database does not match the installed version. Files:")
        for path in candidates:
            self.say(f"          {path}")
        if not self.confirm("Rename these to *.bak so OpenCode recreates them (session history only)?", True):
            return False
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for path in candidates:
            try:
                path.rename(path.with_name(f"{path.name}.{stamp}.bak"))
            except OSError as exc:
                self.fail(f"could not rename {path.name}: {exc} - close every OpenCode window and retry.")
                return False
        self.ok("renamed; OpenCode will create a fresh database on the next call.")
        return True

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
        except OSError as exc:   # e.g. WinError 193: not a Win32 application
            return subprocess.CompletedProcess(command, 126, "", f"{command[0]} cannot be started: {exc}")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 124, "", f"timed out after {timeout}s")

    # -- run ----------------------------------------------------------------

    def run(self) -> int:
        self.say("Decision Board setup")
        self.say("====================")
        self.step_environment()
        self.step_profile(_read_json(LOCAL_CONFIG))
        info = self.step_opencode()
        if self.profile == "private":
            self.step_login(info)
        else:
            self.say("\n4. Provider login")
            self.say("        not needed: the company opencode.json carries the gateway key.")
        config = self.step_config(info)
        self.step_test_call(config, info)
        self.say("\n7. Summary")
        notes = [problem for problem in self.problems if problem not in self.failures]
        if self.failures:
            self.say("        Must be fixed before the board can run:")
            for failure in self.failures:
                self.say(f"        - {failure}")
        if notes:
            self.say("        Worth knowing:")
            for note in notes:
                self.say(f"        - {note}")
        if not self.problems:
            self.ok("everything checked out.")
        elif self.failures:
            self.say("\n        Fix the items above, then run this script again.")
        launcher = "python scripts\\run_board.py serve" if sys.platform == "win32" else "python3 scripts/run_board.py serve"
        self.say(f"\n        Start the board:   {launcher}")
        self.say("        Change any of this later in the interface under Options.")
        if self.interactive and not self.failures and self.confirm("Start the board now?", True):
            from decisionboard.server import serve
            return serve(config, LOCAL_CONFIG, port=int(config["server"]["port"]))
        return 1 if self.failures else 0


def _get(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set(config: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_listed_model(listed: str) -> str:
    """A sensible default from ``opencode models``: the first entry that
    looks like a model string, preferring a well-known strong family."""
    candidates = [line.strip() for line in (listed or "").splitlines() if _looks_like_model_string(line.strip())]
    for marker in ("claude-opus", "claude-sonnet", "gpt-5", "gpt-4"):
        for candidate in candidates:
            if marker in candidate.lower():
                return candidate
    return candidates[0] if candidates else ""


def _looks_like_model_string(value: str) -> bool:
    value = (value or "").strip()
    return "/" in value and not value.startswith("/") and not value.endswith("/") and " " not in value and "<" not in value


def _all_on_path(name: str) -> list[str]:
    """Every executable called ``name`` on PATH, first match per folder.
    On Windows only PATHEXT extensions count: the npm launcher ``opencode``
    without an extension is a shell script that CreateProcess refuses
    (WinError 193, seen 8 September 2026), and ``.EXE``/``.exe`` are one
    file."""
    found: list[str] = []
    seen: set[str] = set()
    if sys.platform == "win32":
        exts = [e for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";") if e]
    else:
        exts = [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        for ext in exts:
            candidate = Path(folder) / (name + ext)
            try:
                if not candidate.is_file():
                    continue
                key = str(candidate.resolve()).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            found.append(str(candidate))
            break
    return found


def _resolve_env_placeholders(value: str) -> str:
    """OpenCode's ``{env:NAME}`` substitution, so the wizard can use the
    same key the file resolves at runtime."""
    return re.sub(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), value)


def is_database_mismatch(stdout: str | None, stderr: str | None) -> bool:
    text = (stdout or "") + (stderr or "")
    return "SQLiteError" in text or "no such column" in text or "databaseMigrations" in text


def diagnose(stdout: str | None, stderr: str | None) -> list[str]:
    """What a failed test call most likely means, from signatures seen on
    real machines. Falls back to the generic list."""
    text = (stdout or "") + (stderr or "")
    if is_database_mismatch(stdout, stderr):
        return [
            "This is OpenCode's own local database, not the board: its schema does not match the",
            "installed OpenCode version (seen 8 September 2026 on 1.17.7 as 'no such column: replacement_seq').",
            "Fix: close every OpenCode window, run this script again and answer yes when it offers to rename",
            "the database (session history only; auth.json keeps the logins). Also check `where opencode` and",
            "`opencode --version`: after `opencode upgrade`, PATH may still point at the old binary.",
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
    parser.add_argument("--profile", choices=["company", "private"], help="skip the company/private question")
    parser.add_argument("--roles", help="roles folder ('' for <vault>/Roles; skips the question)")
    parser.add_argument("--no-test", action="store_true", help="do not make the test call")
    parser.add_argument("--yes", action="store_true", help="no questions: take defaults and arguments")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    wizard = Wizard(interactive=not args.yes, vault=args.vault, model=args.model, run_test=not args.no_test,
                    opencode_config=args.opencode_config, profile=args.profile, roles=args.roles)
    return wizard.run()
