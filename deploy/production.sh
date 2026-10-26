#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
env_file="$project_dir/.env.production"
backup_dir="${AUTOXAK_BACKUP_DIR:-$project_dir/backups}"

compose() {
    docker compose --env-file "$env_file" -f "$project_dir/compose.production.yml" "$@"
}

backup() {
    mkdir -p "$backup_dir"
    local backup_file temporary_file
    backup_file="$backup_dir/autoxak-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
    temporary_file="$(mktemp "$backup_dir/.incomplete.XXXXXX")"
    if compose exec -T postgres pg_dump -U autoXAK_user -d autoXAK_db --format=custom > "$temporary_file" \
        && test -s "$temporary_file" \
        && compose exec -T postgres pg_restore --list < "$temporary_file" > /dev/null; then
        mv -- "$temporary_file" "$backup_file"
        git rev-parse HEAD > "$backup_file.commit"
        compose images -q api > "$backup_file.image"
        printf 'Backup: %s\n' "$backup_file"
    else
        rm -f -- "$temporary_file"
        printf 'Backup failed; deployment aborted.\n' >&2
        return 1
    fi
}

action="${1:-help}"
if [[ "$action" == init ]]; then
    domain="${2:-}"
    email="${3:-}"
    if [[ ! "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ || "$domain" != *.* || ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
        printf 'Usage: bash deploy/production.sh init example.com admin@example.com\n' >&2
        exit 1
    fi
    generated_secret="$(openssl rand -hex 32)"
    generated_password="$(openssl rand -hex 32)"
    # Exclusive creation prevents accidental replacement of persistent signing keys.
    if (set -o noclobber; : > "$env_file") 2>/dev/null; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                APP_DOMAIN=*) printf 'APP_DOMAIN=%s\n' "$domain" ;;
                ACME_EMAIL=*) printf 'ACME_EMAIL=%s\n' "$email" ;;
                SECRET_KEY=*) printf 'SECRET_KEY=%s\n' "$generated_secret" ;;
                POSTGRES_PASSWORD=*) printf 'POSTGRES_PASSWORD=%s\n' "$generated_password" ;;
                *) printf '%s\n' "$line" ;;
            esac
        done < deploy/production.env.example >> "$env_file"
        printf 'Created %s (mode 600). Preserve it across deployments.\n' "$env_file"
    else
        printf '%s already exists; it has not been changed.\n' "$env_file" >&2
        exit 1
    fi
    exit 0
fi

if [[ "$action" == help ]]; then
    printf 'Usage: bash deploy/production.sh {init DOMAIN EMAIL|start|seed|backup|update|check|status|logs}\n'
    exit 0
fi
if [[ ! -f "$env_file" ]]; then
    printf 'Missing .env.production. For a NEW installation use: bash deploy/production.sh init DOMAIN EMAIL\n' >&2
    exit 1
fi
export APP_IMAGE_TAG="${APP_IMAGE_TAG:-$(git rev-parse --short=12 HEAD)}"
# Never print the rendered config: it contains credentials.
compose config --quiet

case "$action" in
    start)
        if [[ "$(docker inspect -f '{{.State.Running}}' autoXAK_postgres 2>/dev/null || true)" == true ]]; then
            printf 'Existing autoXAK_postgres is running. Back it up and stop the old stack before reusing its volume.\n' >&2
            exit 1
        fi
        compose pull postgres redis
        compose build api
        compose up -d --wait --wait-timeout 180
        compose ps
        ;;
    seed)
        # Run explicitly on first installation; these include example fuel prices.
        compose exec -T api python /app/deploy/entrypoint.py python seed_fleet.py
        compose exec -T api python /app/deploy/entrypoint.py python seed_fuel_prices.py
        ;;
    backup) backup ;;
    update)
        if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
            printf 'Commit or save tracked local changes before updating.\n' >&2
            exit 1
        fi
        backup
        git pull --ff-only
        export APP_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
        compose build api
        # Database/cache images are deliberately upgraded in a separate maintenance window.
        compose up -d --no-deps --wait --wait-timeout 180 api
        if [[ "$(compose ps --services --status running)" == *caddy* ]]; then
            compose up -d --no-deps caddy
        fi
        compose ps
        ;;
    check)
        compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5).read().decode())"
        ;;
    status) compose ps -a ;;
    logs) compose logs --tail=100 api caddy ;;
    *) printf 'Unknown action: %s\n' "$action" >&2; exit 1 ;;
esac
