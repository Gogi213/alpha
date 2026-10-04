#!/bin/bash
# tk046-day.sh <сутки>: сутки месяца ($MON, по умолчанию aug; jall-<мес>-<сутки>.sh из дома TK-046) одним заданием; маркер .day-done-<сутки>
MON=${MON:-aug}; d=$1; export HOME=/data/tk046/$MON/home; E=$HOME/alpha/epochs/e-$MON; cd $E || exit 2
[ -e b5/.day-done-$d ] && exit 0
bash $HOME/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh > b5/.day-$d.out 2> b5/.day-$d.err || exit $?
touch b5/.day-done-$d
