# Decision Board HMI — draft specification and open questions

Status: **draft for discussion**. Nothing in this document is built. It records
Alex's request of 8 September 2026 for a human interface and a knowledge
source, what the existing code and `docs/spec.md` already decide, the
defaults proposed for everything not yet decided, and the questions that
have to be answered before the defaults become the spec. Once answered, the
decisions move into `docs/spec.md` and this file is deleted.

## 1. What was asked

1. A modern, simple human interface: a greeting, one text box for the
   question, at least one clarification question back from the board before
   it answers, the consolidated answer from the synthesis ("agent 7"), the
   six individual answers readable on click with an avatar per member, and
   follow-up questions until the topic is closed.
2. A selectable knowledge source: primary path an Obsidian vault, backup a
   text or Markdown file, chosen through a file explorer, probably from an
   options menu.
3. A decision on the hosting platform: local HTTP, command line, Python, or
   something else.

## 2. What is already decided and stays

| Existing decision | Consequence for the HMI |
|---|---|
| Six members polled in isolation, then one synthesis call (FR-3.3a) | The interface shows seven results: one synthesis, six members. It never adds an eighth member call of its own. |
| A follow-up re-runs only the synthesis, one call (section 3.3) | "Ask back until the topic is closed" is the existing follow-up loop with a different front end. |
| Members may return `{"status": "questions", ...}` instead of an assessment (FR-3.2) | Today `_parse_member_response` rejects that shape and the member is counted as failed. The clarification step Alex asks for is the missing half of FR-3.2, not a new feature. |
| Memory is a folder of Markdown files, which is an Obsidian vault by construction (section 5) | "Obsidian vault or text file" is the same on-disk format. The new decision is which folder, and whether the board reads a vault Alex already keeps. |
| Python 3.11, standard library only, no `pip` dependencies (README) | Constrains the platform choice in section 4. |
| Deterministic before AI (AP-1); propose, do not execute (AP-4) | Note selection from the vault is Python, not the model. Nothing is written to the vault without a per-entry confirmation. |
| Audit trail per run (section 6) | Unchanged. A run started from the HMI logs exactly as a CLI run does. |

## 3. Proposed flow

```
Greeting
  └─ one text box: "What do you want the board to decide?"
       └─ Clarification round (1 model call)
            reads the question plus selected knowledge notes,
            extracts topic / context / options / constraints,
            asks 1..n questions about what is missing
       └─ Alex answers (free text per question, or "skip")
       └─ Board round (7 calls, unchanged)
            six avatars fill in as members return
       └─ Result screen
            synthesis card on top (agent 7)
            six avatar chips; click opens that member's view / risks / recommendation
            disagreements listed under the synthesis (FR-3.6)
       └─ Follow-up box (1 call each) ... until "Close topic"
       └─ Close topic
            board proposes memory entries; Alex confirms each one (section 5)
```

The clarification round is a single extra call in front of the board, not
six members each asking three questions. Six members could return up to
eighteen questions, most of them overlapping, and answering them one by one
would be the opposite of "simple". The clarifier consolidates; the members
still keep their FR-3.2 right to return questions instead of an assessment,
and when one does, the HMI shows that member's questions in its avatar card
and offers a re-poll of that member only.

## 4. Platform proposal

**Local HTTP server from the Python standard library, one HTML page, opened
in the default browser.** `http.server` on `127.0.0.1` only, a single
`index.html` with vanilla JavaScript and CSS, no build step, no framework,
no pip dependency. Started with `python scripts/run_board.py serve`. The CLI
`board` command stays as it is.

Why this and not the alternatives:

| Option | Verdict |
|---|---|
| Stdlib HTTP + single HTML page | Keeps the no-dependency rule. Modern look is CSS, not a framework. Runs on the Windows machine the config paths point at. |
| FastAPI or Flask + React | Better tooling, but breaks the no-dependency rule and adds a Node build step for a single-user tool. |
| Terminal UI (Textual, Rich) | Adds a dependency and cannot do avatars, cards or a folder picker well. |
| Desktop wrapper (pywebview, Tauri, Electron) | Adds a dependency or a second toolchain. Can be added later on top of the same HTML page if a window without browser chrome is wanted. |

**Folder picker.** A browser page cannot read a local folder path for
security reasons. The "explorer" is therefore a native dialog opened by the
server on request: `tkinter.filedialog.askdirectory` ships with Python on
Windows and is standard library. The chosen path is stored in
`config.local.json` and shown in the options menu.

**Progress.** Seven subprocess calls take tens of seconds. The page polls a
`/api/run/<id>` endpoint once a second and lights each avatar as its member
returns; no websockets, no server-sent events, nothing beyond `http.server`.

## 5. Knowledge source proposal

Options menu, section "Knowledge":

| Setting | Proposal |
|---|---|
| Primary: Obsidian vault folder | Read-only for the board's question phase. Python selects notes by front-matter `tags` and `title` overlap with the question, and by a full-text match on the question's nouns, capped by a configurable token budget (section 5 of the spec). |
| Backup: one text or Markdown file | Used in full when the vault folder is not set or not reachable (network drive offline). Also capped by the same token budget. |
| Board memory | The board's own notes (`memory/` in the spec) live in a sub-folder of the vault, e.g. `<vault>/Decision Board/`, so Obsidian shows them in the graph. Written only through the confirm-each-entry step on "Close topic". |
| Token budget | Default 6,000 tokens of notes per call, shown in the options menu. |

Selected notes are appended to the `Context` input that every member
already receives (FR-3.7), and to the clarifier call. The synthesis call
does not receive them: it reasons over the six assessments only, as today.

## 6. Open questions

Each question carries the default that applies if it is not answered. The
numbering is for the discussion; answers go into `docs/spec.md`.

### Interface

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | The current CLI asks for four fields: topic, context, options, constraints. Does the single text box replace them, with the clarifier extracting the four and asking about what is missing? Or should options and constraints stay as visible fields under the box? | Single text box; the clarifier extracts the four and shows them for confirmation before the board runs. |
| Q2 | "Minimum one question": must the board always ask at least one clarification question, even when the topic is clear? Or only when something is genuinely missing? | Always at least one. The clarifier is told to ask the one question whose answer would most change the recommendation, even for a clear topic. |
| Q3 | Who asks the clarification questions: one consolidated clarifier call in front of the board (one extra call, ~8,000 tokens overhead), or the six members individually per FR-3.2 (no extra call, but up to eighteen questions and a re-poll of the members that asked)? | One clarifier call. Members keep FR-3.2 as a fallback. |
| Q4 | Avatars: emoji, initials in a coloured circle, or drawn icons? And do the six keep their current names, or get personal names? | Initials in a coloured circle plus a one-word role label, current names. No image assets, nothing to load. |
| Q5 | What does "Close topic" do? Only end the conversation, or also trigger the memory proposal step from spec section 5, where the board proposes notes and Alex confirms each? | Both. Closing is the moment memory is proposed. |
| Q6 | Should past topics be listed in a sidebar and reopenable? That means saving the conversation to disk, which the spec today deliberately does not do. | Yes, saved as one Markdown note per topic in the board's memory sub-folder, so the vault is the history. Reopening shows the transcript read-only; a new question on an old topic is a new run. |
| Q7 | Language of the interface: English throughout per LN-1, or German with English prompts? | English. |

### Knowledge source

| # | Question | Default if unanswered |
|---|---|---|
| Q8 | Is the Obsidian vault a vault Alex already keeps and the board reads, or a new vault the board owns? | An existing vault, read-only, with the board's own notes in one sub-folder inside it. |
| Q9 | How are notes selected for a question: Python by tag and title overlap plus full-text match (AP-1), Alex picks notes by hand in the interface, or both? | Both: Python proposes, the interface shows the selected notes with a checkbox each before the board runs. |
| Q10 | "Backup text file": is that a fallback when the vault is unavailable, or a second source always read alongside the vault? | Fallback only. |
| Q11 | Should the whole vault be indexed, or only sub-folders Alex selects? Vaults with thousands of notes make full-text selection slow and noisy. | Whole vault, with an optional list of excluded folders in the options menu. |
| Q12 | Token budget for notes per call: 6,000 tokens? Each of the seven calls already carries ~8,000 tokens of fixed overhead, so the budget roughly doubles the cost of a round. | 6,000. |

### Platform and options

| # | Question | Default if unanswered |
|---|---|---|
| Q13 | Stdlib HTTP server plus one HTML page, keeping the no-dependency rule? Or is a dependency such as FastAPI acceptable for a better developer experience? | Stdlib only. |
| Q14 | Folder picker as a native tkinter dialog opened by the server, or a folder browser rendered inside the page that lists the server's file system? | tkinter dialog. |
| Q15 | What lives in the options menu: knowledge source, model string, token limits, audit folder, OpenCode auto-approve, theme? And should the menu write `config.local.json` directly? | All of those, written to `config.local.json` on save, with the file's `_comment` fields preserved. |
| Q16 | Localhost only, or reachable from other machines on the network? The vault contents and every prompt would go over that connection. | Localhost only, no authentication needed because of it. |
| Q17 | Does the CLI `board` command stay? | Yes, unchanged; both front ends call the same `run_board`. |

## 7. Not proposed

- No streaming of member text token by token. Members answer in one JSON
  object; there is nothing to stream until it is complete.
- No accounts, no multi-user, no remote deployment.
- No model-driven vault search. The model never lists or reads files; it
  receives what Python selected (AP-1).
- No automatic memory writes (AP-4).
