# x-gu.ru — production deploy runbook

Этот файл предназначен для оператора с SSH-доступом (включая Claude Code/Claude с доступом к серверу). Код из GitHub **не попадает на сервер автоматически**.

## Главное правило

Никогда не делать `git pull`, массовый patch или `cp -r` прямо в `/var/www/x-gu.ru/current`.

Нормальная схема после первоначальной миграции:

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

# Проверить, что Nginx действительно смотрит в ожидаемый current path.
sudo nginx -T 2>/dev/null | grep -n '/var/www/x-gu.ru/current' || true
```

### Жёсткие stop-условия

Не продолжать обычный production rollout, если выполняется хотя бы одно:

- `/opt/p3-app/app/services` отсутствует;
- reviewed `/opt/p3-app/data/index_policy.json` отсутствует;
- `/opt/p3-app/data/whitelist.txt` отсутствует;
- есть непонятные незакоммиченные изменения, которые rollout может затронуть;
- невозможно запустить Python 3.11+;
- validator не проходит;
- Nginx root не соответствует ожидаемому `/var/www/x-gu.ru/current` и это не объяснено.

Если `/var/www/x-gu.ru/current` существует, но **не является symlink**, обычный deploy останавливается и используется только one-time bootstrap из раздела 3A ниже. Не переименовывать live-каталог вручную.

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

## 3A. One-time bootstrap, если `current` ещё обычный каталог

Этот путь используется **только один раз**. Если `current` уже symlink, пропустить раздел 3A и перейти к разделу 3B.

Сначала создать отдельный candidate-клон текущего static сайта. Сам live-каталог не менять:

```bash
set -euo pipefail

CURRENT=/var/www/x-gu.ru/current
RELEASES=/var/www/x-gu.ru/releases
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="$RELEASES/bootstrap-candidate-$STAMP"

[ -d "$CURRENT" ]
[ ! -L "$CURRENT" ]
mkdir -p "$RELEASES"
mkdir "$RELEASE"
cp -a "$CURRENT"/. "$RELEASE"/

echo "LEGACY_CURRENT=$CURRENT"
echo "BOOTSTRAP_CANDIDATE=$RELEASE"
```

Проверить свободное место до копирования. Если места недостаточно для отдельного candidate — остановиться, а не пытаться мигрировать live-каталог без rollback-копии.

Применить reviewed policy и source whitelist **только к candidate**:

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

После проверки dry-run counts:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST" \
  --apply
```

Очистить synthetic proof только в candidate:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python server-opt/sanitize_generated_proof.py --root "$RELEASE" --apply
```

Запустить strict predeploy:

```bash
python server-opt/predeploy_check.py "$RELEASE"
```

Продолжать только при `Pre-deploy check: OK`.

Теперь preview one-time cutover:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE"
```

Скрипт должен показать:

- legacy current directory;
- target release;
- будущий `pre-bootstrap-*` backup;
- `Bootstrap target predeploy: OK`;
- отсутствие изменений в dry-run.

Только в контролируемое окно и только после проверки preview:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE" --apply
```

Bootstrap:

1. не изменяет проверенный candidate;
2. переименовывает старый реальный `current` в `/var/www/x-gu.ru/releases/pre-bootstrap-*`;
3. ставит `current` symlink на проверенный candidate;
4. пытается автоматически вернуть legacy directory обратно в `current`, если установка symlink не удалась.

Сразу после bootstrap:

```bash
readlink -f /var/www/x-gu.ru/current
SEOHC_ROOT=/var/www/x-gu.ru/current python /opt/x-gu-ru-tooling/seo_healthcheck.py
curl --resolve x-gu.ru:443:127.0.0.1 -fsS -I https://x-gu.ru/
```

Сохранить путь `pre-bootstrap-*` как emergency legacy backup. Не удалять его при первом rollout.

После успешного bootstrap сервер уже находится на стандартной release/symlink-модели. Для следующего изменения использовать раздел 3B и далее.

## 3B. Создать обычный release-кандидат, если `current` уже symlink

```bash
set -euo pipefail

CURRENT=/var/www/x-gu.ru/current
RELEASES=/var/www/x-gu.ru/releases
SOURCE="$(readlink -f "$CURRENT")"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="$RELEASES/$STAMP"

[ -d "$SOURCE" ]
[ -L "$CURRENT" ]
mkdir -p "$RELEASES"
mkdir "$RELEASE"
cp -a "$SOURCE"/. "$RELEASE"/

echo "SOURCE=$SOURCE"
echo "RELEASE=$RELEASE"
```

После копирования `current` не изменён; сайт продолжает работать со старого release.

## 4. Применить reviewed index policy только к обычному кандидату

Если раздел 3A уже выполнил policy для bootstrap candidate, этот шаг повторять для него не нужно. Для обычного release из 3B:

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

Этот раздел используется после того, как `current` уже symlink. Для первоначального перехода из реального каталога используется только bootstrap из 3A.

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

Для обычных symlink-based releases использовать `rollback target`, напечатанный deploy helper:

```bash
python /opt/x-gu-ru-tooling/server-opt/deploy_release.py \
  /var/www/x-gu.ru/releases/<previous-release> \
  --apply
```

Self-contained releases содержат собственные `.xgu-index-keep.json` и `.xgu-whitelist.txt`, поэтому rollback возвращает согласованную HTML/SEO policy.

После самого первого bootstrap отдельно сохранить `pre-bootstrap-*` как emergency legacy copy. Не пытаться подавать эту legacy-копию в `deploy_release.py`, если в ней нет self-contained release contract. Если нужен аварийный возврат именно к legacy-копии, остановить дальнейшие действия и выполнить осознанный ручной recovery под контролем оператора.

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

Active release перепроверяется непосредственно перед каждым удалением. `pre-bootstrap-*` не удалять, пока отдельно не принято решение, что legacy emergency copy больше не нужна.

## Что оператор должен вернуть владельцу после работы

Без секретов и содержимого `.env`:

- SHA ветки, которая была проверена;
- результат `python scripts/validate_repo.py`;
- исходный тип `current`: real directory или symlink;
- прежний `current` target, если он был symlink;
- новый release path;
- при bootstrap — путь `pre-bootstrap-*` legacy backup;
- результат dry-run/apply `shrink_index.py`;
- результат `predeploy_check.py`;
- результат `bootstrap_release_layout.py` или `deploy_release.py`;
- новый `current` target;
- результат post-deploy healthcheck/curl;
- rollback target;
- любые stop-условия или ошибки, если rollout не был выполнен.
