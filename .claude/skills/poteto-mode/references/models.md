# Модели pstack (alpha)

Формат строки: `роль: провайдер модель уровень`. `inherit-parent` = модель текущего треда. Скорость всегда стандартная:
`--service-tier fast` только когда человек ждёт результат прямо сейчас (fast ест лимит быстрее).
Проверяющий (verifier, review) всегда из другого семейства, чем исполнитель юнита: делал codex — проверяет claude, и наоборот.
Уровень поднимать на шаг только для самой тонкой работы; рутина — на шаг-два ниже.

coordinator: inherit-parent
feature, refactoring: codex gpt-5.6-sol medium
bug-fix: codex gpt-5.6-sol medium
perf-issue: codex gpt-5.6-sol high
hillclimb: codex gpt-5.6-sol medium
hardest tasks: claude-code claude-opus-5-5 high
judgment and prose: claude-code claude-opus-5-5 high
review and verdicts: claude-code claude-opus-5-5 high
how explorer: claude-code claude-sonnet-5-5 medium
how explainer: claude-code claude-sonnet-5-5 medium
swarm workers: codex gpt-5.6-sol medium
swarm verifiers: claude-code claude-sonnet-5-5 high
arena runners: claude-code claude-opus-5-5 high, codex gpt-5.6-sol medium
arena cross-judge pool: claude-code claude-opus-5-5 high
architect runners: claude-code claude-opus-5-5 high, codex gpt-5.6-sol high
interrogate reviewers: claude-code claude-opus-5-5 high, codex gpt-5.6-sol high
trivial lookups: claude-code claude-haiku-5-5 low
