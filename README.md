# Decision Board

Decision Board is a standalone command-line decision-support tool. You give it
a topic, its context, the options under consideration and any hard
constraints, and six standing members answer from six different professional
perspectives — each in its own model call that cannot see any other member's
answer. A seventh call synthesises the six into one recommendation, and you
can then ask follow-up questions against that synthesis.

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
not seven, since only the synthesis is re-run.

## Install and run

Python 3.11+, standard library only — no `pip` dependencies.

```
copy config\config.example.json config\config.local.json
```

Edit `config/config.local.json` and set `provider.models.board` to the
`provider/model` string your OpenCode endpoint expects, then run:

```
python scripts/run_board.py board
```

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
