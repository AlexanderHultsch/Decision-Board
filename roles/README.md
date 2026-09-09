# Role profiles

The board is whoever has a filled role profile in the roles folder. There
is no member list anywhere else: add a note, and the board has a new
member; remove it, and it is gone. This repository ships **no members** -
only the two support files below, which the setup wizard (or Options)
copies into your roles folder.

The roles folder is one folder, chosen in Options or by the wizard. Inside
your vault, any folder whose name starts with "Roles" (`Roles`,
`Roles&Responsibilities`, ...) or reads "R&R" is found automatically.

| File | What it is |
|---|---|
| `_Board member conduct.md` | `kind: conduct`. How every member behaves: speaking for all its roles, aligning with its KPIs, checking the knowledge network first, character, how to answer. |
| `_Programme context.md` | `kind: conduct`. The facts of your programme: dates, what is fixed, how to weigh a trade-off, standing decisions, where the facts live. Fill it in. |
| `_Template - one member.md` | `kind: template`. The shape of a member note: official description kept verbatim, then the sections the board needs. Never a member itself. |
| `examples/` | Eight complete member notes: the swim lanes of one programme, official descriptions plus the board sections. The reference for the shape. |

**Every** note marked `kind: conduct` is prepended to every member's
profile, in file-name order, so common ground can be split across as many
files as you like.

**One file, one member.** The official role description is kept word for
word under `## Official description`, and everything the board needs beyond
it is written in the same file below it: the targets the role is judged on,
what it protects when it cannot have everything, how the lane usually fails,
what it decides alone, and its vocabulary. A second file naming the same
member is ignored and reported, never merged.

Notes whose name starts with `_`, or whose front matter says `kind:
conduct` or `kind: template`, are never members. A member note that has a
heading and nothing under it is "not filled yet": it is left off the board
and named on the confirm screen, so an empty note never produces an empty
opinion.

**One file is one member: a swim lane.** The file name is the member:
`R&R Hardware.md` and `Hardware.md` both give "Hardware" (a leading "R&R",
"Role" or "Roles" is dropped), and that name is what the interface shows
under the avatar. Inside the file, every level-one heading is one **role**
of that swim lane, and `level: N` directly under the heading is its rank:
1 the programme lead, 2 a project manager, 3 and up the roles below. The
member speaks as its highest-ranked role and weighs the others by rank when
they would disagree. The first sentence under the top role is the line the
synthesis sees. Front matter is optional:

```
---
member: Finance                     # which member this is (file name works too)
title: Finance                      # shown in the interface
perspective: one line ...           # what the synthesis sees about this member
order: 1                            # position on the board
icon: dollar                        # dollar chip gear factory code target scale people shield truck flask chart person
color: "#15803d"
short: Finance                      # label under the avatar
---
# Finance
...
```

```
# Project Manager HW
level: 2

Develop and maintain control over all the Program Management elements of
the HW swim lane ...

## Responsibilities
...

# HW Swim Lane Leader
level: 3

...
```

The whole file is given to that member, and only to that member, on every
call, behind the conduct note, roles in rank order. At least two filled
members are needed for a board.

**KPIs belong inside a member's own responsibilities**, not in a member of
their own: each swim lane is held to its own measures and judges every
option against them. There is deliberately no "KPI" board member.
