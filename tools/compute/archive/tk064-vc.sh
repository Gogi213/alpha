set -uo pipefail
export HOME=/root PATH=/root/.cargo/bin:$PATH
SRC=/opt/alpha-compute/wave2-srctk064vc; TGT=/opt/alpha-compute/target-tk064vc
rm -rf $SRC /opt/alpha-compute/tk064vc.out /opt/alpha-compute/tk064vc.done; mkdir -p $SRC && tar -xzf /opt/alpha-compute/tk064vc.tgz -C $SRC && cd $SRC
flock -w 14400 /opt/alpha-compute/.build.lock /opt/alpha-compute/sweep.sh run $TGT bash -c "cargo fmt --check 2>&1 | tail -10 && cargo clippy --release --target-dir $TGT --all-targets -j 3 -- -D warnings 2>&1 | tail -15 && cargo test --release --target-dir $TGT -j 3 2>&1 | grep -E \"^test result|FAILED|panicked|^error|^warning: unused\" | tail -40" > /opt/alpha-compute/tk064vc.out 2>&1
echo "rc=$?" >> /opt/alpha-compute/tk064vc.out; touch /opt/alpha-compute/tk064vc.done
