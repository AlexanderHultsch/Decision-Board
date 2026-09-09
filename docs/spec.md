# Decision Board — specification

## 1. Purpose and scope

Decision Board is a standalone decision-support tool with two front ends: a
command line and a local browser interface (section 9). Alex poses a
question — a topic, its context, the options under consideration and any hard
constraints — and the board's members — whoever has a role profile in the roles folder,
section 3.4 — answer it from their professional perspectives, each in a
model call that cannot see any other member's answer. One more call synthesises them into one recommendation.

The tool does one thing: run this board and hold the follow-up conversation
that follows it, for the length of one topic. It has no database, no
scheduler, and no access of its own to Jira, SharePoint, Outlook or Teams.
Its one source of knowledge is a folder of Markdown notes Alex chooses — an
Obsidian vault (section 5) — which the board reads for every question and
writes to only after Alex has confirmed a note. Anything else the board
should reason about — an OIL item, a Jira status, a cost figure — is
supplied by Alex as free text.

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

### 3.1 Members

**The board is whoever has a filled role profile in the roles folder**
(section 3.4). There is no member list in the code, in the prompts or in
this document, and the repository ships no members: adding a profile adds
a member, removing one removes it, and a board needs at least two. Today
the members are the programme lead and the project managers of the
programme's swim lanes, each speaking for every role and responsibility
under their lead; more roles
(working level, leadership within a swimlane) can be added as profiles
later. One board run is one call per member plus one synthesis call.

### 3.4 Role profiles

**Decided 8 September 2026.** What a member *is* — character, skills, the
KPIs it watches, the process tasks it owns, how it assesses, what it pushes back on —
is a Markdown note in one folder, written and edited in Obsidian, read
fresh on every board run (`roles.py`, `load_roles`). Nothing caches it: an
edit is in force on the next question. The same notes define *who* sits on
the board (3.1).

| Rule | Reason |
|---|---|
| One folder, chosen by Alex: `knowledge.roles_folder`, picked in Options or by the setup wizard with the folder dialog. Empty means a folder in the vault whose name starts with "Roles" (`Roles`, `Roles&Responsibilities`) or reads "R&R". No folder, no board: the repository ships no member profiles | One place to look, named as Alex names it. Nothing in code can stand in for the board. |
| **What is the same for every member** — character, how to answer, the rule that a member speaks for every role and responsibility under its lead, and that it judges options against its own measures — is one note in the same folder with `kind: conduct` in its front matter (`_Board member conduct.md`), prepended to every member's profile on every call | The personality that is shared is written once, edited in one place, and never duplicated into every profile. |
| **KPIs: the role names them, the network holds the numbers** (decided 9 September 2026; the "KPI Check" member was removed the same day). Each member's profile lists **its own** measures under `## Targets I am judged on` and never a value — they differ from member to member, and the conduct note carries only the rule, not the list. `MG0`, Maturity Gate Zero, is the baseline; later gates and the current state are recorded against it | Each swim lane is held to its own measures and is the one that knows them. Numbers change every few weeks; a role description should not. |
| A member's numbers are a note in the vault with `kind: kpi`, `affected_swimlanes: [<name>, ...]` plus `lead_swimlane: <name>` (the members it is attached to; `member:` is still read) and `updated: YYYY-MM-DD` in its front matter. `knowledge.kpi_notes` attaches every such note to its member's call, on every run, whatever the question, capped at 2,500 tokens per member and excluded from the ranked selection. A member without a note is told so in its prompt and must state its assumption rather than invent a baseline | The data a member is judged against is mandatory context for that member (AP-1: attached by Python, deterministically), not a note that may or may not rank high enough for this question. The confirm screen names which members have a KPI note. |
| **Age is computed, not trusted to the model.** Python puts the note's `updated` date, its age in days, and a warning past `knowledge.kpi_stale_days` (default 30) above the note; the conduct note requires every quoted value to carry its date, and a value whose date cannot be seen to be treated as an assumption | A number without its date is the most expensive kind of confident answer. |
| **Every** note marked `kind: conduct` is prepended, in file-name order, so common ground can be split across files if wanted; one ships (`_Board member conduct.md`) | Behaviour is written once and edited in one place. Programme facts are not written here at all — they belong in the knowledge network (decided 9 September 2026: the `_Programme context.md` note was removed for that reason). |
| **One file, one member.** The official role description is kept word for word under `## Official description`; what the board needs beyond it — targets, what the role protects when it cannot have everything, and the process tasks that name the role (links to the VPDS task pages, decided 9 September 2026: no keyword list, the process is the source) — is written below it in the same file. A second file naming the same member is ignored and reported on the confirm screen, never merged | One place to read a member, and the official text stays recognisable as official. The added sections are what an organisational job description cannot carry: a description written to define a job states no target and says nothing about what the role sacrifices, which is exactly what a decision needs. |
| The conduct note also tells every member to **read the knowledge network first** and to say where a fact came from, and warns that a role description written for the organisation may state no target and may share whole sections with every other member | The vault is young: silence in it is missing information, not evidence. And the shared sections of official descriptions — customer focus, change management, risk management, innovation — are common ground, not what makes a member's view worth hearing. |
| A profile with a heading and nothing under it is *not filled yet*: it is left off the board and named on the confirm screen, in Options and in the CLI | An empty note must never produce an empty opinion, and must never disappear silently either. |
| Every role page carries `Part of Decision Board AI: true` or `false` in its front matter (a checkbox in Obsidian; `board: false` is still read). `false` keeps the page in the folder, linkable from the process, without a seat on the board — Account Management (decided 9 September 2026) | The membership is visible on every role page as one checkbox, and a role the process names but the board does not seat stays linkable. |
| Notes whose name starts with `_`, or whose front matter says `kind: conduct` or `kind: template`, are never members | Support files live next to the profiles without joining the board. |
| One file per role, or one file with several roles. A file whose front matter names a `member:`, or with at most one level-one heading, is one role (named by the front matter or the file). A file with two or more level-one headings is several roles, one per heading, with `key: value` lines directly under the heading as that role's metadata | Alex writes the board the way he thinks about it — six notes, or one note called Board. |
| **A role file carries `level:` and nothing else about itself.** The icon and colour are derived from the member's name in code, and the line the synthesis is told about a member is the first sentence under `## What I protect when I cannot have everything`, falling back to the first sentence of the profile. `perspective`, `icon`, `color`, `short` and `order` are still read when present, but nothing needs them | A role description is about the role, not about how it is drawn. And official descriptions open with wording every swim lane shares, so the first sentence would tell the synthesis nothing — what a member protects is exactly what distinguishes it. |
| Each member receives its own note in full, under `## Role profile`, declared authoritative for that call, and nothing about any other member — not even their names | FR-3.3a: a member never sees another member's material, and no longer knows who else is on the board. |
| The synthesis receives one line per member (`title: perspective`), never the full profiles | It weighs who said what; it does not need to be six people. |
| The roles folder is excluded from the knowledge selection when it lies inside the vault (`knowledge._roles_inside`) | A profile is mandatory context for one member, not a note competing for the token budget of all of them. |
| Fewer than two profiles is `RolesUnavailable`, shown as an error with the folder named | A board of one is not a board; a misconfigured folder must not silently become the six examples. |
| The setup wizard proposes the detected folder (else `<vault>/Roles`), accepts any other, and offers to create it with the conduct note, a profile template and, on request, the example board (`roles/examples/`: the nine swim lanes of one programme, Alex's own, shipped with his consent) when it is missing or has fewer than two filled profiles; Options installs the support files. Existing files are never overwritten | Members are Alex's to write; the examples are his own to edit or delete. |

### 3.2 Process

| ID | Requirement |
|---|---|
| FR-3.1 | Input: topic, context, options under consideration, hard constraints. The CLI asks for each of the four in turn (`cli.py`, `cmd_board`). The browser interface asks for one free-text question and derives the four through FR-3.2. |
| FR-3.2 | **Clarification happens before the board, in one call, independent of the members** (decided 8 September 2026). The clarifier (`clarify.py`, prompt `clarifier.md`) reads Alex's question plus the knowledge block selected for it (section 5), extracts the four FR-3.1 inputs, and asks at least one question — always at least one, even for a clear topic: the one whose answer would most change the recommendation. Python enforces the minimum (`FALLBACK_QUESTION` when the model returns none). Alex's answers are folded into the context by string assembly (`merge_answers`), Alex confirms or edits the four inputs, and only then are the members polled. The clarifier's questions never reach the members; the member prompt (`board_members.md`) now tells a member to state an assumption rather than ask back. A member that returns the old `{"status": "questions"}` shape anyway is recorded under `failed_members` with its questions, not silently lost. The CLI `board` command does not run the clarifier; it asks for the four inputs directly. |
| FR-3.3 | Each member produces a separate, clearly attributed assessment: `view`, `risks`, `recommendation` (`MemberAssessment`). |
| FR-3.3a | **Members are polled in isolation.** Each member is a separate model call that does not see any other member's answer, nor any other member's profile or name (3.4). All prompts are built before any call is dispatched (`run_board`), so isolation is structural, not conventional, even though the calls run concurrently. Only once all have returned are the results combined and passed to the synthesis call. |
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
only inside the one topic that produced it. `BoardConversation` is an
in-memory object; nothing is written to disk by the conversation itself, and
nothing survives the process exiting. In the browser interface a topic ends
with **Close topic**, which asks whether the decision should be written to
memory (section 5); the CLI conversation ends at a blank line, without that
step.

## 4. AI provider and the OpenCode invocation contract

The provider layer (`agent/provider.py`, `agent/opencode_client.py`) is a copy
of the source project's, taken as-is per decision 0005: two repositories that
evolve at different speeds are better served by duplicated code than by a
shared package.

| ID | Requirement |
|---|---|
| AI-1 | Provider, model and token limits are configurable per task type. This repository has one task type, `ai_board`, resolved from `provider.models.board` and, optionally, `provider.token_limits.board` (`resolve_model`, `token_limit`). |
| AI-2 | Every run logs provider, model, token usage and duration. `OpenCodeProvider.complete` returns an `AiResult` carrying all four for every call; `run_board` passes the synthesis call's `AiResult` to `audit.log_run` (section 6). |
| AI-3 | Only approved endpoints may be configured; confidential program content must not leave approved infrastructure. **The approved endpoint since 8 September 2026 is the company's LiteLLM gateway** (`litellm-ai.visteon.com`), reached through a company-provided `opencode.json` that defines it as OpenCode provider `azure` with the model `Opencode-Kimi-K2.7`. `provider.endpoint` in the configuration is not read by any code path — OpenCode is invoked as a subprocess — but stays as the record of that approval. The board never talks to a model directly; every call goes through OpenCode, and OpenCode through that file. A model from another provider is out of bounds unless the company adds it to the same file. |
| AI-5 | Until a second endpoint is attested by name and date, every model configuration key resolves to the same approved endpoint. This repository has one key (`board`); nothing here contradicts that. |

**Invocation.** `opencode run --format json --model <provider>/<model> [--dir <path>] [--auto] "<header>"` with the prompt on standard input, built by `OpenCodeProvider._build_command` and run by `_run`. The two bracketed flags are passed only when the installed version lists them in `opencode run --help` (OC-7); the prompt never travels on the command line (OC-10).

**Output is JSON Lines**, parsed line by line, never as one JSON array (`_parse_output`):

| ID | Requirement |
|---|---|
| OC-1 | A line that is not valid JSON is reported as an `OpenCodeError`, never silently skipped. |
| OC-2 | The answer is the concatenation of every `text` event's `part.text`, in order. |
| OC-3 | Unknown event types are ignored, not treated as errors — only `text` and `step_finish` are inspected; every other type falls through. |
| OC-4 | Provider, model, token counts and duration are taken from the run: tokens from `step_finish` events, provider/model from the `--model` string that was passed. |
| OC-5 | A run that produces no `text` event at all raises `OpenCodeError`, never returns an empty answer. |
| OC-6 | Whether a headless run hangs without `--auto` is unverified, and so is what `--auto` actually approves. The client sends one prompt and reads one text answer back; it registers no tools of its own. But `--auto` auto-approves whatever OpenCode's own built-in tooling can reach in the working directory, which was never tested here, so the flag is not established as harmless on the basis of this repository exposing no tool surface. `provider.opencode.auto_approve` defaults to `True` because a headless run that stops to ask would hang; confirm on the target machine what a run does without it, and what it can touch with it. |
| OC-7 | **Optional flags are probed, not assumed.** On 8 September 2026 the OpenCode installed on the target machine printed its usage text and exited 1 on every call: its `run` command did not know `--auto` (nor `--dir`), and a flag it does not know is a fatal argument error, not an ignored one. The client therefore runs `opencode run --help` once per process and passes `--auto` and `--dir` only when that text lists them. With `--auto` absent, `provider.opencode.auto_approve` has nothing to act on; whether that version stops to ask for permission on a headless run is, per OC-6, still unverified - it did not on the first real runs, because the client registers no tools. On Windows the prompt is one command-line argument and the OS limit is 32,767 characters; the client refuses above about 30,000 with a message that names the knowledge token budget as the thing to lower. |
| OC-8 | **The company `opencode.json` is passed by environment, not by working directory.** OpenCode looks for `opencode.json` in its global config folder and in the working directory; the company file lives in neither, so `provider.opencode.config_file` names it and the client starts every `opencode` process with `OPENCODE_CONFIG` set to that path (`opencode_environment`). The model string in `provider.models.board` is `<provider id>/<model id>` exactly as the file defines them — `azure/Opencode-Kimi-K2.7` — which is why "select Kimi K2.7" is a configuration value, not a code change. The file holds the gateway key and stays outside this repository; `{env:NAME}` placeholders in it are honoured by OpenCode and by the setup wizard. |
| OC-9 | **A run that answers nothing must say what it did instead.** The answer is read from any text-carrying part, not only from an event literally typed `text` (OC-2 stays the shape spec 3.8 verified; a version that wraps the same part in another event is read the same way). When a run still yields no answer, the error names every event type the run produced with counts, the text of any `error` event — a run can report an error and still exit 0 — the tools it called instead of answering, and the path of the raw JSON Lines, written to `<audit_folder>/opencode-debug/`. `provider.opencode.extra_args` appends further CLI arguments (for example `["--agent", "plan"]`) so a fix for such a run is configuration, not a code change. |
| OC-11 | **A run that answers nothing is retried once.** On 9 September 2026 a call came back with reason `stop`, 920 reasoning tokens and 0 output tokens: the model thought and wrote nothing, and because it was the clarifier call the whole session died. `OpenCodeProvider.complete` now retries such a run once with a one-line nudge in front of the same prompt; a second empty run is reported with both attempts and the token story ("finished with reason 'stop', 920 reasoning tokens, 0 output tokens"). Any other failure is not retried. The board's own retry for tool-only answers stays on top of this. |
| OC-10 | **The prompt goes to OpenCode on standard input, never as an argument.** Windows caps a command line at 32,767 characters and a board call is longer (33,033 on 9 September 2026, with a 6,000-token knowledge budget), so the setup wizard's board-shaped test call failed where the one-word test passed. `opencode run` appends piped input to its message; the one positional argument is a fixed header saying that the instruction follows. The knowledge budget is no longer bounded by the operating system. |

**Cost characteristic.** See 3.2's note on FR-3.3a: 8,025 input tokens of fixed
overhead per call, measured once against OpenCode 1.18.11 in the source
project and carried over as the basis for the ≈56,000-token figure quoted
there for one board's initial round.

## 5. Knowledge source and memory

**Decided 8 September 2026, built in `knowledge.py` and `memory_writer.py`.**
Alex selects one knowledge source: a folder of Markdown notes, which is what
an Obsidian vault is on disk. Alex's own is an Obsidian vault in an
offline-synced OneDrive folder. The board reads it for every question and
writes to it only after Alex confirms a note.

| Rule | Reason |
|---|---|
| The source is one folder, chosen in the browser interface's Options with a native folder dialog or typed in; stored as `knowledge.vault_path` | One thing to configure. "Obsidian or a text file" was never a real choice: Obsidian opens any folder of `.md` files, and a single `.md` file in a folder of its own is a vault of one note. |
| No backup source. A configured folder that cannot be read stops the run with an error naming the folder | Alex's decision: an error message, not a fallback. A board that silently answered without its knowledge would look like a board that had read it. |
| The whole vault is read; every `.md` file under the folder, Obsidian's own `.obsidian` and `.trash` folders skipped | Alex's decision: the whole memory, no folder selection. |
| Selection is deterministic Python (AP-1): notes are ranked by how many of the question's terms appear in their title, tags, file name and body, and packed best-first into `knowledge.token_budget` (default 6,000 tokens per call). When the whole vault fits, the whole vault is sent | The model never lists or reads files itself. Reason on what the question touches, not on everything ever written. |
| The selected notes go to the clarifier and, appended to `Context`, to every member (FR-3.7). The synthesis call does not receive them | The synthesis reasons over the six assessments only, as before. |
| **One vault, several projects** (decided 9 September 2026). A page that belongs to one or more projects lists them in its front matter: `projects: [Dual DCDC]`. A page without the property is common to every project: the process, the roles, the guide. `knowledge.project`, chosen in Options from the vault's `kind: project` pages, names the project every question is about; the ranked selection and the KPI notes (3.4) then take only the common pages and the pages of that project, and every member's prompt states the project. Empty means every page is used. The project name also opens the file name of a project-specific page (`KPIs/Dual DCDC - Maturity Gates`), because Obsidian resolves a wikilink by file name and a second project will want a page of the same name | The process is common, the numbers are not: a second project's maturity gates must never reach this project's Hardware member. The property is the machine-readable scope and survives a rename; the file-name prefix keeps links unambiguous and the folders sorted by project. Folders stay by kind (`KPIs/`, `Projects/`, `Teams/`), not by project, so the guide's one rule per folder holds; a project that grows can get a subfolder under the kind folder without changing the property rule. |
| Every note the board writes carries YAML front matter: `title`, `tags` (always including `decision-board`), `created`, `source: decision-board` | The board's own notes stay findable in Obsidian's search and graph. |
| On **Close topic**, Alex is asked whether the decision should be remembered. Yes: one model call (`memory_proposal.md`) receives the topic, the synthesis, the conversation and an outline of the vault — folders and existing note titles — and proposes a path and a note. Alex sees exactly what would be written and where, edits path, title, tags and body, and confirms; only then is the file written | AP-4: propose, do not execute. "Show him what will be written and where" was the decision, and the proposal step is the small dedicated agent Alex asked for: it finds the spot in the network, Python writes. |
| A proposed path is validated in Python: inside the vault, a `.md` file, no traversal. Proposing an existing note's path appends a new section to that note; the board never overwrites | A note Alex wrote by hand is never replaced by one the board proposed. |
| The `memory/` folder in `.gitignore` stays reserved but is no longer where notes go; notes go into the vault, next to what they are about | The vault is outside this repository by construction. |
| Past topics are not listed in the interface | Alex's decision. The vault is the history; Obsidian is the browser for it. |

The board never writes to the vault on its own initiative — every write goes
through the confirmation step, and the proposal call has no file access.

**Cost.** One topic in the browser interface is one clarifier call, seven
board calls, one call per follow-up, and one memory-proposal call if Alex
says yes to remembering. Each call carries the fixed overhead described in
section 4 plus up to `knowledge.token_budget` tokens of notes for the
clarifier and member calls.

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
| Reading OIL, Jira, Confluence, SharePoint, Outlook or Teams | This repository has no connector code. Anything the board should know is in the vault (section 5) or is typed into the question by Alex (FR-3.7, 3.2). |
| Adaptive prioritisation, the import flow, the progress-check engine, the email briefing | These are TR-1/TR-2/TR-3 of the source project and never belonged to the board. |
| Model tiering across task types | The source project's `Config` names five task types with different model classes; this repository has exactly one task type (`ai_board`) and one configuration key (`provider.models.board`). |
| Session resumption across separate invocations, and a list of past topics in the interface | The one open question phase 7 of the source project left behind, and answered on 8 September 2026 by section 5 rather than by a session store: what was worth keeping goes into a note in the vault; the rest was not meant to survive the process exiting anyway. Alex decided against a past-topics sidebar. |
| Reaching the browser interface from another machine | Local only (section 9). The vault's content and every prompt would otherwise cross the network. |

## 9. Browser interface

**Decided 8 September 2026** in answer to seventeen questions; the answers
are recorded here so the draft they came from (`docs/hmi-spec-draft.md`)
could be deleted. Built in `server.py` and `web/`.

### 9.1 Decisions

| # | Decision |
|---|---|
| 1 | One text box. The clarifier (FR-3.2) extracts topic, context, options and constraints and shows them for confirmation; the four are editable before the board runs. All six individual answers are kept and shown; the synthesis is shown first. |
| 2 | Always at least one clarification question. |
| 3 | The clarifier is one call in front of the board, independent of the members. Its questions never reach them; the members get only the aligned input. |
| 4 | Simple icons as avatars: a dollar sign for Finance, a chip for HW Engineering, a gear for Mechanical Engineering, a factory for Manufacturing, code brackets for SW Engineering, a target for KPI Check, a check mark for the synthesis. Inline SVG, no image files. |
| 5 | Close topic asks whether to write to memory, shows what would be written and where, and writes only on confirmation (section 5). |
| 6 | No list of past topics. |
| 7 | English throughout (LN-1). |
| 8 | Alex selects the knowledge source: a folder, in his case an Obsidian vault on an offline-synced OneDrive folder. The board reads it and writes to it after approval. |
| 10 | No backup file. A source that cannot be read is an error. |
| 11 | The whole vault. |
| 12 | 6,000 tokens of notes per call, configurable. |
| 13 | Dependencies are allowed if they run on any company machine. None is needed: the server is `http.server`, the page is one HTML file with plain JavaScript and CSS, no build step. |
| 14 | The folder picker is the native folder dialog, opened through tkinter in a separate Python process; on Windows that is the Explorer folder dialog. If tkinter is missing, the path is typed. |
| 15 | Options holds the knowledge source, the token budget, the model string, the token limit, the audit folder, auto-approve and the theme, and writes `config.local.json` on save. |
| 16 | Local only: the server binds to 127.0.0.1 and nothing else, so there is no login. |
| 17 | The CLI `board` command stays, unchanged. Both front ends call the same `run_board`. |

### 9.2 Flow

```
Greeting, one text box
  -> clarifying        knowledge selected (section 5), one clarifier call (FR-3.2)
  -> questions         at least one; answers optional
  -> confirm           topic / context / options / constraints, editable
  -> running           six avatars fill in as members return (FR-3.3a)
  -> synthesising      the seventh call
  -> result            synthesis first; click a member's icon to read its
                       view, risks and recommendation; disagreements listed
  -> follow-ups        one call each, until Close topic
  -> close             "write to memory?" -> proposal -> edit -> confirm -> written
```

**Decided 9 September 2026, after the first real runs on the company PC.**

| Rule | Reason |
|---|---|
| **Alex chooses who is asked.** The confirm screen lists every member with a ticked checkbox; unticked members are not called (`run` takes `members`, `run_board(members=...)`). Every follow-up has the same list, empty by default: ticked members are asked again, each in isolation with its earlier assessment, the recommendation so far and the conversation in front of it, and the synthesis answers over their new answers (`ask_follow_up_full`, one call per member plus one). Nobody ticked: the one-call form over the original assessments, as before | "Some changes are not applicable for all swim lanes." A follow-up that can go back to the members is what makes "what if the tooling is free?" answerable with new reasoning instead of a re-reading of old text. |
| **Clarification loops until clear**, up to `clarify.MAX_ROUNDS` (3). Round one always asks at least one question; from round two the clarifier sees every question and answer so far and returns an empty list with `clear: true` when nothing important is missing. "Ask the board now" skips further rounds; the round limit sends the question to the board regardless | One round was not enough when the first answers raised new points, and an endless loop is not clarification. Alex keeps the exit in his hand. |
| **A member may say the topic does not touch it**: `applies: false` in its JSON, with the facts in `view` (which responsibilities and measures it checked), no risks, a one-line recommendation. The synthesis gives such a member no vote and names it under `not_affected`; the interface shows the chip as n/a and the card with its reasons | A short answer is right when the topic is not the member's, but only with an argument - silence would look like agreement. |
| **Plain English, bullets.** Every prompt asks for short sentences and lines starting with "- " (at most five per field, at most 25 words each); risks come back one bullet per risk. The interface renders "- " lines as lists everywhere, including follow-ups, which now answer in a JSON shape (`answer`, `reasons`, `recommendation_now`, `disagreements`) instead of prose | The first real answers were right but long and hard to read. The reader skims. |
| **A tool-only answer is retried once.** When a member call returns tool use and no text, the same prompt is sent once more with a prefix saying that no tools exist and only the JSON is wanted; the prompt itself now says so from the start | Seen on the first real run: Manufacturing called `glob` instead of answering. A retry is cheaper than a lost member. |
| **Every fact is labelled with where it came from, and Python checks the label.** A member returns `facts_from_network` (fact plus the note's path) and `own_judgement` separately. `board.verify_sources` accepts a citation only when the named note is one the board actually sent to that member (the ranked selection or its KPI note) and a distinctive word of the fact appears in it; anything else is kept and flagged, never dropped. The synthesis sees the verdicts, leans on verified facts first, and lists under `rests_on_judgement` the members' own statements its recommendation depends on. The result carries the union of verified notes, every failed citation, and the count of judgement statements; the interface shows them on each card and under the recommendation | "Make sure we know which information was based on our knowledge net and what was invented by AI." A model is a poor witness of its own sources, so the boundary is not left to it: the program knows what it sent, and a citation that does not match is a claim, not a fact. A two-pass form (one strictly network-bound call, one open call, merged) stays the fallback if a real model keeps mislabelling; it doubles the calls, so it is not the default. |
| **Nothing typed is lost to an error or a reload.** The browser remembers the current session's id and reloads it on page open (the server holds the session); everything typed - the question, the answers, the confirm edits and member ticks, a follow-up in progress - is kept as a draft per session in the browser until the topic is closed. A failed session has a "Go back, keep what I typed" action (`/api/sessions/<id>/back`): to the confirm screen when the input was already assembled, else to the last round of questions with its answers; the confirm screen's Back uses the same action. "Start over" keeps the question text | "If you want to go back because an error occurred you lose everything." The server had the data all along; the page just never asked for it back. |
| The page scrolls to the top only when the screen changes, not on every poll | Polling redraws the result screen once a second; the reader was pulled back up while the board was thinking. |

### 9.3 Setup

`scripts/setup.py` (`setup_wizard.py`) is the one entry point for a new
machine, and is deliberately not written for the machine it was first run on.
It asks once how the machine is set up and records the answer as
`setup.profile`:

| Profile | What it means |
|---|---|
| `company` | An IT-provided `opencode.json` points OpenCode at an internal gateway (AI-3). The file carries the key, so there is no login; its path is stored as `provider.opencode.config_file` and passed as `OPENCODE_CONFIG` (OC-8). The wizard reads the file, lists the provider/model strings it defines, asks the gateway which models it serves, and flags a placeholder key or a plain-`http` endpoint. |
| `private` | OpenCode logs in to a provider account of the user's own. The wizard offers to run `opencode auth login`, then picks a model from `opencode models`. The user is told plainly that the question and the selected notes leave the machine for that provider. |

Everything after that step is identical in both: knowledge source, token
budget, audit folder, port, theme, then two test calls — one word, and one
shaped like a real board call (the member prompt with the knowledge block,
JSON expected), because the first passes on prompts the board never sends.
The wizard installs nothing and changes nothing outside `config.local.json`,
except the OpenCode database it offers to rename when OpenCode's own schema
does not match its binary. Nothing in the shipped configuration names a
company, a person or a machine; `config.example.json` is empty placeholders.

### 9.4 Server

`decisionboard serve [--port N] [--no-browser]` starts `ThreadingHTTPServer`
on `127.0.0.1` (`server.port`, default 8765) and opens the default browser.
Every model call runs in a background thread; the page polls
`GET /api/sessions/<id>` once a second. `run_board` reports each member's
start and finish through an `on_member` callback that carries state only,
never text — a progress display does not weaken member isolation.

| Route | Purpose |
|---|---|
| `GET /` and `/static/*` | The page, its script and stylesheet, served from `web/`. |
| `GET`/`POST /api/config` | Read and update the options; a write saves `config.local.json`. |
| `POST /api/pick-folder` | Opens the native folder dialog; returns the chosen path or `null`. |
| `POST /api/sessions` | Start a topic from one question: knowledge selection plus the clarifier call. |
| `POST /api/sessions/<id>/answers`, `/run`, `/follow-up`, `/close`, `/memory`, `/discard-memory` | One step of the flow each; a step out of order is refused with 409. |

Sessions live in memory for the life of the process. The server writes
nothing to disk except the audit entry `run_board` already writes and the
one vault note Alex confirms.

## 10. Origin

Decision Board was TR-4 of the Program Lead Cockpit, a single-user automation
system for a Program Lead at Visteon Electronics running the MB32829 Gen6
Dual DCDC program. Decision 0005 of that project, dated 8 September 2026,
split the AI Board out into its own repository — this one — because decision
support on arbitrary topics is a different product from the Open Issue List
that the rest of that system tracks, even though the two share an AI layer.
A reader who meets a stray reference to `OIL`, `MB32829`, or a requirement ID
from a chapter other than the ones this document defines is looking at
something that predates the split; it is not part of this repository.
