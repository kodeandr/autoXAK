"""Stage the known autoxak.ru site without changing its domain or certificates."""
import argparse
from pathlib import Path
import re


def prepare(source):
    if not re.search(r"server_name\s+autoxak\.ru\s+www\.autoxak\.ru\s*;", source):
        raise ValueError("Unexpected server_name; inspect the site before switching")
    if "/etc/letsencrypt/live/autoxak.ru/fullchain.pem" not in source:
        raise ValueError("Unexpected TLS certificate; inspect the site before switching")
    result, count = re.subn(
        r"(?m)^([ \t]*)proxy_pass\s+https://127\.0\.0\.1:8000\s*;[ \t]*$",
        r"\1proxy_pass http://127.0.0.1:8000;\n\1proxy_connect_timeout 5s;\n\1proxy_read_timeout 180s;\n\1proxy_send_timeout 180s;",
        source,
    )
    if count != 1:
        raise ValueError("Expected one HTTPS upstream at 127.0.0.1:8000; site was not changed")
    result = re.sub(r"(?m)^[ \t]*proxy_ssl_verify\s+off\s*;[ \t]*\n", "", result)
    result = result.replace("$proxy_add_x_forwarded_for", "$remote_addr")
    result, count = re.subn(r"client_max_body_size\s+50[Mm]\s*;", "client_max_body_size 150m;", result)
    if count != 1:
        raise ValueError("Unexpected request body limit; site was not changed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = prepare(args.source.read_text())
    with args.destination.open("x") as destination:
        destination.write(result)


if __name__ == "__main__":
    main()
