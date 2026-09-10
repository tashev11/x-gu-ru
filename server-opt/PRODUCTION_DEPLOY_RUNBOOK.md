# x-gu.ru — production deploy runbook

Этот файл предназначен для оператора с SSH-доступом (включая Claude Code/Claude с доступом к серверу). Код из GitHub **не попадает на сервер автоматически**.

## Главное правило

Никогда не делать `git pull`, массовый patch или `cp -r` прямо в `/var/www/x-gu.ru/current`.

Нормальная схема:

```text
отдельный checkout tooling
  -> локальная валидация
  -> отдельный release-кандидат
  -> release policy + whitelist snapshot
  -> candidate-only изменения
  -> strict predeploy
  -> atomic current symlink switch
  -> проверка
  -> rollback при необходимости
```

Не использовать `--unsafe-*` при обычном деплое.

## 0. Сначала только read-only диагностика

Не выводить `.env`, токены, пароли и содержимое secret-файлов.

```bash
set -u

date
uname -a
python3 --version
git --version
df -h /

ls -ld /opt/p3-app || true
ls -ld /opt/p3-app/app/services || true
ls -ld /var/www/x-gu.ru || true
ls -ld /var/www/x-gu.ru/current || true
ls -ld /var/www/x-gu.ru/releases || true

if [ -L /var/www/x-gu.ru/current ]; then
  echo "current is symlink"
  readlink -f /var/www/x-gu.ru/current
else
  echo "STOP: current is not a symlink"
fi

git -C /opt/p3-app status --short --branch 2>/dev/null || true
git -C /opt/p3-app remote -v 2>/dev/null || true

test -s /opt/p3-app/data/index_policy.json && echo "index policy: present" || echo "index policy: MISSING"
test -s /opt/p3-app/data/whitelist.txt && echo "source whitelist: present" || echo "source whitelist: MISSING"
```

### Жёсткие stop-условия

Не продолжать production rollout, если выполняется хотя бы одно:

- `/var/www/x-gu.ru/current` существует, но не является symlink;
- `/opt/p3-app/app/services` отсутствует;
- reviewed `/opt/p3-app/data/index_policy.json` отсутствует;
- `/opt/p3-app/data/whitelist.txt` отсутствует;
- свободного диска недостаточно для копии текущего static release;
- есть непонятные незакоммиченные изменения, которые rollout может затронуть;
- невозможно запустить Python 3.11+;
- validator не проходит.

Если `current` пока является реальным каталогом, сначала нужен отдельный bootstrap-переход на release/symlink-модель. Не переименовывать live-каталог автоматически без отдельного плана и проверки Nginx root.

## 1. Отдельный checkout ветки tooling

Не использовать `/opt/p3-app` как checkout публичного tooling-репозитория.

```bash
TOOLING=/opt/x-gu-ru-tooling
BRANCH=fix/project-hardening

if [ -d "$TOOLING/.git" ]; then
  git -C "$TOOLING" fetch origin "$BRANCH"
  git -C "$TOOLING" checkout "$BRANCH"
  git -C "$TOOLING" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" --single-branch https://github.com/tashev11/x-gu-ru.git "$TOOLING"
fi

cd "$TOOLING"
git status --short --branch
git rev-parse HEAD
```

`reset --hard` выше разрешён только потому, что `/opt/x-gu-ru-tooling` — отдельный disposable checkout. Никогда не применять эту команду к `/opt/p3-app`.

## 2. Реально запустить validation suite на сервере

```bash
cd /opt/x-gu-ru-tooling
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install ruff
python scripts/validate_repo.py
```

Продолжать только при успешном завершении всех проверок.

## 3. Создать отдельный release-кандидат

Этот этап разрешён только если `current` уже symlink.

```bash
set -euo pipefail

CURRENT=/var/www/x-gu.ru/current
RELEASES=/var/www/x-gu.ru/releases
SOURCE="$(readlink -f "$CURRENT")"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="$RELEASES/$STAMP"

[ -d "$SOURCE" ]
[ -L "$CURRENT" ]
mkdir -p "$RELEASE"
cp -a "$SOURCE"/. "$RELEASE"/

echo "SOURCE=$SOURCE"
echo "RELEASE=$RELEASE"
```

После копирования `current` не изменён; сайт продолжает работать со старого release.

## 4. Применить reviewed index policy только к кандидату

```bash
cd /opt/x-gu-ru-tooling
. .venv/bin/activate

POLICY=/opt/p3-app/data/index_policy.json
SOURCE_WHITELIST=/opt/p3-app/data/whitelist.txt

python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST"
```

Сначала изучить dry-run counts. Если план ожидаемый:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST" \
  --apply
```

После apply внутри кандидата обязаны появиться:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
```

Дальнейшие runtime-проверки используют эти snapshots, а не глобальный source whitelist.

## 5. Preview установки hardened generator в private backend

```bash
cd /opt/x-gu-ru-tooling
. .venv/bin/activate
python server-opt/install_generator_facade.py
```

Проверить, что destination — `/opt/p3-app/app/services` и устанавливается ровно три файла.

Только после успешной validation suite и корректного preview:

```bash
python server-opt/install_generator_facade.py --apply
```

Installer создаёт backups и делает rollback уже заменённых файлов при partial failure.

## 6. Candidate-only cleanup / rerender

Минимально рекомендуется сначала посмотреть synthetic-proof cleanup:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
```

После проверки counts:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE" --apply
```

Если требуется ререндер открытого ядра:

```bash
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/rerender_open_hubs.py --root "$RELEASE" --apply
```

Если требуется полный hub/home rerender, сначала dry-run:

```bash
python server-opt/rerender_hubs_home.py --root "$RELEASE"
```

Не делать полный rerender, если dry-run сообщает missing city/service/homepage catalog entries.

## 7. Strict predeploy

```bash
python server-opt/predeploy_check.py "$RELEASE"
```

Продолжать только при `Pre-deploy check: OK`.

Также выполнить deploy helper без `--apply`; он повторно запускает строгий gate:

```bash
python server-opt/deploy_release.py "$RELEASE"
```

## 8. Атомарно переключить production

Только после успешных шагов выше:

```bash
python server-opt/deploy_release.py "$RELEASE" --apply
```

Команда печатает предыдущий release как `rollback target`.

Сразу сохранить это значение в журнале работ.

## 9. После переключения

```bash
readlink -f /var/www/x-gu.ru/current
SEOHC_ROOT=/var/www/x-gu.ru/current python /opt/x-gu-ru-tooling/seo_healthcheck.py
```

Локальная HTTPS-проверка через Nginx без зависимости от внешнего DNS:

```bash
curl --resolve x-gu.ru:443:127.0.0.1 -fsS -I https://x-gu.ru/
curl --resolve x-gu.ru:443:127.0.0.1 -fsS https://x-gu.ru/robots.txt | head -50
curl --resolve x-gu.ru:443:127.0.0.1 -fsS https://x-gu.ru/sitemap.xml | head -50
```

Проверить минимум:

- главная отдаёт 200;
- canonical host правильный;
- robots.txt доступен;
- sitemap.xml доступен;
- lead form/API не сломаны;
- одна открытая city/service page индексируема;
- одна закрытая page имеет `noindex`;
- `seo_healthcheck.py` проходит.

## 10. Rollback

Если после переключения обнаружена проблема, использовать `rollback target`, напечатанный deploy helper:

```bash
python /opt/x-gu-ru-tooling/server-opt/deploy_release.py \
  /var/www/x-gu.ru/releases/<previous-release> \
  --apply
```

Старый release содержит собственные `.xgu-index-keep.json` и `.xgu-whitelist.txt`, поэтому rollback возвращает согласованную HTML/SEO policy, а не смешивает старый HTML с новым whitelist.

## 11. Nginx — отдельный rollout

Не копировать Nginx-конфиг одновременно с первым application rollout без необходимости.

Перед изменением сохранить backup действующего конфига, сравнить его с `server-opt/nginx/`, затем обязательно:

```bash
sudo nginx -t
```

Только после успешной проверки:

```bash
sudo systemctl reload nginx
```

Если `/console` публично доступен, отдельно подтвердить backend authentication либо ограничить его VPN/IP/Nginx auth до публикации новой конфигурации.

## 12. Очистка старых releases

Не делать сразу после выкладки. После проверки стабильности:

```bash
python /opt/x-gu-ru-tooling/server-opt/prune_releases.py --keep 5
```

Изучить план, затем при необходимости:

```bash
python /opt/x-gu-ru-tooling/server-opt/prune_releases.py --keep 5 --apply
```

Active release перепроверяется непосредственно перед каждым удалением.

## Что оператор должен вернуть владельцу после работы

Без секретов и содержимого `.env`:

- SHA ветки, которая была проверена;
- результат `python scripts/validate_repo.py`;
- прежний `current` target;
- новый release path;
- результат dry-run/apply `shrink_index.py`;
- результат `predeploy_check.py`;
- результат `deploy_release.py`;
- новый `current` target;
- результат post-deploy healthcheck/curl;
- rollback target;
- любые stop-условия или ошибки, если rollout не был выполнен.
