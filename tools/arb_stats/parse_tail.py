"""Хвост книг одного символа с живого файла сбора (parse_inc.run), для окон, выходящих за конец разобранных книг.

Запуск: python3 parse_tail.py <биржа> <день> <файл book*.tsv.zst> <символ> <keep_from_rx_мс> <каталог вывода>
Пишет <вывод>/parsed/<биржа>/<символ>.<метка>.<номер>.npz (как этап A) и состояние в <вывод>/state; потом pair_look.py берёт оба каталога parsed через «:».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402

if __name__ == "__main__":
    ex, day, f, sym, kf, out = sys.argv[1:7]
    Path(out, "state").mkdir(parents=True, exist_ok=True)
    print(ex, sym, "kept", parse_inc.run(ex, day, f, {sym.encode(): sym}, out + "/state", out + "/parsed", int(kf)))
