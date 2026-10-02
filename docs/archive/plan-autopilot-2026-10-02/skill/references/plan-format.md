# PLAN.md structure

Every section is required. An empty section is a finding, not an omission — leave the header
with `TBD` so the critic can see the hole.

```markdown
# <task>

## Goal
The end state, as a condition someone else could check. Not the work to be done.
Bad:  "refactor the auth module"
Good: "auth module passes existing tests, no function over 30 lines, async/await throughout"

## Out of scope
Explicit list. What a reader might reasonably assume is included but isn't.

## Decisions
| # | Fork | Chosen | Why | Rejected |
Every fork that was actually a fork. If there was only one way to do it, it doesn't belong here.

## Assumptions
[ASSUMPTION H1] <claim> — provisional default for open question H1, unconfirmed.
Anything not backed by a file:line or a user answer lives here, tagged, so it stays visible.

## Steps
Numbered. Each one:
  - what changes (files, roughly)
  - done-condition: a command, test, or observable state
  - depends on: step numbers
A step whose done-condition is prose is not a step yet.

## Contracts touched
Interfaces, schemas, public signatures that change, and their known callers.

## Data and state
Migrations, backfills, caches, anything git revert won't undo. "None" is a valid answer
but must be written.

## Failure and rollback
What a mid-sequence failure leaves behind, and the way back.

## Verification
How the whole thing is checked once assembled, beyond the per-step conditions.
```

Length is not the goal. A plan that survives eight axes of critique in two pages beats six
pages of narrative. If a section says nothing, it should be one line.
