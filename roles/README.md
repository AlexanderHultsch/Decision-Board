# Role profiles

The board is whoever has a role profile in the roles folder. There is no
member list anywhere else: add a file, and the board has a new member;
remove one, and it is gone. A profile is the member's personality — who it
is, what it knows, what it measures, how it talks, how it assesses, what it
pushes back on.

The roles folder is one folder, chosen in Options or by the setup wizard.
By default it is `Roles/` inside your vault. The wizard copies these example
files there when the folder does not exist yet; from then on the copies in
your folder are the ones in force. Edit them in Obsidian and the next
question already uses the edited version. The files here are the examples
the wizard installs and the fallback the board uses when no roles folder is
configured at all.

**One file per role** — front matter the board reads, then the body:

```
---
member: Finance                     # which member this is (file name works too)
title: Finance                      # shown in the interface
perspective: one line ...           # what the synthesis sees about this member
order: 1                            # position on the board (optional)
icon: dollar                        # dollar chip gear factory code target scale people shield truck flask chart person
color: "#15803d"                    # optional
short: Finance                      # label under the avatar (optional)
tags: [decision-board, role]
---
```

**Or one file with several roles** — one level-one heading per member, with
the same keys as plain `key: value` lines directly under the heading:

```
# Finance
perspective: Cost, budget vs forecast vs actuals
icon: dollar

## Character
...

# Legal
perspective: Contract exposure and liability
icon: scale

## Character
...
```

The body under a member is given to that member, and only to that member,
on every call. Keep the sections; change the content freely. At least two
members are needed for a board.
