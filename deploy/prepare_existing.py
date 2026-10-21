"""Copy existing Docker deployment settings locally, without printing credentials.

This only creates a private env file. It does not stop or modify containers/data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def inspect_container(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]


def environment(container):
    return dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)


def dotenv_quote(value):
    if "\n" in value or "\r" in value:
        raise ValueError("Multiline configuration values are unsupported")
    # Compose uses $$ for a literal dollar sign; escape quotes/backslashes too.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("email", nargs="?", default="")
    parser.add_argument("--api-container", default="autoXAK_backend_api")
    parser.add_argument("--postgres-container", default="autoXAK_postgres")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", args.domain) or "." not in args.domain:
        parser.error("Specify a domain without a scheme or path")
    if args.email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email):
        parser.error("Specify an email address")

    root = Path(__file__).resolve().parent.parent
    target = root / ".env.production"
    if target.exists():
        raise SystemExit(".env.production already exists; no settings were changed")
    api = inspect_container(args.api_container)
    postgres = inspect_container(args.postgres_container)
    api_env, pg_env = environment(api), environment(postgres)
    if pg_env.get("POSTGRES_USER") != "autoXAK_user" or pg_env.get("POSTGRES_DB") != "autoXAK_db":
        raise SystemExit("Nonstandard database/user; adapt the production config before migrating")
    if len(api_env.get("SECRET_KEY", "").strip()) < 32:
        raise SystemExit("Existing SECRET_KEY is absent or too short; resolve key migration before proceeding")
    if hashlib.sha256(api_env["SECRET_KEY"].strip().encode()).hexdigest() == "ebf4229c4ed7aeed131e39d0862a28baa330d56d474fc64e3803da2fec1ea0f9":
        raise SystemExit("The old publicly committed signing key is still in use. Plan key rotation and preserve YANDEX_ID_SALT before migrating; existing guest tokens will need special handling")
    if not pg_env.get("POSTGRES_PASSWORD"):
        raise SystemExit("POSTGRES_PASSWORD is unavailable; preserve the existing password manually")
    if hashlib.sha256(pg_env["POSTGRES_PASSWORD"].encode()).hexdigest() == "258c6d0df36d592b044e3e6ca19e855352a13bf6107097ccf881cb2e2e06f0ff":
        raise SystemExit("The old publicly committed PostgreSQL password is still in use. Rotate the database password and update its private deployment settings before migrating")
    # Unlike pg_isready, this verifies the password against the existing cluster.
    subprocess.run([
        "docker", "exec", args.postgres_container, "sh", "-c",
        'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tAc "SELECT 1"',
    ], check=True, stdout=subprocess.DEVNULL)
    mounts = [mount for mount in postgres["Mounts"] if mount["Destination"] == "/var/lib/postgresql/data"]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume":
        raise SystemExit("Expected a named PostgreSQL volume; adapt config to preserve the actual data mount")
    volume_name = mounts[0]["Name"]
    subprocess.run(["docker", "volume", "inspect", volume_name], check=True, stdout=subprocess.DEVNULL)

    values = {
        "APP_DOMAIN": args.domain,
        "ACME_EMAIL": args.email,
        "SECRET_KEY": api_env["SECRET_KEY"],
        "POSTGRES_PASSWORD": pg_env["POSTGRES_PASSWORD"],
        "POSTGRES_VOLUME": volume_name,
        "POSTGRES_VOLUME_EXTERNAL": "true",
        "PROXY_SUBNET": "172.29.250.0/24",
        "PROXY_GATEWAY": "172.29.250.1",
        "PROXY_IP": "172.29.250.2",
        "PROXY_API_IP": "172.29.250.3",
        "PROXY_TRUSTED_IPS": "172.29.250.1,172.29.250.2",
        "COMPOSE_PROFILES": "",
    }
    for name in ("DGIS_API_KEY", "YANDEX_CLIENT_ID", "YANDEX_CLIENT_SECRET", "YANDEX_ID_SALT"):
        values[name] = api_env.get(name, "")
    content = "# Preserved from existing containers. Keep this file private.\n" + "".join(
        f"{name}={dotenv_quote(value)}\n" for name, value in values.items()
    )
    os.umask(0o077)
    with target.open("x", encoding="utf-8") as output:
        output.write(content)
    print(f"Created private {target.name}; existing containers and volume are unchanged.")


if __name__ == "__main__":
    main()
