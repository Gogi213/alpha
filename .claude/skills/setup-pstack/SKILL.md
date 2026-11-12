---
name: setup-pstack
description: Configure which bb model and reasoning effort each pstack role uses. Writes poteto-mode's models.md. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `.claude/skills/poteto-mode/references/models.md`, the file every pstack skill reads before it spawns a subagent. Its role names are the ones poteto-mode and the routed skills use; keep them verbatim. The bb mapping of a spawn is in `.claude/skills/poteto-mode/references/bb-runtime.md`.

## Steps

### 1. Detect available models

List each provider's catalog with `bb provider models <provider> --json` for `claude-code` and `codex`; each entry names its `id` and `supportedReasoningEfforts`. The catalog can lag behind the provider: a model that a recent thread requested without a `provider/modelFallback` event (`bb thread log <id> --json`) is available too. Never write a model you have not confirmed either way. `inherit-parent` and `auto` are always valid.

### 2. Load current state

Read `models.md`. Its `budget:` line and role values are the current choices. Check unfamiliar roles against the current pstack skills and history before treating them as retired. Preserve choices outside the requested change.

### 3. Resolve the requested model choices

**(a) Reuse the owner's choice.** Apply the budget, models and efforts already specified in the request. For a request to choose a budget without a stated preference, use the structured question tool, name the current budget and offer:

- `unlimited — max reasoning`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`
- `custom — per role group` (for example: Sol and Sonnet at medium, Opus at xhigh)

**(b) Apply it.** Start from the current file and preserve manual role choices outside the request. A budget sets the effort of every entry in its scope to its target. When a model lacks that effort, use its highest supported effort at or below the target, else mark the role as needing a choice. `inherit-parent` and `auto` do not change.

**(c) Resolve remaining choices.** Show every role with its proposed value and list any dropped lines. Ask only about unresolved preferences or an unavailable requested model, offering confirmed models plus `inherit-parent`. An already specified mapping needs no second approval. Panel roles (`arena runners`, `architect runners`, `interrogate reviewers`, `swarm verifiers`) take a list, one subagent per entry. `arena cross-judge pool` is a list Arena picks one entry from, preferring a family different from the parent's. `swarm workers` is the default for every swarm worker unless a race assigns models per arm.

### 4. Validate

Every value is `<provider>/<model> <effort>` with a model and effort confirmed in step 1, or `inherit-parent` / `auto`. Fix formatting errors yourself. An unavailable explicit model choice needs the owner's replacement choice.

### 5. Write the file

Overwrite `models.md` whole, keeping its header paragraph, a `budget:` line naming the choice, and one line per role in this order:

```
coordinator:
feature, refactoring:
bug-fix:
perf-issue:
hillclimb:
judgment and prose:
hardest tasks:
how explorer:
how explainer:
why investigators:
why synthesizer:
reflect tooling:
reflect judgment, divergent, synthesizer:
arena runners:
arena cross-judge pool:
swarm workers:
swarm verifiers:
architect runners:
interrogate reviewers:
review and verdicts:
```

Файл находится в текущем проекте. Проверь diff только `.claude/skills/poteto-mode/references/models.md` и покажи выбранные роли. Глобальную библиотеку, hook, другие устройства и git remote при настройке не меняй. Коммит и push выполняются только как отдельная явно разрешённая инженерная задача.

### 6. Confirm

Tell the user what changed and that new spawns use it at once; running children keep their model.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, read and follow `.claude/skills/create-verification-skill/SKILL.md`. On no, move on without pushing.

### 8. Offer a formal verification skill (optional)

Check whether the project already has a formal verification skill (a project skill that runs a model checker, a proof, or another verifier against the code). If not, offer once: "want a formal verification skill, so agents can prove a key property of your code holds in every case in scope, not only the cases tests reach? I can generate one with /create-formal-verification-skill." On yes, read and follow `.claude/skills/create-formal-verification-skill/SKILL.md`. On no, move on without pushing.
