---
name: principle-encode-lessons-in-structure
description: "Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction. Encode the rule as a lint, metadata flag, runtime check, or script instead of more text."
disable-model-invocation: true
---

# Encode Lessons in Structure

Runtime: written for Cursor, running in bb. A pstack skill a step routes to is part of that step: read `.claude/skills/<name>/SKILL.md` in full before doing it. A subagent a step names is part of it too, however small the question: a second model chosen in `models.md` does or checks that work, so launch it as a bb child thread per `.claude/skills/poteto-mode/references/bb-runtime.md` and finish the step with its output. That file also maps questions, loops and transcripts.

If the lesson needs judgment and cannot be encoded, make the instruction more prominent and add an example of the failure mode.

**Corollary:** If the fix is structural, only use the structural fix. The instruction is the symptom.
