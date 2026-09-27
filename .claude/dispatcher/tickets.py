"""CLI для тикетов диспетчера: `new`, `comment`, `start`, `status`. Только stdlib.

    python .claude/dispatcher/tickets.py new --owner researcher --title "..." [--desc "..."]  # reviewer: judge по умолчанию
    python .claude/dispatcher/tickets.py new --owner engineer --title "..." --no-reviewer     # явный отказ от ревью
    python .claude/dispatcher/tickets.py new --owner researcher --title "..." --backlog   # перенос из TASKS.md
    python .claude/dispatcher/tickets.py new --owner engineer --title "..." --budget L    # S=3/M=10/L=25, умолч. M
    python .claude/dispatcher/tickets.py comment TK-001 --author researcher --text "..."
    python .claude/dispatcher/tickets.py start TK-001                                     # backlog → todo
    python .claude/dispatcher/tickets.py status

Бюджет задачи (--budget) живёт только в state.json (dispatch.py), не в шапке тикета — роль его не
видит (владелец 27.09: «запрещено добивать задачи до их бюджетов, раздувая токены»).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch as D  # noqa: E402
import ticket as T  # noqa: E402

TICKETS_DIR = Path(__file__).resolve().parent.parent / "tickets"


PROJECT_ROOT = TICKETS_DIR.parent.parent


def cmd_new(args) -> int:
    reviewer = args.reviewer
    if reviewer is None and not args.no_reviewer and args.owner in ("researcher", "engineer"):
        # судья 27.09, п.6 «обязательно»: без ревьюера по умолчанию done молча минует проверку Судьи
        # (числа владельцу должны идти после Судьи) — отказ только явным --no-reviewer
        reviewer = "judge"
    path = T.create_ticket(TICKETS_DIR, owner=args.owner, title=args.title, reviewer=reviewer,
                            description=args.desc or "", wait_for=args.wait_for or "",
                            status="backlog" if args.backlog else "todo")
    tid = path.stem
    try:
        budget = D.parse_budget_arg(args.budget) if args.budget else D.DEFAULT_TICKET_BUDGET_USD
    except ValueError:
        print(f"--budget: не число и не S|M|L: {args.budget!r}", file=sys.stderr)
        return 1
    state = D.load_state()
    D.set_ticket_budget(state, tid, budget)
    D.save_state(state)
    try:
        print(path.relative_to(PROJECT_ROOT))
    except ValueError:
        print(path)
    return 0


def cmd_comment(args) -> int:
    path = TICKETS_DIR / f"{args.id}.md"
    if not path.exists():
        print(f"нет тикета {args.id}", file=sys.stderr)
        return 1
    T.append_log(path, args.author, args.text)
    print(f"дописано в {path}")
    return 0


def cmd_start(args) -> int:
    """backlog → todo: задача, перенесённая из TASKS.md, берётся в работу — диспетчер начинает её видеть."""
    path = TICKETS_DIR / f"{args.id}.md"
    if not path.exists():
        print(f"нет тикета {args.id}", file=sys.stderr)
        return 1
    tkt = T.read_ticket(path)
    if tkt.status != "backlog":
        print(f"{args.id}: status={tkt.status!r}, не backlog — не трогаю", file=sys.stderr)
        return 1
    T.write_header_updates(path, {"status": "todo"})
    print(f"{args.id}: backlog → todo")
    return 0


def cmd_status(args) -> int:
    state = D.load_state()
    rows = []
    for path in T.list_tickets(TICKETS_DIR):
        try:
            tkt = T.read_ticket(path)
        except Exception as e:
            rows.append((path.stem, f"<ошибка разбора: {e}>", "", "", "", "", ""))
            continue
        tid = tkt.id
        spent = D.ticket_cost_spent(state, tid)
        budget = D.ticket_budget_usd(state, tid)
        # владелец 27.09: бюджет и остаток роли не называть, но CEO — да; ${потрачено}/${бюджет}
        spent_col = f"${spent:.2f}/${budget:.2f}"
        rows.append((tid, tkt.header.get("title", "")[:40], tkt.owner, tkt.status,
                     tkt.reviewer, spent_col, tkt.header.get("updated", "")))
    if not rows:
        print("тикетов нет")
        return 0
    header = ("ид", "заголовок", "владелец", "статус", "ревьюер", "потрачено", "обновлён")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(7)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*header))
    for r in rows:
        print(fmt.format(*r))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="tickets.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new")
    p_new.add_argument("--owner", required=True, choices=["researcher", "engineer", "judge"])
    p_new.add_argument("--title", required=True)
    p_new.add_argument("--reviewer", choices=["researcher", "engineer", "judge"])
    p_new.add_argument("--no-reviewer", action="store_true",
                        help="явный отказ от умолчания --reviewer judge (researcher/engineer)")
    p_new.add_argument("--desc", default="")
    p_new.add_argument("--wait-for", dest="wait_for", default="")
    p_new.add_argument("--backlog", action="store_true",
                        help="создать сразу в backlog (перенос из TASKS.md) — диспетчер её не трогает до `start`")
    p_new.add_argument("--budget", default=None,
                        help="S=3/M=10/L=25 или число долларов; умолч. M; только в state.json, не в шапке")
    p_new.set_defaults(func=cmd_new)

    p_comment = sub.add_parser("comment")
    p_comment.add_argument("id")
    p_comment.add_argument("--author", required=True)
    p_comment.add_argument("--text", required=True)
    p_comment.set_defaults(func=cmd_comment)

    p_start = sub.add_parser("start")
    p_start.add_argument("id")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
