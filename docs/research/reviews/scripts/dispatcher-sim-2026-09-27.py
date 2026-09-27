"""Симуляция решений диспетчера alpha на синтетических тикетах (Судья, TK-001, 2026-09-27).
Подтверждает дыры п.1–7 лога TK-001: петля на blocked, сирота in_progress, петля ревью (г),
done без ревьюера, флуд parse-error, таймаут при записанном прогрессе, самоупоминание.
Запуск: python docs/research/reviews/scripts/dispatcher-sim-2026-09-27.py  (пишет только в свой каталог)."""
import sys, pathlib, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, r"C:\visual projects\alpha\.claude\dispatcher")
import dispatch as D, ticket as T

S = pathlib.Path(__file__).parent
TD = S / ("tickets-%d" % int(time.time())); TD.mkdir()
for name in ("STATE_FILE", "RUNS_DIR", "RUNS_LOG", "CEO_INBOX", "CEO_WAKE_LOG"):
    setattr(D, name, TD / ("x_" + name))
D.TICKETS_DIR = TD
tz = timezone(timedelta(hours=4))
t0 = datetime(2026, 9, 27, 22, 0, 0, tzinfo=tz)


def mk(tid, hdr, log=""):
    p = TD / f"{tid}.md"
    p.write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in hdr.items()) + "\n---\n\nописание\n\n## Лог\n" + log,
                 encoding="utf-8")
    return p


def iso(dt):
    return dt.isoformat(timespec="seconds")


print("== (1) blocked ticket after dispatcher's own blocked entry")
p = mk("X1", dict(id="X1", title="t", owner="researcher", status="blocked", updated=iso(t0)))
state = {"sessions": {"X1::researcher": {"last_woken": iso(t0 - timedelta(minutes=5))}}}
T.append_log(p, "dispatcher", "Запуск роли @researcher — дважды не оставил запись в «## Лог» — задача заблокирована, нужен @ceo.", now=t0)
print("   decide ->", D.decide(T.read_ticket(p), state, t0 + timedelta(minutes=2)))

print("== (2) in_progress, role exited after a logged step, no mention")
p = mk("X2", dict(id="X2", title="t", owner="engineer", status="in_progress", updated=iso(t0)),
       f"### {iso(t0)} engineer\nсделал шаг 1, дальше шаг 2\n")
print("   decide ->", D.decide(T.read_ticket(p), {}, t0 + timedelta(hours=3)))

print("== (3) review: dispatcher set in_review; judge comments 'принято' then sets status: done")
p = mk("X3", dict(id="X3", title="t", owner="researcher", status="in_review", reviewer="judge", updated=iso(t0)))
t1 = t0 + timedelta(minutes=10)
T.append_log(p, "judge", "принято @ceo", now=t1)
T.write_header_updates(p, {"status": "done"}, now=t1 + timedelta(seconds=30))
state = {"sessions": {"X3::judge": {"last_woken": iso(t0)}}}
print("   decide ->", D.decide(T.read_ticket(p), state, t1 + timedelta(minutes=2)))
print("   (3b) same but judge leaves status in_review:")
p = mk("X3b", dict(id="X3b", title="t", owner="researcher", status="in_review", reviewer="judge", updated=iso(t0)))
T.append_log(p, "judge", "принято", now=t1)
print("   decide ->", D.decide(T.read_ticket(p), state, t1 + timedelta(minutes=2)))

print("== (4) done without reviewer: anyone notified?")
p = mk("X4", dict(id="X4", title="t", owner="researcher", status="done", updated=iso(t0)),
       f"### {iso(t0)} researcher\nготово, числа: KPI 0,097\n")
st = {}
D.handle_ceo_mentions(T.read_ticket(p), st, t0)
D.notify_status_for_ceo(T.read_ticket(p), st, t0)
print("   decide ->", D.decide(T.read_ticket(p), st, t0), "| ceo-inbox exists:", D.CEO_INBOX.exists())

print("== (5) parse-error flood: 3 ticks with one broken ticket")
(TD / "X5.md").write_text("no header here\n", encoding="utf-8")
class FakeP0:
    pid = 88888

    def poll(self):
        return None


launched0 = []
D._popen = lambda cmd, **k: (launched0.append(cmd), FakeP0())[1]
for i in range(3):
    D.tick(t0 + timedelta(seconds=15 * i))
print("   ceo-inbox lines:", D.CEO_INBOX.read_text(encoding="utf-8").count("\n"))
print("   ceo-inbox:", D.CEO_INBOX.read_text(encoding="utf-8")[:600])
print("   launched by tick (tid/role):", [(c[2].split('.claude/tickets/')[1][:6]) for c in launched0])
D.RUNNING.clear()

print("== (6) timeout after the role DID log progress -> counted as failure?")
p = mk("X6", dict(id="X6", title="t", owner="engineer", status="in_progress", updated=iso(t0)))
T.append_log(p, "engineer", "сделал шаг 1 (артефакт a.csv), дальше шаг 2", now=t0 + timedelta(minutes=20))
launched = []


class FakeP:
    pid = 99999

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass


def fake_popen(cmd, **k):
    launched.append(cmd)
    return FakeP()


D._popen = fake_popen
D.RUNNING.clear()
D.RUNNING["X6"] = {"role": "engineer", "popen": FakeP(), "pid": 99999, "started": t0, "attempt": 0,
                   "run_file": TD / "x6.json", "err_file": TD / "x6.err", "out_fh": None, "err_fh": None,
                   "reason": "todo"}
(TD / "x6.json").write_text("", encoding="utf-8")
state = {}
D._poll_running(state, t0 + timedelta(minutes=41))
print("   retry launched:", len(launched), "| prompt tail:", launched[-1][2][-110:] if launched else None)
print("   ticket status now:", T.read_ticket(p).status)

print("== (7) mention of a role by itself (judge writes '@researcher' being judge; researcher writes '@researcher'?)")
p = mk("X7", dict(id="X7", title="t", owner="researcher", status="in_progress", updated=iso(t0)))
T.append_log(p, "researcher", "сделал; напоминание себе: @researcher завтра проверить", now=t0 + timedelta(minutes=5))
state = {"sessions": {"X7::researcher": {"last_woken": iso(t0)}}}
print("   decide ->", D.decide(T.read_ticket(p), state, t0 + timedelta(minutes=6)))
