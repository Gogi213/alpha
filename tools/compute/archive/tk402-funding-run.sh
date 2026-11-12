#!/bin/bash
cd /data/tk0402
python3 /data/tk0402/tk402-funding-fetch.py /data/tk0402/stage1.tsv /data/tk0402/funding459.json > /data/tk0402/funding.log 2>&1 && touch /data/tk0402/funding.done || touch /data/tk0402/funding.failed
