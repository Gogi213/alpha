import datetime, pathlib, sys, time
until = datetime.datetime.fromisoformat(sys.argv[1]); since = sys.argv[2]
while datetime.datetime.now(until.tzinfo) < until:
    time.sleep(30)
d = pathlib.Path(r"C:/visual projects/alpha/.claude/dispatcher")
hits = [l.rstrip() for l in (d / "ceo-wake.log").read_text(encoding="utf-8", errors="replace").splitlines()
        if l[:19] >= since and ("deck" in l.lower() or "dispatcher-down" in l)]
(d / "tk077-deck-quiet.txt").write_text(f"since {since}: {len(hits)} deck/dispatcher-down\n" + "\n".join(hits), encoding="utf-8")
(d / "tk077-deck-quiet.flag").write_text("done", encoding="utf-8")
