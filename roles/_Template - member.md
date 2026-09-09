---
kind: template
---
<!--
Copy this file, delete the two front-matter lines above, and name the copy
after the member: "R&R Hardware.md" or "Hardware.md" both give the member
"Hardware" (a leading "R&R", "Role" or "Roles" in the file name is dropped).

One file, one member. The official role description goes in verbatim under
"Official description" and is never rewritten for the board; what the board
needs beyond it is written below, in this same file.

Front matter: kind: role, lead_swimlane: <the member's name>, updated, and
"Part of Decision Board AI": true or false (a checkbox in Obsidian). false
keeps a role page in this folder without a seat on the board (a role tasks
link to, like Account Management, that is not a member).

The first heading is the member's title. The only key under it is "level":
1 for the programme lead, 2 for a project manager, 3 and below for roles
under them. The icon and colour are chosen by the program from the member's
name; nothing about presentation belongs in this file.

See roles/examples/ for eight complete files.
-->
---
kind: role
lead_swimlane: <Swim Lane>
Part of Decision Board AI: true
updated: 2026-09-09
---
# Project Manager <Swim Lane>
level: 2

## Official description

*Taken from the internal role description. Kept word for word; edit only when the official document changes.*

<paste the official text here, unchanged>

## Targets I am judged on

The measures, not the numbers. Baseline is MG0. The values - baseline,
target, current and estimate at completion, each with the date it was
recorded - live in the knowledge network, in the KPI note for this member
(`kind: kpi`, `affected_swimlanes: [<this member>]`).

- Resources: within the budget approved at MG0 - budget variance, estimate at completion.
- Expenses: within the budget approved at MG0 - budget variance, estimate at completion.
- Milestones: achieved on time - slip against the target date.
- <any measure specific to this lane, e.g. cBOM cost delta against the MG0 target>

## What I protect when I cannot have everything

The one sentence an official job description never contains, and the one
this board most needs. When cost, time, quality and scope collide, what does
this role defend to the last, and what does it let go first?

## Process

The process tasks that name this role, one link per task page, with the
owner as the process writes it. The board reads the task page for how the
work is done; the role page only points there.

- [[<Task page>]] - as <owner as written in the task>
