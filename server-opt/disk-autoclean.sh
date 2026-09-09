#!/bin/sh
# Threshold-triggered disk cleanup for a disk-constrained box (5.7G total).
# Runs daily via systemd timer; only cleans when free space is below THRESHOLD_MB.
# Safe/idempotent: no package removal, no data deletion outside caches/logs.
set -e

THRESHOLD_MB=400
LOG=/var/log/disk-autoclean.log

avail_mb() {
    df -m / | awk 'NR==2 {print $4}'
}

before=$(avail_mb)

if [ "$before" -ge "$THRESHOLD_MB" ]; then
    echo "$(date -Is) OK avail=${before}M (>= ${THRESHOLD_MB}M) - skip" >> "$LOG"
    exit 0
fi

apt-get clean 2>/dev/null || true
find /var/cache/fwupd -type f -delete 2>/dev/null || true
find /var/cache/swcatalog -type f -delete 2>/dev/null || true
find /var/cache/apparmor -type f -delete 2>/dev/null || true
journalctl --vacuum-time=3d >/dev/null 2>&1 || true
journalctl --vacuum-size=15M >/dev/null 2>&1 || true
# stale rotated/compressed logs older than 7 days
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' \) -mtime +7 -delete 2>/dev/null || true
# our own scratch backups older than 14 days (server-opt patch scripts leave .bak.*)
find /opt/p3-app -maxdepth 3 -name '*.bak.*' -mtime +14 -delete 2>/dev/null || true

after=$(avail_mb)
echo "$(date -Is) CLEANED avail=${before}M -> ${after}M (threshold ${THRESHOLD_MB}M)" >> "$LOG"

# keep the log itself from growing unbounded
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" || true
exit 0
