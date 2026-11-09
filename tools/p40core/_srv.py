import sys, os
os.environ.setdefault("P40_DIR", "/root/p40core-tk516")
from gate import ssh
r = ssh(sys.argv[1])
sys.stdout.write(r.stdout.decode(errors="replace")); sys.stderr.write(r.stderr.decode(errors="replace")); sys.exit(r.returncode)
