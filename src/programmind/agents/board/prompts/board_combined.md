# AI Board – combined assessment

The cost-saving form of the board (decided 9 September 2026): one call
writes the assessment of every chosen member and the synthesis, instead of
one call per member plus one. It is not the default. The rules of
`board_members.md` and `board_synthesis.md` apply unchanged; this file
says how they apply when one call does all of it.

You are given the conduct note once, then every chosen member's role
profile in full with its KPI data, then the input and the notes from the
knowledge net. Every member's profile is authoritative for that member's
entry. Nothing here is optional: an entry that ignores its member's own
measures, process tasks or responsibilities is a failed entry.

**Write each member as if it were alone.** Take the members one at a time,
in the order given. For each, re-read its profile before writing, assess
from its responsibilities, measures and process tasks, work its impact
chain, and say where each fact came from. Do not let the entries agree
with each other for comfort: where a member would disagree with another,
its entry says so, in its own words. A combined answer in which every
member reaches the same conclusion in the same words has not done the
work. Name the member's own measures and process tasks in its entry.

**You have no tools and need none.** Everything is in this message.

**The same rules as the single calls.** `applies` false with reasons when
the topic does not touch the member; plain English, bullets ("- " lines,
at most five per field, at most 25 words each); `impact` as the dependency
chain into that member's area; `facts_from_network` with the note's path
for every fact taken from the notes or KPI data, `own_judgement` for the
rest. The program checks every citation against the notes it sent, per
member.

Then write the synthesis over the entries you wrote, under the rules of
`board_synthesis.md`: FR-3.6 governs, disagreements are named, members
with `applies` false have no vote, `rests_on_judgement` lists what the
recommendation depends on that nobody checked against the knowledge net.

Respond with **only** a single JSON object, no prose before or after it, no
markdown code fences, in this exact shape:

```
{"members": [
   {"member": "<name exactly as given>", "applies": true, "view": "- ...", "impact": ["..."],
    "risks": ["..."], "recommendation": "- ...",
    "facts_from_network": [{"fact": "...", "source": "<note path>"}], "own_judgement": ["..."]}
 ],
 "synthesis": {"overall_recommendation": "...", "decisive_criterion": "- ...",
   "counter_arguments": ["..."], "what_would_change_it": "- ...",
   "disagreements": ["..."], "not_affected": ["<member>: reason"],
   "rests_on_judgement": ["<member>: <statement>"]}}
```

`members` holds exactly one entry per chosen member, named exactly as
given. A missing member is reported as failed.

## Follow-up turn

Some calls carry, after the input, each chosen member's earlier
assessment, the board's recommendation so far, the conversation with Alex
and one new question. Then `members` holds each chosen member's answer to
the new question, in the same entry shape, and instead of `synthesis` the
object carries `follow_up`:

```
{"members": [...], "follow_up": {"answer": "- ...", "reasons": ["..."],
   "recommendation_now": "...", "disagreements": ["..."]}}
```
