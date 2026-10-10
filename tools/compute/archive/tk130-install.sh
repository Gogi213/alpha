#!/bin/bash
# TK-130: команда на calc — пользователь team, node LTS, Claude Code, git, rust 1.93.1, complexipy, клон alpha, плагин.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
step(){ echo "=== $(date +%T) $*"; }
step apt
apt-get update -qq && apt-get install -y -qq git build-essential pkg-config libssl-dev curl ca-certificates xz-utils python3-venv python3-pip jq || exit 1
step user
id team >/dev/null 2>&1 || useradd -m -s /bin/bash team
step node
if [ ! -x /opt/node/bin/node ]; then
  V=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c 'import json,sys;print(next(x["version"] for x in json.load(sys.stdin) if x["lts"]))')
  echo "node $V"
  mkdir -p /opt/node && curl -fsSL "https://nodejs.org/dist/$V/node-$V-linux-x64.tar.xz" | tar -xJ -C /opt/node --strip-components=1 || exit 1
fi
ln -sf /opt/node/bin/node /usr/local/bin/node; ln -sf /opt/node/bin/npm /usr/local/bin/npm; ln -sf /opt/node/bin/npx /usr/local/bin/npx
step claude-code
/opt/node/bin/npm i -g @anthropic-ai/claude-code 2>&1 | tail -3
ln -sf /opt/node/bin/claude /usr/local/bin/claude
claude --version
step rust+complexipy+clone
sudo -u team -H bash -lc '
set -u
[ -x ~/.cargo/bin/rustup ] || curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.93.1 -c clippy -c rustfmt 2>&1 | tail -3
. ~/.cargo/env; rustc -V; cargo -V
python3 -m venv ~/venv && ~/venv/bin/pip -q install complexipy && ~/venv/bin/complexipy --version
git config --global user.name "George Stern"; git config --global user.email "1993georgiy@gmail.com"
[ -d ~/alpha/.git ] || git clone --branch master https://github.com/Gogi213/alpha.git ~/alpha 2>&1 | tail -2
git -C ~/alpha log --oneline -1
'
step plugin
sudo -u team -H bash -lc 'claude plugin marketplace add Gogi213/role-play-vibing 2>&1 | tail -3; claude plugin install role-play-vibing --scope project 2>&1 | tail -3; claude plugin list 2>&1 | tail -5'
step done
