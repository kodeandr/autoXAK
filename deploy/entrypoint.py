"""Build the database URL without interpreting password characters as URL syntax."""
import os
import sys

from sqlalchemy.engine import URL


def main():
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+asyncpg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host="postgres",
            port=5432,
            database=os.environ["POSTGRES_DB"],
        ).render_as_string(hide_password=False)
    if len(sys.argv) < 2:
        raise SystemExit("A command is required")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
