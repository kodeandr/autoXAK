#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
compose() {
    # Exported production variables must not override staging's private env file.
    env -u SECRET_KEY -u POSTGRES_PASSWORD -u APP_DOMAIN -u DGIS_API_KEY \
        -u YANDEX_CLIENT_ID -u YANDEX_CLIENT_SECRET -u YANDEX_ID_SALT \
        -u PROXY_SUBNET -u PROXY_GATEWAY -u PROXY_API_IP -u PROXY_TRUSTED_IPS \
        -u COMPOSE_PROFILES \
        docker compose --project-name autoxak-staging --env-file "$project_dir/.env.staging" -f "$project_dir/compose.staging.yml" "$@"
}
backup() {
    mkdir -p .staging/backups
    local target temporary
    target=".staging/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
    temporary="$(mktemp .staging/backups/.incomplete.XXXXXX)"
    if compose exec -T postgres pg_dump -U autoXAK_user -d autoXAK_staging -Fc > "$temporary" \
        && test -s "$temporary" && compose exec -T postgres pg_restore --list < "$temporary" > /dev/null; then
        mv "$temporary" "$target"
        printf 'Staging backup: %s\n' "$target"
    else
        rm -f "$temporary"
        return 1
    fi
}
action="${1:-help}"
if [[ "$action" == init ]]; then
    python3 deploy/init_staging.py "${2:?Usage: staging.sh init staging.example.com}"
    exit 0
fi
if [[ "$action" == help ]]; then
    printf 'Usage: staging.sh {init DOMAIN|start|update|seed|backup|check|status|logs|stop}\n'
    exit 0
fi
[[ -f .env.staging ]] || { printf 'Initialize staging first.\n' >&2; exit 1; }
export APP_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
compose config --quiet
case "$action" in
    start) compose build api; compose up -d --wait --wait-timeout 180; compose ps ;;
    update)
        [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { printf 'Save tracked changes first.\n' >&2; exit 1; }
        backup
        git pull --ff-only
        export APP_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
        compose build api
        compose up -d --no-deps --wait --wait-timeout 180 api
        ;;
    seed)
        # exec avoids a duplicate static API address; entrypoint recreates DATABASE_URL.
        compose exec -T api python /app/deploy/entrypoint.py python seed_fleet.py
        compose exec -T api python /app/deploy/entrypoint.py python seed_fuel_prices.py
        ;;
    backup) backup ;;
    check) compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=5).read().decode())" ;;
    status) compose ps -a ;;
    logs) compose logs --tail=80 api postgres redis ;;
    stop) compose stop ;;
    *) printf 'Unknown action: %s\n' "$action" >&2; exit 1 ;;
esac
