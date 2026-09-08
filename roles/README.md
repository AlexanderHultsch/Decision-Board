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
| `_Board member conduct.md` | `kind: conduct`. What is the same for every member: character, how to answer, how to speak for all the roles and responsibilities under one's wing. Prepended to every member's profile on every call. |
| `_Template - one member.md` | `kind: template`. The shape of a member note, with the optional keys. Never a member itself. |
| `examples/` | Real, complete member notes (the roles and responsibilities of one programme's swim lanes), as examples of the shape. Not read by the board. |

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
