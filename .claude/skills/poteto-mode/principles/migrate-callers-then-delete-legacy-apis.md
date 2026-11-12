---
name: principle-migrate-callers-then-delete-legacy-apis
description: "Apply when introducing a new internal API while old callers still exist. Migrate callers and delete the old API in the same wave instead of preserving compatibility layers."
disable-model-invocation: true
---

# Migrate Callers Then Delete Legacy APIs

Runtime: written for Cursor, running in bb. A pstack skill a step routes to is part of that step: read `.claude/skills/<name>/SKILL.md` in full before doing it. A subagent a step names is part of it too, however small the question: a second model chosen in `models.md` does or checks that work, so launch it as a bb child thread per `.claude/skills/poteto-mode/references/bb-runtime.md` and finish the step with its output. That file also maps questions, loops and transcripts.

- Update tests to assert the new contract, and delete tests that only protect pre-refactor implementation details

**When this applies:**
- No external users depend on backward compatibility
- The project can absorb coordinated breaking changes
- The new API is part of a simplification or refactor initiative
