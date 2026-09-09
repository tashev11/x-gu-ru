#!/bin/sh
# Удаление мусора на x-gu.ru. Ничего из контента сайта не трогает.
# Запуск: ssh khaki 'sh /opt/p3-app/scripts/cleanup_junk.sh'
set -u

echo "=== ДО ==="
df -h / | tail -1

# 1. Логи SSH-брутфорса (21M + 12M). Обрезаем, файл остаётся живым.
rm -f /var/log/auth.log.1
: > /var/log/auth.log

# 2. Резервные копии файлов, которые правились 14-17 августа.
#    Сайт две недели работает на новых версиях — бэкапы больше не нужны.
#    Самый свежий бэкап конфига nginx намеренно оставлен.
rm -f /opt/p3-app/app/services/*.bak.*
rm -f /opt/p3-app/app/templates/*.bak.*
rm -f /var/www/x-gu.ru/current/sitemap.xml.bak.*
rm -f /var/www/x-gu.ru/current/sitemaps/*.bak.*
rm -f /var/www/x-gu.ru/current/privacy/index.html.bak.*
rm -f /etc/nginx/sites-available/x-gu.ru.conf.bak.20260614-214135

# 3. Системная статистика и посторонний лог.
rm -rf /var/log/sysstat/*
rm -f /var/log/geoengine-sync-x-gu.log

# 4. Сжатые логи nginx старше двух суток.
find /var/log/nginx -name '*.gz' -mtime +2 -delete 2>/dev/null

# 5. Кэш Python и временные файлы.
find /opt/p3-app -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
rm -f /tmp/seo_*.log /tmp/shrink*.log /tmp/empty_*.log /tmp/empty_dirs.json
rm -f /tmp/patch_landing.log /tmp/inject_widget.log /tmp/rerender.log

# 6. Системные кэши.
journalctl --vacuum-size=8M >/dev/null 2>&1
apt-get clean 2>/dev/null

echo "=== ПОСЛЕ ==="
df -h / | tail -1
