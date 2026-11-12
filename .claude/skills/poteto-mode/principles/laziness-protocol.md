---
name: principle-laziness-protocol
description: "Apply when refactoring, evaluating diff size, or tempted to add abstractions, layers, or signal threading. Bias toward deletion and the smallest change that solves the problem."
disable-model-invocation: true
---

# Laziness Protocol

Runtime: written for Cursor, running in bb. A pstack skill a step routes to is part of that step: read `.claude/skills/<name>/SKILL.md` in full before doing it. A subagent a step names is part of it too, however small the question: a second model chosen in `models.md` does or checks that work, so launch it as a bb child thread per `.claude/skills/poteto-mode/references/bb-runtime.md` and finish the step with its output. That file also maps questions, loops and transcripts.

- **Consolidate decisions.** Do not repeat the same choice in several places. Put it behind one source of truth and pass the result as a simple flag.
- **Minimize the diff.** Make the smallest change that solves the problem. Fewer lines beat "elegant" boilerplate.
