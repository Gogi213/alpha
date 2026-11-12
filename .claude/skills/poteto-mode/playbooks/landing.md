### Landing

Invoked at the end of every other playbook. alpha has no pull requests: trunk is `master`, one branch per topic.

1. **Branch and worktree.** Work on a topic branch in its own worktree (`.claude/worktrees/<name>`, or `bb thread spawn --new-environment worktree`). One writer per branch.
2. **Commits.** Small and ordered, each landable. Stage only your files (`git add <files>`, never `-A`). Subject in Russian, short, imperative. End every commit message with the attribution line from the session reminder.
3. **Gate.** Locally (build server): only the changed module — `bash tools/vps-check.sh <worktree> test <module filter>` plus `cargo fmt --check`; never the full suite. Then push the branch: GitHub CI (GC: fmt + lint + all tests; Quality: strict lints, lint budget, deps) runs on every branch push and is the full gate (owner 12.11). Land only when both are green on the branch head (`gh run list --branch <name>`). Speedups also need the byte-for-byte gate against the previous binary.
4. **Independent verdict.** A verifier thread on a different model than the worker (models.md `review and verdicts`) reads the diff and the gate output and says land or return, with file:line evidence.
5. **Merge.** Only after green CI on the branch head and a land verdict: merge into `master`, push. Finished branch gets tag `archive/<name>` and is deleted locally and on origin. Never delete `archive/*` tags, never force-push, never rewrite `master`.

**Report:** branch, head SHA, gate command and result, verifier verdict, merge SHA or why not merged.
