#!/usr/bin/env bash
# Restore the existing /opt/autoxak API behind host Nginx, using its current DB.
set -Eeuo pipefail
umask 077
project_dir="${1:-/opt/autoxak}"
api=autoXAK_backend_api
postgres=autoXAK_postgres
cd -- "$project_dir"
project_dir="$(pwd -P)"
command -v docker >/dev/null
command -v curl >/dev/null
[[ -f docker-compose.yml ]] || { printf 'docker-compose.yml not found.\n' >&2; exit 1; }
label_dir="$(docker inspect "$api" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}')"
project="$(docker inspect "$api" --format '{{ index .Config.Labels "com.docker.compose.project" }}')"
[[ "$label_dir" == "$project_dir" && "$project" == autoxak ]] || { printf 'Unexpected API project/path; no changes applied.\n' >&2; exit 1; }
volume="$(docker inspect "$postgres" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}')"
[[ "$volume" == autoxak_pgdata ]] || { printf 'Unexpected PostgreSQL volume; no changes applied.\n' >&2; exit 1; }
# Two PostgreSQL servers must never run on the same PGDATA. Stop only the known
# duplicate; leave unrelated databases (including test.autoxak.ru) untouched.
while IFS= read -r container; do
    [[ -n "$container" ]] || continue
    name="$(docker inspect "$container" --format '{{.Name}}')"
    case "$name" in
        /autoXAK_postgres) ;;
        /autoxak-production-postgres-1)
            printf 'Stopping duplicate PostgreSQL on volume %s.\n' "$volume"
            docker stop --time 60 "$container" ;;
        *) printf 'Another container mounts PGDATA: %s. Inspect it before continuing.\n' "$name" >&2; exit 1 ;;
    esac
done < <(docker ps -q --filter "volume=$volume")
if [[ "$(docker inspect "$postgres" --format '{{.State.Running}}')" != true ]]; then
    printf 'Starting existing PostgreSQL on volume %s.\n' "$volume"
    docker start "$postgres"
fi
postgres_ready=false
for attempt in {1..45}; do
    if docker exec "$postgres" sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
        postgres_ready=true
        break
    fi
    sleep 1
done
if [[ "$postgres_ready" != true ]]; then
    printf 'Existing PostgreSQL did not become ready; no API/config changes applied.\n' >&2
    docker logs --tail=80 "$postgres"
    exit 1
fi
compose() { docker compose -p "$project" "$@"; }
# Validate existing environment without printing credentials.
compose config --quiet
override=docker-compose.override.yml
marker='# autoXAK HTTP recovery override'
if [[ -e "$override" ]] && ! head -n 1 "$override" | grep -Fxq -- "$marker"; then
    printf 'An existing custom override requires review; it was not overwritten.\n' >&2
    exit 1
fi
mkdir -p backups
backup_dir="$(mktemp -d "$project_dir/backups/http-recovery-XXXXXXXX")"
cp -- docker-compose.yml "$backup_dir/"
[[ ! -f "$override" ]] || cp -- "$override" "$backup_dir/"
docker inspect "$api" --format '{{json .Config.Cmd}}' > "$backup_dir/api-command.before.json"
# Bash preserves the binary stream. Never use PowerShell text redirection here.
if ! docker exec "$postgres" sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$backup_dir/database.dump" \
    || [[ ! -s "$backup_dir/database.dump" ]] \
    || ! docker exec -i "$postgres" pg_restore --list < "$backup_dir/database.dump" >/dev/null; then
    printf 'Database backup failed. API was not recreated. Inspect PostgreSQL before continuing.\n' >&2
    exit 1
fi
trusted_ips="$(docker inspect "$api" --format '{{range .NetworkSettings.Networks}}{{.Gateway}},{{end}}127.0.0.1')"
[[ "$trusted_ips" =~ ^[0-9.,]+$ ]] || { printf 'Unexpected proxy gateway format.\n' >&2; exit 1; }
temporary="$(mktemp "$project_dir/.http-override-XXXXXXXX")"
cat > "$temporary" <<YAML
$marker
services:
  api:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "$trusted_ips"]
    ports: !override
      - "127.0.0.1:8000:8000"
YAML
# !override is supported by Compose >=2.24.4 (this server reports 5.6.0).
if ! docker compose -p "$project" -f docker-compose.yml -f "$temporary" config --quiet; then
    rm -f -- "$temporary"
    printf 'HTTP override validation failed; API unchanged. Backup: %s\n' "$backup_dir" >&2
    exit 1
fi
mv -- "$temporary" "$override"
# No build, database/cache recreation, volume replacement, or environment changes.
compose up -d --no-deps --no-build --force-recreate api
ready=false
for attempt in {1..30}; do
    if curl --fail --silent --show-error --connect-timeout 2 --max-time 3 http://127.0.0.1:8000/ready > "$backup_dir/ready.json" 2>/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    printf 'HTTP configuration applied, but application is not ready. Backup: %s\n' "$backup_dir" >&2
    compose logs --tail=80 api
    exit 1
fi
cat "$backup_dir/ready.json"
printf '\nAPI ready. Backup: %s\n' "$backup_dir"
# Verify Nginx locally, independently of the mobile network or DNS path.
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    --resolve autoxak.ru:443:127.0.0.1 https://autoxak.ru/ready
printf '\nPublic HTTPS through Nginx is ready.\n'
compose ps api postgres redis
