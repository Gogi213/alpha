# Модели pstack (alpha)

Формат строки: `роль: провайдер модель уровень`. `inherit-parent` = модель текущего треда. Скорость всегда стандартная:
`--service-tier fast` только когда человек ждёт результат прямо сейчас (fast ест лимит быстрее).
Codex не используется (владелец 12.11: «кодекса нет и не будет»), все треды — Claude. Проверяющий всегда на другой модели, чем исполнитель юнита: делал Sonnet — проверяет Opus; делал Opus — проверяет Fable (claude-fable-5-2).
Уровень поднимать на шаг только для самой тонкой работы; рутина — на шаг-два ниже.

coordinator: inherit-parent
feature, refactoring: claude-code claude-sonnet-5-5 high
bug-fix: claude-code claude-sonnet-5-5 high
perf-issue: claude-code claude-sonnet-5-5 high
hillclimb: claude-code claude-sonnet-5-5 high
hardest tasks: claude-code claude-opus-5-5 high
judgment and prose: claude-code claude-opus-5-5 high
review and verdicts: claude-code claude-opus-5-5 high
how explorer: claude-code claude-sonnet-5-5 medium
how explainer: claude-code claude-sonnet-5-5 medium
swarm workers: claude-code claude-sonnet-5-5 high
swarm verifiers: claude-code claude-opus-5-5 high
arena runners: claude-code claude-opus-5-5 high, claude-code claude-sonnet-5-5 high
arena cross-judge pool: claude-code claude-opus-5-5 high
architect runners: claude-code claude-opus-5-5 high, claude-code claude-sonnet-5-5 high
interrogate reviewers: claude-code claude-opus-5-5 high, claude-code claude-sonnet-5-5 high
trivial lookups: claude-code claude-haiku-5-5 low
