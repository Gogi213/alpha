### Landing

Invoked at the end of every other playbook. alpha has no pull requests: trunk is `master`, one branch per topic.

1. **Branch and worktree.** Work on a topic branch in its own worktree (`.claude/worktrees/<name>`, or `bb thread spawn --new-environment worktree`). One writer per branch.
2. **Commits.** Small and ordered, each landable. Stage only your files (`git add <files>`, never `-A`). Subject in Russian, short, imperative. End every commit message with the attribution line from the session reminder.
3. **Gate.** `bash tools/vps-check.sh <worktree> all` (test + clippy -D warnings + fmt on the build server; never cargo on the owner's PC). Speedups also need the byte-for-byte gate against the previous binary. Paste the command and its result in the report.
4. **Independent verdict.** A verifier thread on a different model than the worker (models.md `review and verdicts`) reads the diff and the gate output and says land or return, with file:line evidence.
5. **Merge.** Only after a green gate and a land verdict: merge into `master`, push. Finished branch gets tag `archive/<name>` and is deleted locally and on origin. Never delete `archive/*` tags, never force-push, never rewrite `master`.

**Report:** branch, head SHA, gate command and result, verifier verdict, merge SHA or why not merged.
