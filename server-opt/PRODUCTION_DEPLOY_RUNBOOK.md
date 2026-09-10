# x-gu.ru — production deploy runbook

Инструкция для оператора с SSH-доступом, включая Claude Code/Claude. Изменения из GitHub **не попадают на сервер автоматически**.

## Неподвижные правила

- Не делать `git pull`, `cp -r` или массовый patch прямо в `/var/www/x-gu.ru/current`.
- Все изменения сначала выполняются в отдельном release-кандидате.
- `--unsafe-*` не использовать в обычном rollout.
- После `finalize_release.py --apply` candidate считается **immutable**: ничего в нём больше не менять.
- `deploy_release.py`, `bootstrap_release_layout.py` и `prune_releases.py` используют общий host-wide lock `/var/www/x-gu.ru/.release-operation.lock`. Не запускать параллельные rollout/prune процессы и не переопределять `--lock-file` без причины.
- Никогда не печатать `.env`, токены, пароли или содержимое secret-файлов.

Нормальный pipeline:

```text
read-only discovery
 -> separate tooling checkout
 -> validate_repo.py
 -> release candidate
 -> reviewed policy + whitelist snapshot
 -> candidate-only maintenance
 -> finalize_release.py (Git SHA + full content fingerprint)
 -> predeploy_check.py
 -> deploy/bootstrap under host lock
 -> post-deploy checks
 -> retain rollback releases
```

## 0. Read-only диагностика

```bash
set -u

date
uname -a
python3 --version
git --version
df -h /

ls -ld /opt/p3-app /opt/p3-app/app/services 2>/dev/null || true
ls -ld /var/www/x-gu.ru /var/www/x-gu.ru/current /var/www/x-gu.ru/releases 2>/dev/null || true

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

sudo nginx -T 2>/dev/null | grep -n '/var/www/x-gu.ru/current' || true
```

Остановиться, если отсутствуют private backend services, reviewed policy/whitelist, Python 3.11+, понятный Nginx root или достаточное место для отдельного candidate. Непонятные локальные изменения `/opt/p3-app` не перетирать.

Если `current` — обычный каталог, не переименовывать его вручную. Использовать one-time bootstrap ниже.

## 1. Отдельный tooling checkout

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

TOOLING_SHA="$(git -C "$TOOLING" rev-parse HEAD)"
echo "TOOLING_SHA=$TOOLING_SHA"
git -C "$TOOLING" status --short --branch
```

`reset --hard` допустим только для disposable `/opt/x-gu-ru-tooling`; не применять к `/opt/p3-app`.

## 2. Реально выполнить validation suite

```bash
cd "$TOOLING"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install ruff
python scripts/validate_repo.py
```

Продолжать только после успешного validator. Сохранить `TOOLING_SHA` в отчёте.

## 3. Создать release candidate

### 3A. Первый rollout: `current` пока обычный каталог

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
```

Live-каталог остаётся нетронутым до финального bootstrap cutover.

### 3B. Обычный rollout: `current` уже symlink

```bash
set -euo pipefail
CURRENT=/var/www/x-gu.ru/current
RELEASES=/var/www/x-gu.ru/releases
SOURCE="$(readlink -f "$CURRENT")"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="$RELEASES/$STAMP"

[ -L "$CURRENT" ]
[ -d "$SOURCE" ]
mkdir -p "$RELEASES"
mkdir "$RELEASE"
cp -a "$SOURCE"/. "$RELEASE"/

# Новый candidate должен быть изменяемым, поэтому убрать fingerprint,
# скопированный из предыдущего immutable release.
rm -f "$RELEASE/.xgu-release.json"
```

## 4. Reviewed index policy + whitelist snapshot

```bash
cd "$TOOLING"
. .venv/bin/activate
POLICY=/opt/p3-app/data/index_policy.json
SOURCE_WHITELIST=/opt/p3-app/data/whitelist.txt

python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST"
```

Изучить dry-run. При ожидаемом плане:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST" \
  --apply
```

После apply внутри candidate должны быть `.xgu-index-keep.json` и `.xgu-whitelist.txt`.

## 5. Hardened generator

Preview:

```bash
python server-opt/install_generator_facade.py
```

Проверить destination `/opt/p3-app/app/services` и ровно три файла. Затем:

```bash
python server-opt/install_generator_facade.py --apply
```

Installer syntax-checks, stages, backups and rollback. После установки использовать только candidate для рендера.

## 6. Candidate-only maintenance

Все команды сначала без `--apply`:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python server-opt/rerender_open_hubs.py --root "$RELEASE"
# при необходимости:
python server-opt/rerender_hubs_home.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
```

После проверки counts применять только необходимые операции. Например:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE" --apply
python server-opt/rerender_open_hubs.py --root "$RELEASE" --apply
```

До следующего шага candidate ещё можно изменять.

## 7. Финализация: заморозить candidate

Сначала получить актуальный SHA именно того checkout, чей validator прошёл:

```bash
TOOLING_SHA="$(git -C "$TOOLING" rev-parse HEAD)"
```

Preview fingerprint:

```bash
python server-opt/finalize_release.py \
  "$RELEASE" \
  --tooling-revision "$TOOLING_SHA" \
  --source-release "${SOURCE:-legacy-current}"
```

Затем:

```bash
python server-opt/finalize_release.py \
  "$RELEASE" \
  --tooling-revision "$TOOLING_SHA" \
  --source-release "${SOURCE:-legacy-current}" \
  --apply
```

Появится `.xgu-release.json` с:

- `tooling_revision`;
- `finalized_at`;
- `content_sha256`;
- `file_count`;
- `total_bytes`;
- source release.

**После этого candidate не менять.** Bulk mutators сами должны отказать при наличии `.xgu-release.json`.

## 8. Strict predeploy

```bash
python server-opt/predeploy_check.py "$RELEASE"
```

Он проверяет fingerprint всего релиза, Git SHA metadata, policy/whitelist SHA и SEO-инварианты. Любое изменение файла после финализации даёт failure.

## 9A. One-time bootstrap cutover

Только если исходный `current` был обычным каталогом:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE"
```

При успешном preview и только в контролируемое окно:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE" --apply
```

Под host-wide lock скрипт повторно выполняет predeploy, переносит legacy `current` в `releases/pre-bootstrap-*` и ставит symlink на finalized candidate. При сбое symlink-cutover пытается вернуть legacy directory обратно.

`pre-bootstrap-*` — emergency copy; обычный prune её не удаляет.

## 9B. Обычный atomic deploy

Если `current` уже symlink:

```bash
python server-opt/deploy_release.py "$RELEASE"
```

После успешного preview:

```bash
python server-opt/deploy_release.py "$RELEASE" --apply
```

Apply повторно проверяет finalized fingerprint/predeploy **под host-wide lock**, затем атомарно меняет `current`. Сохранить printed rollback target.

## 10. Post-deploy

```bash
readlink -f /var/www/x-gu.ru/current
SEOHC_ROOT=/var/www/x-gu.ru/current python "$TOOLING/seo_healthcheck.py"

curl --resolve x-gu.ru:443:127.0.0.1 -fsS -I https://x-gu.ru/
curl --resolve x-gu.ru:443:127.0.0.1 -fsS https://x-gu.ru/robots.txt | head -50
curl --resolve x-gu.ru:443:127.0.0.1 -fsS https://x-gu.ru/sitemap.xml | head -50
```

Проверить главную, canonical, robots, sitemap, одну открытую страницу, одну закрытую `noindex`, lead form/API и SEO healthcheck.

## 11. Rollback

Для обычного self-contained previous release:

```bash
python "$TOOLING/server-opt/deploy_release.py" \
  /var/www/x-gu.ru/releases/<previous-release> \
  --apply
```

Deploy проверит его исторический fingerprint и policy/whitelist перед rollback.

Первый `pre-bootstrap-*` может не иметь нового release contract; не подавать его в обычный deploy helper. Хранить как emergency filesystem copy до отдельного решения об удалении.

## 12. Nginx rollout отдельно

Не смешивать изменение Nginx с первым application rollout без необходимости. Перед reload:

```bash
sudo nginx -t
```

Только при успехе:

```bash
sudo systemctl reload nginx
```

`/console` должен быть защищён backend authentication либо отдельно ограничен VPN/IP/Nginx auth.

## 13. Prune старых releases

Не выполнять сразу после выкладки. Позже:

```bash
python "$TOOLING/server-opt/prune_releases.py" --keep 5
python "$TOOLING/server-opt/prune_releases.py" --keep 5 --apply
```

Prune использует тот же host-wide lock и повторно строит план под lock. `pre-bootstrap-*` защищены по умолчанию. Для их включения в retention существует отдельный `--include-bootstrap-backups`; использовать только после осознанного решения.

## Отчёт владельцу

Вернуть без секретов:

- `TOOLING_SHA`;
- результат `validate_repo.py`;
- исходный тип/target `current`;
- новый release path;
- результат `shrink_index`;
- `content_sha256` из finalization;
- результат predeploy;
- результат bootstrap/deploy;
- новый `current` target;
- post-deploy healthcheck/curl;
- rollback target или `pre-bootstrap-*` emergency backup;
- любые stop-условия/ошибки.
