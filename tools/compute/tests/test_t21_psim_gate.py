#!/usr/bin/env python3
"""T-21: гейт «до = после» переноса правил портфеля `portfolio-sim.py` → `_lib/portfolio.py`
(`docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5) — прогоняет синтетическую сетку
`tools/compute/t21-psim-gate.py` и требует ноль DIFF. Полный побайтный гейт на реальных прогонах —
на Steam Deck, за пределами этого теста (`--real`)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "t21-psim-gate.py"


def load():
    spec = importlib.util.spec_from_file_location("t21_psim_gate", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_synthetic_gate_all_cases_match():
    gate = load()
    assert gate.run_synthetic(gate.PRE_PORT_REF) is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
