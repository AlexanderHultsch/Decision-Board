# Role profiles

One Markdown note per board member. These are the board's personality: who
each member is, what it knows, what it measures, how it talks, how it
assesses, and what it pushes back on.

The setup wizard copies these six files into `<your vault>/Roles/` when that
folder does not exist yet. From then on the copies in the vault are the ones
in force — edit them in Obsidian, and the next question already uses the
edited version. The files here are only the examples the wizard installs and
the fallback the board uses when a member has no note in the vault.

Each note carries front matter the board reads:

```
---
member: Finance                     # which member this note is for (file name works too)
title: Finance                      # shown in the interface
perspective: one line ...           # what the synthesis sees about this member
tags: [decision-board, role]
---
```

The body below the front matter is given to that member, and only to that
member, on every call. Keep the sections; change the content freely.
