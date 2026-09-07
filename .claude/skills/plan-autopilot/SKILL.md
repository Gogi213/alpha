---
name: plan-autopilot
description: Drive a plan to reviewable quality autonomously by looping an adversarial critic against a planner until no blocking objections remain. Use whenever the user asks to plan a large feature, refactor or migration, says a task is too big, says the agent is flailing on scope, or asks for a spec, PRD or implementation plan before writing code. Also use when the user asks to "grill" a plan, stress-test a design, or wants planning to run unattended.
---

# Plan Autopilot

Converges `PLAN.md` by looping planner → critic → researcher until the critic returns zero
blocking objections. Human input is batched into one pass at the end, not scattered across
the loop.

## Non-negotiables

1. **Never write implementation code while this skill is active.** No source files, no
   migrations, no config. Only artifacts under `docs/plan/`.
2. **The critic runs as a subagent, always.** Critiquing in the main context is
   self-confirmation: you already believe the plan, so you will grade your own reasoning
   instead of the artifact. If subagents are unavailable, stop and tell the user the loop
   cannot run honestly.
3. **The exit condition is the critic's JSON, not your judgment.** You do not get to decide
   the plan is good enough.

## Artifacts

```
docs/plan/
├── PLAN.md              the plan itself
├── OPEN_QUESTIONS.md    questions only the user can answer
├── SETTLED.md           resolved objections, so the critic can't reopen them
└── critique-N.json      raw verdict from each pass
```

## Loop

### Pass 0 — draft

Read the task and enough of the repo to write a first `PLAN.md` against the structure in
`references/plan-format.md`. Do not polish it. A thin honest draft converges faster than a
thick speculative one — the critic works better against gaps than against filler.

Create `SETTLED.md` empty.

### Pass N — critique

Spawn the `plan-critic` subagent. Pass it exactly three things: the path to `PLAN.md`, the
path to `SETTLED.md`, and the original task statement. **Do not pass your reasoning, your
draft notes, or a summary of why you made a choice.** If the rationale is not in `PLAN.md`,
the critic is supposed to catch that.

It returns JSON:

```json
{
  "blocking":   [{"id": "B1", "axis": "verification", "issue": "...", "why_blocking": "..."}],
  "resolvable": [{"id": "R1", "question": "...", "where_to_look": "src/auth/"}],
  "human":      [{"id": "H1", "question": "...", "options": ["...", "..."], "default": "..."}],
  "verdict":    "revise" | "ready"
}
```

Write it to `docs/plan/critique-N.json`.

### Pass N — resolve

- For each `resolvable`: spawn `plan-researcher` with the question and the search hint. It
  reads the codebase and returns a factual answer or `UNKNOWN`. `UNKNOWN` gets promoted to
  `human`.
- For each `human`: append to `OPEN_QUESTIONS.md` with the critic's suggested default.
  **Do not interrupt the user.** Record the default as a provisional assumption in `PLAN.md`,
  tagged `[ASSUMPTION H1]`, and keep going.
- For each `blocking`: revise `PLAN.md`. Then append a line to `SETTLED.md`:
  `B1 | <objection in one line> | <how the plan now answers it>`.

### Termination

Stop when any of these hits:

- `verdict: "ready"` and `blocking` is empty → done.
- Iteration 5 → stop, report which blockers survived.
- **Stall**: two consecutive passes where no blocker id from the previous pass got settled →
  stop and report. A stall means the critic is objecting to something the plan cannot fix
  without a human decision. Say which one.
- **Churn**: the critic raises an objection already listed in `SETTLED.md` → do not revise.
  Reject it, note the collision, continue. Re-litigating settled points is how these loops
  spin forever.

### Final pass — the one interruption

Present `OPEN_QUESTIONS.md` to the user via AskUserQuestion, batched, with each provisional
default shown. Apply the answers, rerun one critique pass, then hand over the plan.

Report at the end: iterations used, blockers settled, assumptions still live.

## Calibration

Run the loop at the granularity of the task. A two-file change does not need five passes —
over-planning burns context and produces plans longer than the diff. If pass 1 returns fewer
than two blockers, the task is small; ship the plan.

If `OPEN_QUESTIONS.md` is still empty after three passes, the critic is not working. Real
tasks always contain decisions the codebase cannot answer. An empty file means the critic is
rubber-stamping, not that the plan is airtight.

## Reference files

- `references/plan-format.md` — required structure of `PLAN.md`

The eight review axes live in `.claude/agents/plan-critic.md`, not here — deliberately. If the
planner reads the rubric it writes to the rubric, and the critique stops finding anything.
