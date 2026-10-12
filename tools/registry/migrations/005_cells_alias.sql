-- TK-208 (В-225): cells/run_cells/results на каноническом хеше; прежние id клетки — в alias (JSON-список)
alter table cells add column alias text;
