"""П-42 §13.21 (TK-595): базы, список денежных клеток по стадиям, подсчёт испытаний, строки вариантов, хэши баз.

  python3 retitr_cells.py counts            # число денежных клеток по стадиям и сторонам (то, что стоит в §13.21)
  python3 retitr_cells.py bases             # точные строки баз и sha256 (то, что стоит в §13.21)
  python3 retitr_cells.py emit <L|S> <файл> # строки вариантов стадий E1, E-ls, NS, NT, T, P (E2 и F — по результату предыдущей стадии)
  python3 retitr_cells.py e2 <L|S> <S*> <файл>  # совместная сетка E2 вокруг выбранного S*

Клетка = одно изменение базы (оси «по одной»), кроме E2 (совместная сетка S × R × BE). Значение, равное значению базы, — контроль, не клетка.
Ключи движка v21 (новые: sn, sg, sb, sr, sd, tj, fa/fo/fv) — в протоколе §13.21.
"""
import hashlib
import sys

COMMON = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0"
BASES = {
    # основная база стороны (единственная, по которой идёт отбор)
    "L": COMMON + " rf=0.2 rs=20",
    "S": COMMON + " tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05",
}
CONTROLS = {  # помечены «контроль», не испытания, не участвуют в отборе
    "L_ow_e": COMMON,
    "S_ow_e": COMMON,
    "S_es15": COMMON + " e=0.15",
}
BM_BASE = {"L": 0, "S": 1}     # режим безубытка базы (bm): лонг — 0 (после 1-го тейка), шорт — 1 (выкл.)
S_GRID = [1, 1.5, 2, 3, 4, 5, 6, 8]  # 5 — база; 7 клеток
S_EXT = [0.5, 12]              # расширения за краем (одна клетка на край, если выбор на краю)
R_GRID = [1, 2, 3]             # 2 — база

STAGES = [  # (id, число клеток, описание)
    ("E1", 7, "жёсткий стоп S, % : 1, 1,5, 2, 3, 4, 6, 8 (база 5)"),
    ("E-ls", 1, "стоп за дальним краем зоны: ls = 1 NATR"),
    ("E2", 17, "совместная сетка S ∈ {соседи выбранного S* в E1, S*} × R ∈ {1, 2, 3} × BE ∈ {база, альтернатива} (18 клеток минус клетка, уже посчитанная в E1)"),
    ("NS", 5, "стоп в NATR, семейства: const k0=3; const k0=5; линейно по времени k0=3, β=1/ч; геометрически k0=3, ρ=1,5 (BE выкл.); шаг после тейка k0=3, δ=0,5 (BE выкл.)"),
    ("NT", 1, "тейки: шаг после каждого тейка δ=0,25 (оставшиеся ступени × (1+δ·j), перестановка со следующей сделки)"),
    ("T", 6, "тейки и выходы: t_high ∈ {3, 8}; q ∈ {25, 75}; rf ∈ {0,1; 0,3}"),
    ("P", 6, "пул: natr_min ∈ {0,3; 0,6}; t1h ∈ {1; 2} млн; t24 ∈ {100; 500} млн"),
    ("F", 15, "признаки: ≤ 5 признаков × 3 среза (отбросить худшие 10 / 20 / 25 % кандидатов сигнала)"),
    ("ext+asm", 2, "расширение S за край (1 клетка) + сборка кандидата (1 клетка)"),
]


def cells_simple():
    """Клетки стадий, которые не зависят от результатов: (id, строка ключей поверх базы)."""
    out = []
    for s in S_GRID:
        if s != 5:
            out.append(("E1_s%g" % s, "s=%g" % s))
    out.append(("Els_ls1", "ls=1"))
    ns = [("NS_c3", "sn=3 sg=0"), ("NS_c5", "sn=5 sg=0"), ("NS_lin", "sn=3 sg=1 sb=1"),
          ("NS_geo", "sn=3 sg=2 sr=1.5 bm=1"), ("NS_step", "sn=3 sg=3 sd=0.5 bm=1")]
    out += ns
    out.append(("NT_step", "tj=0.25"))
    out += [("T_th3", "t_high=3"), ("T_th8", "t_high=8"), ("T_q25", "q=25"), ("T_q75", "q=75"),
            ("T_rf01", "rf=0.1"), ("T_rf03", "rf=0.3")]
    out += [("P_n03", "natr_min=0.3"), ("P_n06", "natr_min=0.6"), ("P_t1", "t1h=1"), ("P_t2", "t1h=2"),
            ("P_t24_100", "t24=100"), ("P_t24_500", "t24=500")]
    return out


def e2_cells(side, s_star):
    i = S_GRID.index(s_star)
    ss = [S_GRID[j] for j in (i - 1, i, i + 1) if 0 <= j < len(S_GRID)]
    bm_alt = 1 if BM_BASE[side] == 0 else 0
    out = []
    for s in ss:
        for r in R_GRID:
            for bm in (BM_BASE[side], bm_alt):
                if s == s_star and r == 2 and bm == BM_BASE[side]:
                    continue  # посчитано в E1 (или база)
                out.append(("E2_s%g_r%g_bm%d" % (s, r, bm), "s=%g rr=%g bm=%d" % (s, r, bm)))
    return out


def line(side, cid, keys):
    return ("%s %s %s" % (cid, BASES[side], keys)).strip()


def counts():
    tot = 0
    for sid, n, d in STAGES:
        print("%-8s %3d  %s" % (sid, n, d))
        tot += n
    print("итого денежных клеток на сторону (верхняя граница):", tot)
    print("клеток по списку без E2/F/ext:", len(cells_simple()))
    for side in ("L", "S"):
        print("E2 при S*=3:", side, len(e2_cells(side, 3)), "при S*=5:", len(e2_cells(side, 5)), "при S*=8 (край):", len(e2_cells(side, 8)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "counts"
    if cmd == "counts":
        counts()
    elif cmd == "bases":
        for k, v in list(BASES.items()) + list(CONTROLS.items()):
            print(k, hashlib.sha256(v.encode()).hexdigest()[:16], v)
    elif cmd == "emit":
        side, path = sys.argv[2], sys.argv[3]
        rows = [line(side, "%s_base" % side, "")] + [line(side, c, k) for c, k in cells_simple()]
        open(path, "w", encoding="utf-8").write("\n".join(rows) + "\n")
        print(len(rows) - 1, "клеток + база")
    elif cmd == "e2":
        side, s_star, path = sys.argv[2], float(sys.argv[3]), sys.argv[4]
        cs = e2_cells(side, s_star)
        open(path, "w", encoding="utf-8").write("\n".join(line(side, c, k) for c, k in cs) + "\n")
        print(len(cs), "клеток E2")
