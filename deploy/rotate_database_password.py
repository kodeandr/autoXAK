"""Apply or undo the planned password change without putting secrets in arguments."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def verify_config(root, state):
    raw = subprocess.check_output([
        "docker", "compose", "--env-file", str(root / ".env.production"),
        "-f", str(root / "compose.production.yml"), "config", "--format", "json",
    ], text=True)
    environment = json.loads(raw)["services"]["api"]["environment"]
    if (environment["POSTGRES_PASSWORD"] != state["new_password"]
            or hashlib.sha256(environment["SECRET_KEY"].encode()).hexdigest() != state["signing_key_sha256"]):
        raise SystemExit("Private rotation state does not match .env.production; no password was changed")


def change_password(state, restore=False):
    password = state["old_password"] if restore else state["new_password"]
    if not password or any(character in password for character in "\r\n"):
        raise SystemExit("Cannot rotate an empty or multiline password")
    container = state["postgres_container"]
    command = ["docker", "exec", "-i", container, "psql", "-U", "autoXAK_user", "-d", "autoXAK_db",
               "-v", "ON_ERROR_STOP=1", "-c", '\\password "autoXAK_user"']
    for attempt in range(60):
        # psql encrypts the password client-side; neither password appears in argv.
        result = subprocess.run(command, input=password + "\n" + password + "\n",
                                text=True, capture_output=True)
        if result.returncode == 0:
            break
        if attempt == 59:
            raise SystemExit("Could not set the PostgreSQL password; inspect the database startup logs")
        time.sleep(0.5)
    verification = subprocess.run([
        "docker", "exec", "-i", container, "sh", "-c",
        'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -h 127.0.0.1 -U autoXAK_user -d autoXAK_db -v ON_ERROR_STOP=1 -tAc "SELECT 1"',
    ], input=password + "\n", text=True, capture_output=True)
    if verification.returncode != 0 or verification.stdout.strip() != "1":
        raise SystemExit("PostgreSQL password authentication check failed; cutover aborted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "restore"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    state = json.loads((root / ".migration-secrets.json").read_text())
    verify_config(root, state)
    if args.action != "check":
        change_password(state, restore=args.action == "restore")
        print("Database password restored and verified." if args.action == "restore" else "Database password changed and verified.")


if __name__ == "__main__":
    main()
