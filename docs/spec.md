# Decision Board — specification

## 1. Purpose and scope

Decision Board is a standalone command-line decision-support tool. Alex poses a
question — a topic, its context, the options under consideration and any hard
constraints — and six standing members answer it from six different
professional perspectives, each in a model call that cannot see any other
member's answer. A seventh call synthesises the six into one recommendation.

The tool does one thing: run this board and hold the follow-up conversation
that follows it, for the length of one invocation. It has no data store, no
scheduler, and no access of its own to Jira, SharePoint, Outlook or Teams.
Anything the board should reason about — an OIL item, a Jira status, a cost
figure — is supplied by Alex as free text when he is asked for context.

## 2. Principles

Carried over from the source project's architecture principles (Program Lead
Cockpit spec 3.2), reworded for a tool that is a single command with no
persistent state:

| ID | Principle |
|---|---|
| AP-1 | **Deterministic before AI.** Assembling the six prompts, parsing each member's JSON response, ordering assessments back into a fixed member order, and rendering the tables and synthesis prose are all Python. The model is used only for the language understanding, per-member assessment and synthesis text themselves. |
| AP-3 | **Batch.** One invocation of the `board` command is one run covering the whole topic — options, constraints and all six perspectives — never a separate invocation per member. FR-3.3a (3.2) is a deliberate, narrowly-scoped exception to this principle: see below. |
| AP-4 | **Propose, do not execute.** The board produces an answer and, in a follow-up turn, may put a question back to Alex — it never acts on its own recommendation. The memory design in section 5 extends this principle to remembering: a proposed entry is written only after Alex confirms it, never on the board's own initiative. |
| AP-7 | **Encapsulated AI layer.** Provider and model are configuration, not code. `provider.models.board` in `config/config.local.json` names the `provider/model` string OpenCode is invoked with; nothing in `board.py` or `cli.py` names a model. |

## 3. The board

### 3.1 Standing members

| Member | Perspective |
|---|---|
| Finance | Cost, budget vs forecast vs actuals, cBOM impact |
| HW Engineering | Hardware feasibility, maturity, technical risk |
| Mechanical Engineering | Mechanical feasibility, packaging, tolerances |
| Manufacturing | Manufacturability, ramp-up, supplier and plant capability |
| SW Engineering | Software scope, integration and test effort |
| KPI Check | Which option best fits the four core responsibilities: time tracking, deliverables tracking, cost management, customer satisfaction |

### 3.2 Process

| ID | Requirement |
|---|---|
| FR-3.1 | Input: topic, context, options under consideration, hard constraints. The CLI asks for each of the four in turn (`cli.py`, `cmd_board`). |
| FR-3.2 | If the topic or the options are unclear, the member prompt (`board_members.md`) instructs the model to ask at least three targeted questions instead of producing an assessment, returning `{"status": "questions", "questions": [...]}`. The response parser (`board._parse_member_response`) currently recognises only the assessment shape (`view`/`risks`/`recommendation`); a `questions` response does not match it and that member is recorded under `failed_members` rather than surfaced to Alex as questions. The instruction is in force in the prompt; the CLI has no dedicated path for it yet. |
| FR-3.3 | Each member produces a separate, clearly attributed assessment: `view`, `risks`, `recommendation` (`MemberAssessment`). |
| FR-3.3a | **Members are polled in isolation.** Each member is a separate model call that does not see any other member's answer. All six prompts are built before any of the six calls is dispatched (`run_board`), so isolation is structural, not conventional, even though the six calls run concurrently. Only once all six have returned are the results combined and passed to the synthesis call. |
| FR-3.4 | A synthesis follows: overall recommendation, decisive criterion, main counter-arguments, and what new information would change the recommendation (`board_synthesis.md`, `_synthesis_text`). |
| FR-3.5 | Output format: one table per member, synthesis as short prose. `board.render` produces this from the structured `BoardResult` in Python; it is never asked of the model. |
| FR-3.6 | Disagreement between members is stated explicitly, never smoothed over. The synthesis prompt requires a `disagreements` list (empty only where members genuinely agree), and `render` prints it under the synthesis, or states plainly that none were stated. |
| FR-3.7 | Context supplied by Alex — a pasted OIL summary, a Jira status, a cost figure — is passed through the single free-text `Context` input and is used as context by every member (`board_members.md`). The board does not fetch such data itself: this repository holds no data store and no Jira, SharePoint or Outlook access (section 8). What was "the board draws in current OIL, Jira and cost status" in the source specification is, here, "Alex draws it in by typing or pasting it." |

**FR-3.3a is a deliberate exception to AP-3.** One batched call across all six perspectives would be far cheaper and would satisfy AP-3, but a model writing the sixth assessment can see the five it has already written and converges towards them — the disagreement FR-3.6 exists to surface would be smoothed away before anyone could read it. The cost of isolation is accepted for the independence it buys, and that cost is known, not assumed: a verification run against OpenCode 1.18.11 in the source project (Program Lead Cockpit spec 3.8) sent a four-word prompt and got a two-token answer back, and it still reported 8,025 input tokens — OpenCode's own system prompt and tool definitions, charged on every call regardless of prompt size. Seven calls per board question therefore carry roughly 56,000 input tokens of fixed overhead before a single word of the actual topic is counted.

### 3.3 Conversation

A board run does not end at the synthesis. `cmd_board` opens a follow-up loop
immediately after printing the result: it keeps asking for a follow-up
question until Alex enters a blank line.

The owner decision behind this loop is that a follow-up is not a new board
run. `ask_follow_up` re-runs only the synthesis prompt — one model call — over
the original six assessments plus every question/answer pair asked so far
(`BoardConversation.turns`); the six members are never polled again for the
same topic. This is why `board_members.md` requires each member's first
answer to be self-contained and to carry its reasoning, not just its
conclusion: it is the only material any later follow-up will ever have to
work with.

The synthesis prompt's follow-up instructions (`board_synthesis.md`, "Follow-up
turn") allow the model to put a question back to Alex rather than guess, where
what would be needed to answer with confidence is genuinely missing from the
six assessments and the conversation so far.

The whole conversation — the initial result and every follow-up turn — lives
only inside the one `board` invocation that produced it. `BoardConversation`
is an in-memory object; nothing is written to disk, and nothing survives the
process exiting. Section 5 is the design for changing that.

## 4. AI provider and the OpenCode invocation contract

The provider layer (`agent/provider.py`, `agent/opencode_client.py`) is a copy
of the source project's, taken as-is per decision 0005: two repositories that
evolve at different speeds are better served by duplicated code than by a
shared package.

| ID | Requirement |
|---|---|
| AI-1 | Provider, model and token limits are configurable per task type. This repository has one task type, `ai_board`, resolved from `provider.models.board` and, optionally, `provider.token_limits.board` (`resolve_model`, `token_limit`). |
| AI-2 | Every run logs provider, model, token usage and duration. `OpenCodeProvider.complete` returns an `AiResult` carrying all four for every call; `run_board` passes the synthesis call's `AiResult` to `audit.log_run` (section 6). |
| AI-3 | Only approved endpoints may be configured; confidential program content must not leave approved infrastructure. `provider.endpoint` in the configuration is not read by any code path — OpenCode is invoked as a fixed subprocess — but stays in the config file as the record of which endpoint was approved. |
| AI-5 | Until a second endpoint is attested by name and date, every model configuration key resolves to the same approved endpoint. This repository has one key (`board`); nothing here contradicts that. |

**Invocation.** `opencode run --format json --model <provider>/<model> [--dir <path>] [--auto] "<prompt>"`, built by `OpenCodeProvider._build_command`.

**Output is JSON Lines**, parsed line by line, never as one JSON array (`_parse_output`):

| ID | Requirement |
|---|---|
| OC-1 | A line that is not valid JSON is reported as an `OpenCodeError`, never silently skipped. |
| OC-2 | The answer is the concatenation of every `text` event's `part.text`, in order. |
| OC-3 | Unknown event types are ignored, not treated as errors — only `text` and `step_finish` are inspected; every other type falls through. |
| OC-4 | Provider, model, token counts and duration are taken from the run: tokens from `step_finish` events, provider/model from the `--model` string that was passed. |
| OC-5 | A run that produces no `text` event at all raises `OpenCodeError`, never returns an empty answer. |
| OC-6 | Whether a headless run hangs without `--auto` is unverified, and so is what `--auto` actually approves. The client sends one prompt and reads one text answer back; it registers no tools of its own. But `--auto` auto-approves whatever OpenCode's own built-in tooling can reach in the working directory, which was never tested here, so the flag is not established as harmless on the basis of this repository exposing no tool surface. `provider.opencode.auto_approve` defaults to `True` because a headless run that stops to ask would hang; confirm on the target machine what a run does without it, and what it can touch with it. |

**Cost characteristic.** See 3.2's note on FR-3.3a: 8,025 input tokens of fixed
overhead per call, measured once against OpenCode 1.18.11 in the source
project and carried over as the basis for the ≈56,000-token figure quoted
there for one board's initial round.

## 5. Memory

**Designed in decision 0005. Not built.** No code in this repository reads or
writes a memory folder; `ask_follow_up` and `run_board` work only from the
current invocation's own assessments and conversation. What follows is the
design as decided, not a description of running code.

```
memory/
  index.md              # one line per topic note, maintained by the board
  topics/<slug>.md      # one note per topic the board has been consulted on
  principles.md         # standing positions Alex has confirmed - never re-argued
```

This is a folder of Markdown files, which makes it an Obsidian vault by
construction — Obsidian opens any folder of `.md` files, so "Obsidian or a
text file" was never a real choice. Alex would open it in Obsidian for the
graph and the backlinks, or in Notepad if he did not want either; the board
would read the same files either way.

| Rule | Reason |
|---|---|
| Every note carries YAML front matter: `title`, `tags`, `created`, `last_consulted` | Deterministic selection (AP-1): Python would pick the notes whose title or tags overlap the new topic; the model would never search the vault itself |
| A session would receive at most the notes Python selected, plus `principles.md`, capped by a configured token budget | The same reasoning as AP-3/AP-9 in the source project: reason on the delta the topic actually touches, not on the whole vault |
| After the synthesis, the board would propose memory entries; each is written only after Alex confirms it individually | The same per-entry decision the source project's workbook context (WB-3) and import flow (6.2) already use — never a blanket "remember all of this." A memory that fills itself with whatever the model found notable stops being trusted within a month |
| The `memory/` folder is git-ignored | It will hold program content once it exists; the repository is on GitHub and must stay free of it. `.gitignore` already reserves the path today, ahead of the folder existing |

The board never writes to its own memory on its own initiative — that rule is
designed in now, before there is anything to write, so it is never a later
retrofit.

## 6. Audit trail

Every completed board run is logged, whether or not the follow-up loop that
comes after it is used. `board.run_board` calls `audit.log_run("board", ...)`
once, after the synthesis call (or after the skip when fewer than two members
answered); follow-up turns are not separately logged.

The trail is `<runtime.audit_folder>/audit_log.jsonl` — append-only JSON
Lines, one object per line, created if it does not exist. Appending, rather
than rewriting the file, means a write cannot corrupt what is already there;
a rewrite-the-whole-file format would put every prior entry at risk on a
failed write. Each entry carries: `timestamp`, `action_class` (always `"B"` —
see section 8, the board has no class C–E action), `target`, a one-line
`description`, `decision` (`"completed"`), `actor`, `pc_name`, `provider`,
`model`, `tokens`, `duration_seconds`, and a structured `counts` object
(`assessments`, `failed_members`, `llm_calls`, `over_token_limit`).

A line that fails to parse when the trail is read back is returned as
`{"malformed": <line>}` rather than dropped, so a hole in the trail is
visible rather than silently absorbed. Logging itself can never take a run
down: if `runtime.audit_folder` is unset, or the write fails, `log_run`
returns `None` and the run's result is still returned to the caller — a run
that did its work and then could not log it has still done its work.

## 7. Language rules

Carried over from the source project (spec chapter 10):

| ID | Requirement |
|---|---|
| LN-1 | Specification, source code, configuration, prompts and log output are in English. |
| LN-2 | Generated correspondence follows the language of the counterpart or source message. This repository generates no correspondence — the board answers Alex, in whatever session he is running, and nothing here is addressed to a third party — so the rule is inherited but has nothing to act on today. It stays recorded in case a future output of this tool is ever addressed to someone other than Alex. |
| LN-3 | No mixing of languages within a single output. |

## 8. Out of scope

Everything below stayed in the source repository (Program Lead Cockpit) under
decision 0005, or was never part of the board to begin with:

| Item | Why it is not here |
|---|---|
| GOV-1..GOV-5, the approval-token gate, action classes C–E | The board has no class C–E action: it never contacts a person and never writes to an external system. Section 6's `action_class: "B"` is the only class this tool ever logs. |
| Reading OIL, Jira, Confluence, SharePoint, Outlook or Teams | This repository has no data store and no connector code. Anything the board should know is typed or pasted into `Context` by Alex (FR-3.7, 3.2). |
| Adaptive prioritisation, the import flow, the progress-check engine, the email briefing | These are TR-1/TR-2/TR-3 of the source project and never belonged to the board. |
| Model tiering across task types | The source project's `Config` names five task types with different model classes; this repository has exactly one task type (`ai_board`) and one configuration key (`provider.models.board`). |
| Session resumption across separate CLI invocations | The one open question phase 7 of the source project left behind. The memory design in section 5 is the answer that does not depend on it: what was worth keeping goes into a note; the rest was not meant to survive the process exiting anyway. |

## 9. Origin

Decision Board was TR-4 of the Program Lead Cockpit, a single-user automation
system for a Program Lead at Visteon Electronics running the MB32829 Gen6
Dual DCDC program. Decision 0005 of that project, dated 8 September 2026,
split the AI Board out into its own repository — this one — because decision
support on arbitrary topics is a different product from the Open Issue List
that the rest of that system tracks, even though the two share an AI layer.
A reader who meets a stray reference to `OIL`, `MB32829`, or a requirement ID
from a chapter other than the ones this document defines is looking at
something that predates the split; it is not part of this repository.
