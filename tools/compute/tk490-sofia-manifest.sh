#!/bin/bash
# манифест sha256 готовых файлов out/ (для сверки на Deck до удаления в Софии); дописывает только новые
O=/opt/alpha-pump-dl/out; cd $O
touch MANIFEST.sha256
while true; do
  find candles oi -type f ! -name '*.part' ! -name '*.tmp' -mmin +1 -printf './%p\n' 2>/dev/null | sort > /tmp/pumpdl-files.txt
  awk '{print $2}' MANIFEST.sha256 | sort > /tmp/pumpdl-have.txt
  comm -23 /tmp/pumpdl-files.txt /tmp/pumpdl-have.txt > /tmp/pumpdl-new.txt
  if [ -s /tmp/pumpdl-new.txt ]; then
    cp MANIFEST.sha256 MANIFEST.tmp; xargs -a /tmp/pumpdl-new.txt -d '\n' sha256sum >> MANIFEST.tmp; mv MANIFEST.tmp MANIFEST.sha256
  fi
  sleep 60
done
