---
name: principle-subtract-before-you-add
description: "Apply when sequencing an addition, refactor, or rewrite. Remove dead code, redundant validators, and stub references first, then build on the simpler base."
disable-model-invocation: true
---

# Subtract Before You Add

Runtime: written for Cursor, running in bb. A pstack skill a step routes to is part of that step: read `.claude/skills/<name>/SKILL.md` in full before doing it. A subagent a step names is part of it too, however small the question: a second model chosen in `models.md` does or checks that work, so launch it as a bb child thread per `.claude/skills/poteto-mode/references/bb-runtime.md` and finish the step with its output. That file also maps questions, loops and transcripts.

When evolving a system, remove complexity first, then build. Default to subtraction.

Make simplification a continual investment. Leave the design slightly simpler and more capable behind the same or smaller surface than you found it.

- Sequence removal before construction
- Cut before you polish (get to the minimum before investing in quality)
- Design for observed usage, not speculative edge cases
- No speculative validators, parsers, or guards beyond what the spec demands
- Simplify prompts (remove redundant instructions, excessive templates)
- When a reference has no novel content, delete it rather than leaving a stub
