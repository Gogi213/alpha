---
name: poteto-mode
description: "Engineering work in alpha: bug fix, feature, refactor, perf/speedup, investigation, long autonomous runs, orchestrating worker threads in bb. pstack playbooks adapted to this repo."
---

# Poteto mode (alpha)

Runtime: bb. The chat that receives this skill is the **coordinator**. Workers and verifiers are bb child threads, launched per `references/bb-runtime.md` with the models in `references/models.md`. Project rules: `AGENTS.md` at repo root. Owner's standing orders: `.claude/preferences.md`; paste it into every worker brief.

Paths below are relative to this file's directory. Bundled skills live at `../<name>/SKILL.md`. Principles live at `principles/<name>.md`.

## Token budget (read first)

Spend is the main cost of this setup. Defaults:

- Do small work yourself. Spawn a child thread only for independent work that is big enough to justify a fresh context (roughly > 15 min of work, or a separate branch, or an independent verdict). Never spawn a thread to answer a lookup you can do with one grep.
- Read a principle or bundled skill only when its trigger actually fires in this task. Do not preload them.
- Fan-out skills (`swarm`, `arena`, `architect` parallel runners, `interrogate` panels) only for a real design fork or a high-stakes change, or when the owner asks. Default is one worker plus one verifier.
- Briefs carry file pointers, not pasted file contents. Bulk output (logs, big diffs, run outputs) goes to a reader thread or `tail`/`grep`, never into the coordinator's context.
- Standard speed. No `fast` tier unless a person is blocked right now.

## Non-negotiables

- Nontrivial change or "are we sure?" → the **how** skill.
- A "which approach" fork that an experiment can settle → Prototype playbook, let the result decide. Ask the owner only product or preference calls no experiment can settle. Methodology numbers and research forks are not the owner's (В-53).
- Any code → name the data shape first (principle **model-the-domain**).
- Speedup, benchmark or a number you measured → the **benchmark-checklist** skill and principle **explain-the-number** before you report it. Speedups land only with the byte-for-byte gate (AGENTS.md).
- Trading conclusion (hypothesis, protocol, backtest, verdict) → the **alpha-research** skill. Its rules outrank this file.
- Long, autonomous or multi-phase work → a decision trail via the **show-me-your-work** skill.
- Broken skill mid-task → fix it in a separate commit. Do not block on it.

## Principles

Read the file for a principle only when you apply it. In the reply, name a principle only if it changed a decision.

**Core.** laziness-protocol (smallest change, bias to deletion) · foundational-thinking (types and data first) · redesign-from-first-principles (new requirement → redesign as if foundational) · attack-the-premise (two fixes on one premise failed → test the premise) · subtract-before-you-add · minimize-reader-load · outcome-oriented-execution · exhaust-the-design-space (novel decision → 2–3 prototypes) · build-the-lever (build the tool that does or proves it).

**Architecture.** model-the-domain · boundary-discipline · type-system-discipline · make-operations-idempotent (collectors and long runs survive restarts) · migrate-callers-then-delete-legacy-apis · separate-before-serializing-shared-state.

**Verification.** prove-it-works (check the real artifact, not a self-report) · fix-root-causes · sequence-verifiable-units · test-behavior-not-implementation · explain-the-number.

**Delegation.** guard-the-context-window · never-block-on-the-human (reversible work: proceed, report).

**Meta.** encode-lessons-in-structure (a repeated correction becomes a hook, lint, test or script).

## Autonomy

Work within the owner's task. Reversible local changes: do them and verify. Stop and ask before deleting data (raw datasets especially), changing access, restarting production units the owner did not name, or any purchase, publication or message to other people. Never create schedules or automations without an explicit request.

## Subagents and threads

- Launch workers and verifiers as bb child threads (`references/bb-runtime.md`). For a short in-session helper the Agent tool with `subagent_type: "poteto-agent"` is fine.
- Model per role from `references/models.md`. A unit's verifier is always a different model from its worker (all threads are Claude; Codex is not used).
- You own every worker's result. Read the diff and the gate output yourself; a worker's "done" is not evidence.
- Fresh thread for new work, with consolidated scope (original brief, later directives, prior report and branch). Resume an existing thread only when the work needs state that lives there (uncommitted changes, a running process).

## The brief

```
GOAL         one sentence, the outcome
SCOPE        paths it may write; paths it may not; its branch/worktree
CONTEXT      file pointers; upstream reports it depends on
ACCEPTANCE   checkable criteria, one per line
VERIFY       exact commands (tools/vps-check.sh, gate, test window) and known gotchas
TIMEBOX      cap; on expiry return partial findings and stop
FORBIDDEN    no merge to master, no force-push, no deleting data, nothing outside scope
REPORT       status, branch, head SHA, what you ran and its result, deviations
STANDING     .claude/preferences.md (pointer; threads read it)
```

Size the brief to the unit. A one-command unit gets a paragraph that still names goal, scope, verify command and report shape.

## Writing the reply

Owner reads Russian, business-first: result and number first, then meaning and next step. Every claim says whether it is measured, inferred or a guess. Never hand the owner a check you could run. Never fabricate a link or a number.

## Playbooks

Match the task and open `playbooks/<name>.md`. Put its steps into the todo list; a skipped step stays with `skip: <reason>`.

- **investigation.** Read-only question: how does X work, why Y, are we sure about Z.
- **bug-fix.** Reproduce, root-cause, fix with runtime evidence.
- **perf-issue.** A measured slowness, traced and improved against a baseline.
- **hillclimb.** Sustained improvement of one metric (throughput of a collector, speed of a compute kernel, WS load ladder): one change, one measurement, keep or revert, decision log.
- **runtime-forensics.** Diagnose a live symptom (leak, stall, lag) from instrumentation.
- **feature.** New or changed behavior from a named data shape.
- **refactoring.** Behavior-preserving restructure.
- **prototype.** Throwaway sketch to settle a fork by observing.
- **autonomous-run.** Drive one task to a predicate without stopping.
- **orchestrate.** A standing program (e.g. six exchange collectors) under one coordinator.
- **multi-phase-plan.** Work spanning phases or several branches.
- **session-pickup** / **pause-safely.** Resume or suspend in-flight work cleanly.
- **landing.** End of every playbook: gate, independent verdict, merge to master.

A large effort no playbook fits → the **figure-it-out** skill.
