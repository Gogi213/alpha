-- TK-460 (слой G плана TK-456, В-67): выжившие признаки смарт-мани (вердикт «подтверждено» по §3 п.3 на слоях L2/B2/S).
-- Только определение (def.* из словаря B1) + метка события + ссылка на файл событий; прогноза нет. Источник — smartmoney-survivors-*.csv
-- (tools/g017db/sm_survivors.py), строки к исходным таблицам вердиктов — verdict_file:verdict_rows.
CREATE TABLE sm_features (
  sm_id TEXT PRIMARY KEY, layer TEXT NOT NULL, test TEXT NOT NULL, row_label TEXT NOT NULL, def_ids TEXT NOT NULL, claim_ids TEXT NOT NULL,
  event_label TEXT NOT NULL, event_key TEXT NOT NULL, events_file TEXT NOT NULL, controls TEXT NOT NULL, n_rows INTEGER NOT NULL,
  delta_min REAL, p_holm_max REAL, verdict TEXT NOT NULL CHECK (verdict = 'подтверждено'), verdict_file TEXT NOT NULL, verdict_rows TEXT NOT NULL,
  verdict_md5 TEXT NOT NULL, source_file TEXT NOT NULL, src_row INTEGER NOT NULL, run_id INTEGER NOT NULL REFERENCES runs);
