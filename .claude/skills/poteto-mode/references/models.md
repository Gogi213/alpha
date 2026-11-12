# Модели pstack (alpha)

Формат строки: `роль: провайдер модель уровень`. `inherit-parent` = модель текущего треда. Скорость всегда стандартная:
`--service-tier fast` только когда человек ждёт результат прямо сейчас (fast ест лимит быстрее).
Codex не используется (владелец 12.11: «кодекса нет и не будет»), все треды — Claude. Роли назначил владелец 12.11: координатор и проверяющий (бывший Судья) — Opus 5.5 medium; все исполнители — Sonnet 5.5 xhigh; чтение и разбор — Sonnet 5.5 medium; мелкие правки — Haiku 5.5 high. Исполнитель — всегда Sonnet, проверяющий — всегда Opus.
Уровни — как ниже, без самодеятельности.

coordinator: claude-code claude-opus-5-5 medium
feature, refactoring: claude-code claude-sonnet-5-5 xhigh
bug-fix: claude-code claude-sonnet-5-5 xhigh
perf-issue: claude-code claude-sonnet-5-5 xhigh
hillclimb: claude-code claude-sonnet-5-5 xhigh
hardest tasks: claude-code claude-sonnet-5-5 xhigh
judgment and prose: claude-code claude-opus-5-5 medium
review and verdicts: claude-code claude-opus-5-5 medium
how explorer: claude-code claude-sonnet-5-5 medium
how explainer: claude-code claude-sonnet-5-5 medium
swarm workers: claude-code claude-sonnet-5-5 xhigh
swarm verifiers: claude-code claude-opus-5-5 medium
arena runners: claude-code claude-sonnet-5-5 xhigh
arena cross-judge pool: claude-code claude-opus-5-5 medium
architect runners: claude-code claude-sonnet-5-5 xhigh
interrogate reviewers: claude-code claude-opus-5-5 medium
trivial edits and lookups: claude-code claude-haiku-5-5 high
