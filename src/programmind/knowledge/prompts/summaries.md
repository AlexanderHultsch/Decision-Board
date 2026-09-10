# AI Board – page summaries

Implements spec section 5.1 (decided 10 September 2026): every vault page
carries a two-line summary, written once by a model and marked as such,
so the knowledge pick can read what a page holds without reading the page
and a member can learn that a page exists in one line.

You are given several pages of an Obsidian vault: the path and the text of
each. For each page write a summary of at most two sentences and 45 words:
what the page is and what a reader finds on it. Plain English, no
markdown, no opinion, no words the page does not support. Name the roles,
tasks, gates or numbers the page carries when they are its point.

Respond with **only** a single JSON object, no prose before or after it, no
markdown code fences, mapping each path to its summary:

```
{"Process/VPDS/VPDS_Tasks/VPDS_Design Freeze.md": "...", "Projects/Dual DCDC.md": "..."}
```

Every path given appears once.
