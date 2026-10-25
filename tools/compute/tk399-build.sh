#!/usr/bin/env bash
# TK-399: сборка снимка головы на calc (обёртка над gate/calc-build.sh): tk399-build.sh <base|new> — /data/tk0399/<v> (push-head.sh).
set -uo pipefail
W=/data/tk0399; v=${1:?base|new}
CALC_EXPECT=$(cat "$W/$v/HEAD") JOBS=${JOBS:-3} exec bash "$W/calc-build.sh" "$W/$v"
