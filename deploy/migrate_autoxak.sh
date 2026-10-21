#!/usr/bin/env bash
# Run on the existing Ubuntu server as root, from the new checkout.
set -Eeuo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ "$EUID" != 0 ]]; then
    printf 'Run this migration as root on the existing server.\n' >&2
    exit 1
fi
exec 9>/run/lock/autoxak-production-migration.lock
flock -n 9 || { printf 'Another migration is running.\n' >&2; exit 1; }
for command in docker python3 nginx curl systemctl; do
    command -v "$command" > /dev/null
done
for container in autoXAK_backend_api autoXAK_postgres autoXAK_redis; do
    [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == true ]] || {
        printf 'Expected running old container: %s. No switching performed.\n' "$container" >&2
        exit 1
    }
done

nginx_site="$(readlink -f /etc/nginx/sites-enabled/autoxak)"
[[ -f "$nginx_site" ]]
nginx -t
export APP_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
compose() {
    docker compose --env-file "$project_dir/.env.production" -f "$project_dir/compose.production.yml" "$@"
}
if [[ ! -e .env.production ]]; then
    python3 deploy/prepare_existing.py autoxak.ru
else
    printf 'Using existing private .env.production; checking the target volume and domain.\n'
fi
# Inspect rendered configuration in memory; do not print credentials.
compose config --format json | python3 -c '
import hashlib, json, sys
c = json.load(sys.stdin)
e = c["services"]["api"]["environment"]
checks = [
    (c["volumes"]["pgdata"]["name"] == "autoxak_pgdata", "Unexpected PostgreSQL volume"),
    (c["volumes"]["pgdata"].get("external") is True, "Existing volume must be external"),
    (e["YANDEX_REDIRECT_URI"] == "https://autoxak.ru/api/v1/auth/yandex/callback", "Unexpected domain"),
    (len(e["SECRET_KEY"].strip()) >= 32, "Signing key is too short"),
    (hashlib.sha256(e["SECRET_KEY"].strip().encode()).hexdigest() != "ebf4229c4ed7aeed131e39d0862a28baa330d56d474fc64e3803da2fec1ea0f9", "Published signing key must be rotated"),
    (hashlib.sha256(e["POSTGRES_PASSWORD"].encode()).hexdigest() != "258c6d0df36d592b044e3e6ca19e855352a13bf6107097ccf881cb2e2e06f0ff", "Published database password must be rotated"),
]
for condition, message in checks:
    if not condition:
        raise SystemExit(message)
'
docker volume inspect autoxak_pgdata > /dev/null
compose pull postgres redis
compose build api

backup_dir="$(mktemp -d "$project_dir/backups-migration-XXXXXXXX")"
chmod 700 "$backup_dir"
cp -p -- "$nginx_site" "$backup_dir/nginx.original"
cp -p .env.production "$backup_dir/env.production"
python3 deploy/prepare_nginx.py "$nginx_site" "$backup_dir/nginx.staged"
printf 'events {}\nhttp { include "%s/nginx.staged"; }\n' "$backup_dir" > "$backup_dir/nginx.validation.conf"
nginx -t -c "$backup_dir/nginx.validation.conf"

switched=false
nginx_changed=false
rollback() {
    local status="$?"
    trap - ERR INT TERM
    set +e
    if [[ "$switched" == true ]]; then
        printf 'Switch failed; stopping the new stack before restarting the old PostgreSQL.\n' >&2
        if compose stop api redis postgres; then
            if [[ "$nginx_changed" == true ]]; then
                cp -p -- "$backup_dir/nginx.original" "$nginx_site"
            fi
            docker start autoXAK_postgres autoXAK_redis autoXAK_backend_api
            nginx -t && systemctl reload nginx
        else
            printf 'Could not stop the new stack. Old PostgreSQL was NOT started, to protect its volume.\n' >&2
        fi
    fi
    printf 'Migration stopped. Backup directory: %s\n' "$backup_dir" >&2
    exit "${status:-1}"
}
trap rollback ERR
trap 'false' INT TERM
# Pause writes before taking the final dump; it also matches a possible rollback.
switched=true
docker stop -t 100 autoXAK_backend_api
docker exec autoXAK_postgres pg_dump -U autoXAK_user -d autoXAK_db -Fc > "$backup_dir/database.incomplete"
test -s "$backup_dir/database.incomplete"
docker exec -i autoXAK_postgres pg_restore --list < "$backup_dir/database.incomplete" > /dev/null
mv "$backup_dir/database.incomplete" "$backup_dir/database.dump"
docker stop -t 60 autoXAK_redis autoXAK_postgres
compose up -d --no-build --wait --wait-timeout 180 postgres redis api
bash deploy/production.sh check
nginx_changed=true
cp -- "$backup_dir/nginx.staged" "$nginx_site"
nginx -t
systemctl reload nginx
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 --retry 2 --retry-delay 1 --noproxy '*' --resolve autoxak.ru:443:127.0.0.1 https://autoxak.ru/ready
trap - ERR INT TERM
printf '\nMigration completed. Site: https://autoxak.ru\nPrivate backups: %s\n' "$backup_dir"
compose ps
