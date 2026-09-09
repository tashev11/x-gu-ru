#!/bin/sh
# Conservative cleanup for x-gu.ru.
# Removes caches and old rotated files but preserves active auth/nginx logs
# and recent rollback material.
set -eu

echo "=== BEFORE ==="
df -h / | tail -1

# 1. Never truncate active authentication logs. Only remove old compressed
# rotations after 30 days; fail2ban/systemd journal data remains available.
find /var/log -maxdepth 1 -type f -name 'auth.log*.gz' -mtime +30 -delete 2>/dev/null || true

# 2. Keep recent patch/deploy backups for rollback; remove only stale copies.
find /opt/p3-app/app/services -maxdepth 1 -type f -name '*.bak.*' -mtime +30 -delete 2>/dev/null || true
find /opt/p3-app/app/templates -maxdepth 1 -type f -name '*.bak.*' -mtime +30 -delete 2>/dev/null || true
find /var/www/x-gu.ru/current -maxdepth 1 -type f -name 'sitemap.xml.bak.*' -mtime +30 -delete 2>/dev/null || true
find /var/www/x-gu.ru/current/sitemaps -maxdepth 1 -type f -name '*.bak.*' -mtime +30 -delete 2>/dev/null || true
find /var/www/x-gu.ru/current/privacy -maxdepth 1 -type f -name 'index.html.bak.*' -mtime +30 -delete 2>/dev/null || true

# 3. Keep a useful nginx history window instead of deleting after two days.
find /var/log/nginx -type f -name '*.gz' -mtime +14 -delete 2>/dev/null || true

# 4. Python caches and known temporary scratch files.
find /opt/p3-app -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -f /tmp/seo_*.log /tmp/shrink*.log /tmp/empty_*.log /tmp/empty_dirs.json
rm -f /tmp/patch_landing.log /tmp/inject_widget.log /tmp/rerender.log

# 5. Package/system caches. Preserve enough journal for incident analysis.
journalctl --vacuum-time=14d >/dev/null 2>&1 || true
journalctl --vacuum-size=100M >/dev/null 2>&1 || true
apt-get clean 2>/dev/null || true

echo "=== AFTER ==="
df -h / | tail -1
