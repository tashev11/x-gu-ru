#!/bin/sh
# zram compressed swap setup (no disk usage). Idempotent.
set -e
modprobe zram num_devices=1 || true
# wait for sysfs node
for i in 1 2 3 4 5; do [ -e /sys/block/zram0 ] && break; sleep 1; done
# reset if already in use
swapoff /dev/zram0 2>/dev/null || true
echo 1 > /sys/block/zram0/reset 2>/dev/null || true
# algorithm MUST be set before disksize
echo lz4 > /sys/block/zram0/comp_algorithm 2>/dev/null || echo zstd > /sys/block/zram0/comp_algorithm
echo 512M > /sys/block/zram0/disksize
mkswap /dev/zram0
swapon -p 100 /dev/zram0
