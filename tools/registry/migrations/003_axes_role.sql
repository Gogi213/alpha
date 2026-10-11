-- TK-187: формат TK-188 — роль оси (tit титруется | fixed | pending) и движок (rust | python)
alter table hyp_axes add column role text;
alter table hyp_axes add column engine text;
