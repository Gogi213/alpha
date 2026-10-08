import subprocess, sys, time, datetime, pathlib
until = datetime.datetime.fromisoformat(sys.argv[1])
while datetime.datetime.now(until.tzinfo) < until:
    time.sleep(60)
d = pathlib.Path(r"C:/Users/Георгий/.claude/plugins/cache/role-play-vibing/role-play-vibing/1.8.2/.claude/dispatcher")
out = subprocess.run([sys.executable, str(d / "doctor.py"), "--project", r"C:/visual projects/alpha"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
p = pathlib.Path(r"C:/visual projects/alpha/.claude/dispatcher/tk077-day-check.txt")
p.write_text(out, encoding="utf-8")
p.with_suffix(".flag").write_text("done " + datetime.datetime.now().isoformat(), encoding="utf-8")
