---
id: srv-no-pkill
type: rule
status: done
title: "На srv — никаких pkill/killall по имени, только свой systemd-юнит"
date: 2026-11-20
links: [srv, AGENTS.md]
supersedes: []
---

`pkill -f rustfeat_run.py` 19.11 убил чужой счёт TK-599. Останавливать только свой юнит: `systemctl stop <свой юнит>`. Машина — [[srv]]; свод граблей — [[AGENTS.md]].
