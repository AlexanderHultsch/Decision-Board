# Decision Board

Decision Board is a standalone decision-support tool with a browser
interface and a command line. You give it a question, it asks you what it
needs to know, and six standing members answer from six different
professional perspectives — each in its own model call that cannot see any
other member's answer. A seventh call synthesises the six into one
recommendation, you can ask follow-up questions against that synthesis, and
when you close the topic the board offers to write the decision into your
Obsidian vault — after showing you exactly what and where.

| Member | Perspective |
|---|---|
| Finance | Cost, budget vs forecast vs actuals, cBOM impact |
| HW Engineering | Hardware feasibility, maturity, technical risk |
| Mechanical Engineering | Mechanical feasibility, packaging, tolerances |
| Manufacturing | Manufacturability, ramp-up, supplier and plant capability |
| SW Engineering | Software scope, integration and test effort |
| KPI Check | Which option best fits time tracking, deliverables tracking, cost management and customer satisfaction |

## Why seven calls

Batching all six perspectives into one call would be cheaper, but a model
writing the sixth assessment can see the five it has already written and
converges towards them — the disagreement the board exists to surface would
be smoothed away before anyone could read it. Isolation costs a known amount:
each call to the configured OpenCode endpoint carries roughly 8,025 input
tokens of fixed overhead regardless of prompt size, so one board's initial
round of seven calls carries roughly 56,000 tokens of overhead before the
topic itself is counted. Each follow-up question after that costs one call,
not seven, since only the synthesis is re-run. In the browser interface one
topic adds one clarifier call before the board and, if you choose to remember
the decision, one memory-proposal call after it.

## Install and run

Python 3.11+, standard library only — no `pip` dependencies.

```
copy config\config.example.json config\config.local.json
```

Edit `config/config.local.json` and set `provider.models.board` to the
`provider/model` string your OpenCode endpoint expects, then start the
browser interface:

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
