#!/usr/bin/env bash
# Manage the existing test.autoxak.ru stack; never changes production services.
set -Eeuo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir=/opt/autoxak-test
[[ -f "$test_dir/.env.test" && -f "$test_dir/compose.test.yml" ]] || {
    printf 'Expected the existing /opt/autoxak-test configuration.\n' >&2
    exit 1
}
compose() {
    ML_DOCKER_GID="$(stat -c %g /var/run/docker.sock)" ML_SOURCE_DIR="$source_dir" APP_IMAGE_TAG="$(git -C "$source_dir" rev-parse --short=12 HEAD)" \
    env -u SECRET_KEY -u POSTGRES_PASSWORD -u POSTGRES_VOLUME -u POSTGRES_VOLUME_EXTERNAL \
        -u APP_DOMAIN -u ACME_EMAIL -u COMPOSE_PROFILES -u DGIS_API_KEY \
        -u YANDEX_CLIENT_ID -u YANDEX_CLIENT_SECRET -u YANDEX_ID_SALT \
        -u PROXY_SUBNET -u PROXY_GATEWAY -u PROXY_API_IP -u PROXY_IP -u PROXY_TRUSTED_IPS \
        docker compose --project-name autoxak-test --env-file "$test_dir/.env.test" \
        -f "$test_dir/compose.test.yml" -f "$source_dir/compose.ml-test.yml" "$@"
}
backup() {
    install -d -m 700 "$test_dir/backups"
    local target temporary
    target="$test_dir/backups/before-ml-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
    temporary="$(mktemp "$test_dir/backups/.incomplete.XXXXXX")"
    if compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$temporary" \
        && test -s "$temporary" \
        && compose exec -T postgres pg_restore --list < "$temporary" > /dev/null; then
        mv -- "$temporary" "$target"
        printf 'Test database backup: %s\n' "$target"
    else
        rm -f -- "$temporary"
        printf 'Backup failed; deployment aborted.\n' >&2
        return 1
    fi
}
compose config --quiet
case "${1:-help}" in
    start)
        docker_api="$(docker version --format '{{.Server.APIVersion}}')"
        python3 -c 'import sys; assert tuple(map(int,sys.argv[1].split("."))) >= (1,45), "Docker Engine с API 1.45+ нужен для изоляции файлов моделей"' "$docker_api"
        compose --profile ml-build build api ml_runtime ml_controller
        backup
        compose up -d --no-deps --no-build --wait --wait-timeout 180 api
        compose up -d --no-deps --no-build --wait --wait-timeout 90 ml_controller
        compose ps
        ;;
    backup) backup ;;
    status) compose ps -a ;;
    logs) compose logs --tail=80 api ml_controller ;;
    check) compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=5).read().decode())" ;;
    *) printf 'Usage: bash deploy/test_ml.sh {start|backup|status|logs|check}\n' ;;
esac
