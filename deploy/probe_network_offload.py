#!/usr/bin/env python3
"""Temporarily compare virtio offloads, with a host-systemd rollback timer."""

import argparse
from datetime import datetime, timezone
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import time


TARGETS = (
    "tx-tcp-segmentation", "tx-tcp6-segmentation", "tx-tcp-ecn-segmentation",
    "generic-segmentation-offload", "generic-receive-offload", "rx-gro-hw",
    "tx-checksum-ip-generic",
)


def command(argv, timeout=10):
    result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def parse_features(source, allow_pending=False):
    """Exclude ethtool's aggregate headings; retain actual feature bits only."""
    rows = []
    for line in source.splitlines():
        match = re.fullmatch(r"(\s*)([a-z0-9_-]+):\s+(on|off)(.*)", line)
        if match:
            indent, name, value, suffix = match.groups()
            requested = re.search(r"\[requested (on|off)\]", suffix)
            rows.append((len(indent.expandtabs()), name, {
                "active": value, "fixed": "[fixed]" in suffix,
                "requested": requested.group(1) if requested else value,
            }))
    result = {}
    for index, (indent, name, state) in enumerate(rows):
        if index + 1 < len(rows) and rows[index + 1][0] > indent:
            continue
        result[name] = state
    if not result:
        raise ValueError("Cannot parse ethtool features; no settings have been changed")
    if not allow_pending and any(state["active"] != state["requested"] for state in result.values()):
        raise ValueError("Pending feature requests exist; refusing to change this interface")
    return result


def make_plan(ethtool, interface, features):
    # Restore all mutable bits: disabling checksum offload may affect dependencies.
    restore = [ethtool, "-K", interface]
    for name, state in features.items():
        if not state["fixed"]:
            restore.extend((name, state["active"]))
    targets = [name for name in TARGETS if name in features and not features[name]["fixed"]
               and features[name]["active"] == "on"]
    if not targets:
        raise ValueError("No enabled mutable target features; no comparison is possible")
    disable = [ethtool, "-K", interface]
    for name in targets:
        disable.extend((name, "off"))
    return disable, restore, targets


def assert_restored(original, current):
    mismatches = [name for name, state in original.items()
                  if name not in current or current[name]["active"] != state["active"]]
    if mismatches:
        raise RuntimeError("Feature restoration not confirmed: " + ", ".join(mismatches))


def observe(seconds, domain):
    print(f"Phone reproduction window ({seconds}s): NOW reconnect the phone and open https://{domain}/ready then https://{domain}/", flush=True)
    deadline = time.monotonic() + seconds
    while True:
        print(datetime.now(timezone.utc).isoformat(timespec="seconds"), flush=True)
        output = command(["ss", "-ntio", "state", "established", "( sport = :443 )"], timeout=3)
        # Remove terminal padding, which otherwise makes pasted reports very large.
        print("\n".join(line.rstrip() for line in output.splitlines()[:100]), flush=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(5, remaining))


def comparison(ethtool, interface, features, seconds, domain):
    disable, restore, targets = make_plan(ethtool, interface, features)
    unit = f"autoxak-offload-rollback-{os.getpid()}-{secrets.token_hex(3)}"
    print("Original state recorded. Emergency restore command:\n" + shlex.join(restore), flush=True)
    # Arm before any mutation; systemd still runs this if SSH/Python is interrupted.
    command(["systemd-run", "--quiet", "--collect", "--unit", unit,
             "--on-active=180s", "--property=Type=oneshot", "--", *restore])
    restored = False
    try:
        command(["systemctl", "is-active", "--quiet", unit + ".timer"])
        print(f"Independent rollback armed: {unit}.timer (180s).", flush=True)
        command(disable)
        current = parse_features(command([ethtool, "-k", interface]), allow_pending=True)
        not_disabled = [name for name in targets if current[name]["active"] != "off"]
        if not_disabled:
            raise RuntimeError("Comparison not applied: " + ", ".join(not_disabled))
        print("Temporarily disabled: " + ", ".join(targets), flush=True)
        observe(seconds, domain)
    finally:
        try:
            command(restore)
            assert_restored(features, parse_features(command([ethtool, "-k", interface])))
            restored = True
            print("RESTORED: all original interface feature states verified.", flush=True)
        except Exception as exc:
            print(f"RESTORE NOT CONFIRMED: {exc}\nIndependent rollback retained: {unit}.timer", flush=True)
        if restored:
            # Do not cancel the independent rollback unless restoration was verified.
            command(["systemctl", "stop", unit + ".timer"])
        else:
            raise RuntimeError("Check the rollback timer and the emergency restore command above")


def interrupted(number, frame):
    raise SystemExit(128 + number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="enp3s0")
    parser.add_argument("--domain", default="autoxak.ru")
    parser.add_argument("--observe", type=int, default=45, choices=range(15, 61), metavar="15..60")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without changing anything or arming a timer")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", args.interface) or args.interface.startswith("-"):
        parser.error("Invalid interface name")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,251}[a-zA-Z0-9]", args.domain):
        parser.error("Use a hostname, not a URL")
    ethtool = shutil.which("ethtool")
    if not ethtool:
        parser.error("ethtool is required; no changes made")
    features = parse_features(command([ethtool, "-k", args.interface]))
    disable, restore, _ = make_plan(ethtool, args.interface, features)
    if args.dry_run:
        print("Would disable:\n" + shlex.join(disable) + "\nWould restore:\n" + shlex.join(restore))
        return
    if os.geteuid() != 0 or any(not shutil.which(name) for name in ("ss", "systemd-run", "systemctl")):
        parser.error("Run as root on a systemd host with ss; no changes made")
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    comparison(ethtool, args.interface, features, args.observe, args.domain)


if __name__ == "__main__":
    main()
