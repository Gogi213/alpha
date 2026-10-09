#!/bin/bash
s=$1; B=/opt/alpha-compute/bin/alpha-tk035-steps; r=/root/tk035new/$s
$B lob verify --symbol $s --root $r > /root/tk035new/vf-$s.log 2>&1; echo "rc=$?" >> /root/tk035new/vf-$s.log
$B lob verify --symbol $s --root $r --keep-going > /root/tk035new/vfk-$s.log 2>&1; echo "rc=$?" >> /root/tk035new/vfk-$s.log
