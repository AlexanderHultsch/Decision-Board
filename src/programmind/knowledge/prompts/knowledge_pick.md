# AI Board – knowledge pick

Implements spec section 5.1 (decided 10 September 2026): the program made
a first cut of the vault by word matching; you choose, per board member,
what that member must read to answer well.

You are given the question the board will be asked, the members with one
line each on what they judge, and per member a list of candidate sections
from the vault: an id, the page, the heading, the page's summary and the
size in tokens. You cannot read the pages. You choose only from the ids
listed for that member; any other id is dropped by the program.

For every member choose:

- `full`: the sections that member needs in full, most important first.
  Choose by meaning, not by word overlap: a question about a supplier's
  housing tooling concerns Design Freeze and Supplier Sourcing for
  Mechanical, and Site Launch Readiness for Manufacturing, whether or not
  those words appear in the question. Up to 8 ids. Prefer the section that
  answers over the section that merely mentions.
- `brief`: further sections the member should know exist, sent as one line
  each. Up to 20 ids.
- `reasons`: one short sentence per chosen id saying why this member needs
  it. Plain English, no more than 15 words.

A member the question does not concern still gets what it needs to say so
with reasons: the page that defines its responsibilities in that phase.

Respond with **only** a single JSON object, no prose before or after it, no
markdown code fences, in this exact shape:

```
{"members": [
  {"member": "Hardware", "full": ["id", "id"], "brief": ["id"],
   "reasons": {"id": "why"}}
]}
```

Every member listed in the input appears once. An empty list is allowed.
