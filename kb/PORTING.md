# Перенос kb в другой репозиторий (в т. ч. macOS)

Нужен Python 3.8+ (на macOS — `python3`, Homebrew или системный), git. Внешних пакетов нет. FTS5 в sqlite проверка: `python3 -c "import sqlite3;sqlite3.connect(':memory:').execute('create virtual table t using fts5(a)')"` — если ошибка, `find` всё равно работает (медленнее, перебором).

## Что скопировать

```
tools/kb.py                 # весь инструмент
tools/tests/test_kb.py      # тесты (по желанию)
kb/kb.toml                  # настройки
kb/.gitignore               # .kb.sqlite, .site/
kb/_templates/              # шаблоны тела карточек (default.md, task.md)
kb/README.md                # правила письма — поправить под проект
```

Из этого репозитория не нужно: `kb/<тип>/*` (карточки), `.claude/hooks/kb_check.py` (хук Claude Code; нужен только там, где работают агенты Claude), `tools/kb_deploy.sh` (публикация на srv — привязана к нашей машине).

## Шаги

1. Скопировать файлы выше, в `kb/kb.toml` поправить `title`, при необходимости `types` и `statuses` (имена каталогов `kb/<тип>/` = типы; каталоги с `_` или `.` в начале карточками не считаются).
2. `python3 tools/kb.py new finding F-1 "первая карточка"` → заполнить; вторую карточку связать с первой (`links:` или `[[F-1]]`) — карточка без связей считается ошибкой `check` (смягчить: `orphans = "warn"` в `[check]`).
3. `python3 tools/kb.py check` → `find` → `show F-1`.
4. Хук на коммит: `python3 tools/kb.py install-hook` — кладёт в `.git/hooks/pre-commit` вызов `kb.py precommit` (проверка только если в коммит попал `kb/`; существующий pre-commit не затирается, блок добавляется в конец; при отсутствии python хук молча пропускается).
5. Сайт: `python3 tools/kb.py serve` (локально, `http://127.0.0.1:8095/`) или `python3 tools/kb.py site <каталог>` и положить каталог на любой статический хостинг (GitHub Pages, `rsync` на сервер). Страницы работают и по `file://`.

## Заметки

- Имена файлов с кириллицей на macOS (NFD) обрабатываются: id и имя файла сравниваются в NFC.
- Ссылка на файл репозитория — путь от корня (`docs/x.md`, `AGENTS.md`); `check` проверяет, что файл существует. Остальное в `[[…]]` — id карточки.
- Всё внутри `kb.py` — подмножество TOML и «YAML-подобный» front matter (скаляр, `[a, b]`, блок `- a`); вложенных структур нет.
