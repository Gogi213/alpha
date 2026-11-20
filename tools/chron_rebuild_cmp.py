"""chron_rebuild_cmp — пересборка летописи с нуля из md репозитория против копии прод-БД (TK-647): номер, «до счёта», дата раздела.

  python tools/chron_rebuild_cmp.py <копия прод-БД.sqlite> [--fix "причина"]

Разделы сопоставляются по (протокол, h, порядковый номер повтора). Расхождение номера / флага печатается; с --fix — поправки
на srv (`psec num`, `psec flag`; строки psec не меняются, поправки только добавляются). Копию прод-БД снять заранее
(sqlite backup), рабочее дерево — то, из которого шёл перенос.
"""
import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chron  # noqa: E402


def keyed(c):
    out, seen = {}, {}
    for r in c.execute("SELECT protocol, h, id, num, prereg, prereg_at FROM psec_cur ORDER BY id"):
        k = (r[0], r[1])
        seen[k] = seen.get(k, 0) + 1
        out[k + (seen[k],)] = tuple(r[2:])
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("prod")
    p.add_argument("--fix", metavar="REASON")
    a = p.parse_args(argv)
    root = Path(chron.repo_root())
    with tempfile.TemporaryDirectory() as tmp:
        st = chron.Store(chron.connect(os.path.join(tmp, "fresh.sqlite")))
        paths = list(chron.DEFAULT_SOURCES) + chron.protocol_files(root)
        kbn = chron.load_kb_nodes(root)
        st.import_docs(chron.build_docs(root, paths, kbn, chron.Git(root), chron.archive_map(root, paths)), [list(n) for n in kbn])
        fresh = keyed(st.c)
        st.c.close()
    prod = keyed(sqlite3.connect("file:%s?mode=ro" % Path(a.prod).as_posix(), uri=True))
    only_p, only_f = set(prod) - set(fresh), set(fresh) - set(prod)
    diff = []
    for k in sorted(set(prod) & set(fresh), key=lambda k: prod[k][0]):
        (pid, pn, pp, pa), (_, fn, fp, fa) = prod[k], fresh[k]
        if (pn, pp, pa) != (fn, fp, fa):
            diff.append((pid, (pn, pp, pa), (fn, fp, fa)))
            print("PS-%d прод %s пересборка %s" % (pid, (pn, pp, pa), (fn, fp, fa)))
    print("разделов: прод %d, пересборка %d, только в проде %d, только в пересборке %d, расхождений %d"
          % (len(prod), len(fresh), len(only_p), len(only_f), len(diff)))
    if a.fix:
        for pid, (pn, pp, pa), (fn, fp, fa) in diff:
            if fn != pn and fn:
                chron.call("num_psec", "PS-%d" % pid, fn, a.fix, chron.author())
            if (fp, fa) != (pp, pa):
                chron.call("flag_psec", "PS-%d" % pid, bool(fp), fa, a.fix, chron.author())
        print("поправок отправлено: %d разделов" % len(diff))
    return 1 if diff or only_p or only_f else 0


if __name__ == "__main__":
    sys.exit(main())
