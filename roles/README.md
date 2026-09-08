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

Notes whose name starts with `_`, or whose front matter says `kind:
conduct` or `kind: template`, are never members. A member note that has a
heading and nothing under it is "not filled yet": it is left off the board
and named on the confirm screen, so an empty note never produces an empty
opinion.

**One file per member** — optional front matter, then the body:

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

**Or one file with several members** — one level-one heading per member,
with the same keys as plain `key: value` lines directly under the heading.

The body under a member is given to that member, and only to that member,
on every call, behind the conduct note. At least two filled members are
needed for a board.
