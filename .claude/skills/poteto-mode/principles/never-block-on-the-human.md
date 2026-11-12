---
name: principle-never-block-on-the-human
description: "Apply when tempted to ask 'should I do X?' on reversible work."
disable-model-invocation: true
---

# Never Block on the Human

Runtime: written for Cursor, running in bb. A pstack skill a step routes to is part of that step: read `.claude/skills/<name>/SKILL.md` in full before doing it. A subagent a step names is part of it too, however small the question: a second model chosen in `models.md` does or checks that work, so launch it as a bb child thread per `.claude/skills/poteto-mode/references/bb-runtime.md` and finish the step with its output. That file also maps questions, loops and transcripts.

The human supervises asynchronously. Agents must stay unblocked. Make reasonable decisions, proceed, and let the human course-correct after the fact. Since code changes are reversible and reviewable, a wrong decision usually costs less than blocking.

- Proceed, then present. Do the work, show the result. Don't ask "should I do X?" Do X, explain why.
- Make the system self-healing. When you notice a problem, log it and fix it in the next round.
- Irreversible actions (force-push, delete production data, send external messages) still require confirmation.
- Reversible actions (write code, edit notes, split tasks) should proceed without blocking.
- Product direction comes from the human. Execution should not block.
