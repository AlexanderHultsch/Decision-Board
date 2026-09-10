# AI Board – memory proposal

Implements spec section 5 (memory) as decided on 8 September 2026: when a
topic is closed and Alex wants it remembered, one call proposes where in
his vault the note belongs and what it says. Nothing is written by this
call - Alex sees the proposal, edits it if he wants, and confirms.

You are given an outline of the vault (its folders and the existing notes
with their titles), the topic with its inputs, the board's synthesis, and
the follow-up conversation.

Propose one note:

- `path`: a vault-relative path ending in `.md`. Put the note where it
  belongs among the existing folders - next to the notes it is about. If
  the vault has no fitting place, use `Decision Board/<short title>.md`.
  Never propose a path outside the vault. Proposing the path of an
  existing note means the text is appended to that note as a new section.
- `title`: a short title for the note.
- `tags`: a few tags, lower-case, no `#`.
- `body`: the note in Markdown. Record the decision, the decisive
  criterion, the disagreements that were left standing, and what would
  change the recommendation. Link to existing notes with `[[Title]]`
  wikilinks only where a note with exactly that title exists in the
  outline. Keep it to what a reader six months from now needs; leave out
  the conversation's back-and-forth.

Respond with **only** a single JSON object, no prose before or after it, no
markdown code fences, in this exact shape:

```
{"path": "...", "title": "...", "tags": ["..."], "body": "..."}
```
