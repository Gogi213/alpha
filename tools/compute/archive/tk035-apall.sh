#!/bin/bash
cd /root/tk035ap
xargs -a ap-list.txt -P ${P:-3} -L1 ./tk035-ap1.sh
touch AP_DONE
