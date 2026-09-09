#!/bin/sh
# Threshold-triggered disk cleanup for a disk-constrained box (5.7G total).
# Runs daily via systemd timer; only cleans when free space is below threshold.
# Conservative by design: active logs and application data are never truncated.
set -eu

THRESHOLD_MB="${THRESHOLD_MB:-400}"
JOURNAL_DAYS="${JOURNAL_DAYS:-7d}"
JOURNAL_MAX="${JOURNAL_MAX:-50M}"
ROTATED_LOG_DAYS="${ROTATED_LOG_DAYS:-14}"
BACKUP_DAYS="${BACKUP_DAYS:-30}"
LOG="${DISK_AUTOCLEAN_LOG:-/var/log/disk-autoclean.log}"

avail_mb() {
    df -Pm / | awk 'NR==2 {print $4}'
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

# Preserve a useful incident-analysis window even during low-disk cleanup.
journalctl --vacuum-time="$JOURNAL_DAYS" >/dev/null 2>&1 || true
journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null 2>&1 || true

# Delete only old rotated/compressed logs. Never touch active *.log files.
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' \) \
    -mtime "+$ROTATED_LOG_DAYS" -delete 2>/dev/null || true

# Narrow rollback cleanup to known x-gu maintenance backup locations.
find /opt/p3-app/app/services -maxdepth 1 -type f -name '*.bak.*' \
    -mtime "+$BACKUP_DAYS" -delete 2>/dev/null || true
find /opt/p3-app/app/templates -maxdepth 1 -type f -name '*.bak.*' \
    -mtime "+$BACKUP_DAYS" -delete 2>/dev/null || true

after=$(avail_mb)
echo "$(date -Is) CLEANED avail=${before}M -> ${after}M (threshold ${THRESHOLD_MB}M)" >> "$LOG"

# Keep the cleaner's own log bounded.
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" || true
exit 0
