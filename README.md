# Decision Board

Decision Board is a standalone decision-support tool with a browser
interface and a command line. You give it a question, it asks you what it
needs to know, and six standing members answer from six different
professional perspectives — each in its own model call that cannot see any
other member's answer. A seventh call synthesises the six into one
recommendation, you can ask follow-up questions against that synthesis, and
when you close the topic the board offers to write the decision into your
Obsidian vault — after showing you exactly what and where.

**Who sits on the board is decided by role profiles, not by code.** One
folder of Markdown notes — by default `Roles/` inside your vault, or any
folder you choose in Options — holds one note per member, or one note with
several members. Each note is that member's personality: character, skills,
the KPIs it watches, its vocabulary, how it assesses, what it pushes back on.
The notes are read fresh on every question, so an edit in Obsidian is in
force immediately. Six example profiles ship in `roles/` (Finance, HW
Engineering, Mechanical Engineering, Manufacturing, SW Engineering, KPI
Check); the setup wizard copies them into your folder to start from. See
`roles/README.md` for the file format.

## Why one call per member

Batching all perspectives into one call would be cheaper, but a model
writing the last assessment can see the ones it has already written and
converges towards them — the disagreement the board exists to surface would
be smoothed away before anyone could read it. Isolation costs a known amount:
each call to the configured OpenCode endpoint carries several thousand input
tokens of fixed overhead regardless of prompt size, so a board of six makes
seven calls per question before the topic itself is counted. Each follow-up question after that costs one call,
not seven, since only the synthesis is re-run. In the browser interface one
topic adds one clarifier call before the board and, if you choose to remember
the decision, one memory-proposal call after it.

## Install and run

Python 3.11+, standard library only — no `pip` dependencies. Clone the
repository:

```
git clone https://github.com/AlexanderHultsch/Decision-Board.git
cd Decision-Board
```

Then run the setup wizard. It works on Windows, macOS and Linux, and on a
company machine as well as a private one. It asks first how you will use
Decision Board:

* **Company or organisation** — your IT provides an `opencode.json` that
  points OpenCode at an internal gateway. Prompts and notes stay on approved
  infrastructure, and the file carries the key, so there is nothing to log
  in to.
* **Private or your own account** — OpenCode logs in to a provider you
  choose. Your question and the notes the board selects are sent to that
  provider, so point the knowledge source at a vault you may share with it.

Everything after that is the same either way. The wizard checks Python, git
and OpenCode (and tells you how to install OpenCode if it is missing), writes
`config/config.local.json` with everything preselected, lets you choose the
knowledge source with the folder dialog, and makes two real test calls to the
model so you know it is reachable before the board is asked anything:

```
python scripts/setup.py
```

In a company setup the wizard asks for that `opencode.json`. Its path is
stored as `provider.opencode.config_file` and passed to every OpenCode call
as `OPENCODE_CONFIG`, so the board finds it from any folder, and the model
string is `provider/model` exactly as the file defines it. The wizard also
asks the gateway which models it serves, so a stronger model behind the same
approved endpoint is visible at a glance.

In a private setup the wizard offers to run `opencode auth login` for you and
then picks a model from what `opencode models` lists.

The two test calls are a one-word call and a call shaped like a real board
call, with your notes attached and JSON expected. A failure prints OpenCode's
complete output and the likely causes. Non-interactive use is supported too:

```
python scripts/setup.py --yes --profile private --vault "/path/to/vault"
```
On a machine without the repository yet, `scripts/install.ps1` does the
clone as well: it asks for the folder, clones or pulls, then runs the wizard.

Start the browser interface:

```
python scripts/run_board.py serve
```

It listens on `http://127.0.0.1:8765/` only and opens your default browser.
Open **Options** (the gear) to choose your knowledge source — a folder of
Markdown notes, typically an Obsidian vault — with the native folder dialog,
and to set the model string, token budget, audit folder and theme. Options
are saved to `config/config.local.json`.

The command-line front end is still there and asks for topic, context,
options and constraints directly:

```
python scripts/run_board.py board
```

## Knowledge source

The board reads every `.md` file under the configured vault folder for each
question. Notes are ranked against the question in Python — title, tags,
file name and body — and the best-ranked ones are sent, up to
`knowledge.token_budget` tokens per call (default 6,000). If the whole vault
fits, the whole vault is sent. A configured folder that cannot be read stops
the run with an error; there is no fallback source.

Nothing is written to the vault without your confirmation. On **Close
topic** the board proposes a note and a place for it; you edit and confirm,
or don't write.

The `board` subcommand asks, in order: topic, context, options under
consideration (one per line, blank line to finish), and hard constraints (one
per line, blank line to finish). It then prints one table per member — view,
risks, recommendation — followed by the synthesis: overall recommendation,
decisive criterion, counter-arguments, what would change it, and any
disagreements stated explicitly. After that it opens a follow-up loop: type a
question, get an answer against the same six assessments, and press Enter on
a blank line to end the conversation. The final line reports the total number
of model calls made.

## Tests

```
python -m pytest -q tests/
```

18 tests pass.

## Status

The code is a straight port from the source project and works. The memory
described in `docs/spec.md` section 5 — a folder of Markdown notes the board
would consult and grow across sessions — is designed but not built: nothing
in this repository reads or writes it yet. Nothing else is currently planned
for this repository.

See `docs/spec.md` for the full specification.
