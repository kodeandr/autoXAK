#!/usr/bin/env python3
"""Read-only Ubuntu/Docker/Nginx diagnostics; Python standard library only."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time


OOM_PATTERN = re.compile(r"out of memory|oom-kill|killed process|memory cgroup out", re.I)
NGINX_DIRECTIVE = re.compile(
    r"^\s*(?:listen|server_name|proxy_pass|proxy_ssl_verify|proxy_connect_timeout|"
    r"proxy_read_timeout|ssl_protocols|ssl_certificate|ssl_certificate_key|ssl_buffer_size|"
    r"ssl_session_cache|ssl_session_timeout|http2|access_log|error_log|worker_processes|"
    r"worker_connections|client_max_body_size)\s+"
)


def redact(value):
    """Do not export OAuth codes, URL credentials, tokens or query strings."""
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", str(value))
    value = re.sub(r"\?[^\s\"'<>]*", "?[REDACTED]", value)
    value = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    return re.sub(
        r"(?i)((?:password|secret_key|client_secret|api_key|access_token|refresh_token)"
        r"\s*[=:]\s*)[^\s,;\"']+", r"\1[REDACTED]", value
    )


def run(argv, timeout=10, input_text=None):
    if not shutil.which(argv[0]):
        return 127, "Unavailable command: " + argv[0]
    try:
        result = subprocess.run(
            argv, input=input_text, capture_output=True, text=True, errors="replace",
            timeout=timeout, check=False,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"Timed out after {timeout}s"
    except OSError as exc:
        return 126, f"Cannot run {argv[0]}: {type(exc).__name__}"


def read(path):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return "unavailable"


def memory_snapshot():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        parts = line.replace(":", "").split()
        if len(parts) >= 2 and parts[1].isdigit():
            info[parts[0]] = int(parts[1]) * 1024
    vm = {}
    for line in read("/proc/vmstat").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            vm[parts[0]] = int(parts[1])
    return {"memory_bytes": info, "vm_counters": vm, "monotonic": time.monotonic()}


def memory_conclusions(first, last):
    mem = last["memory_bytes"]
    total, available = mem.get("MemTotal", 0), mem.get("MemAvailable")
    findings = []
    if total and available is not None:
        ratio = available / total
        findings.append(f"MemAvailable: {available / 1024**2:.0f} MiB ({ratio:.1%} of RAM).")
        if ratio < .05:
            findings.append("Very little available RAM; inspect PSI, swap traffic and OOM below.")
        elif ratio < .15:
            findings.append("Limited available RAM; peak usage may exhaust this reserve.")
        else:
            findings.append("This snapshot does not show host RAM exhaustion; check peaks and cgroup limits too.")
    if mem.get("SwapTotal", 0) == 0:
        findings.append("No swap configured. This alone does not prove an out-of-memory problem.")
    before, after = first["vm_counters"], last["vm_counters"]
    for key in ("pswpin", "pswpout", "pgmajfault", "oom_kill"):
        if key in before and key in after:
            findings.append(f"{key}: +{max(0, after[key] - before[key])} during audit (cumulative={after[key]}).")
    if "oom_kill" in before and after.get("oom_kill", 0) > before["oom_kill"]:
        findings.append("CONFIRMED: kernel OOM kills increased during this audit.")
    findings.append("Swap pages/major faults indicate activity, not its cause; correlate with latency and PSI.")
    return findings


class Report:
    def __init__(self, path):
        self.path = path
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(descriptor, "w")

    def write(self, value=""):
        value = redact(value)
        print(value, flush=True)
        self.file.write(value + "\n")
        self.file.flush()

    def section(self, title):
        self.write("\n=== " + title + " ===")

    def command(self, title, argv, timeout=10):
        self.write("-- " + title)
        code, output = run(argv, timeout)
        self.write(output[:18000])
        if code:
            self.write(f"exit={code} (unavailable/failed checks do not stop the audit)")
        return code, output


def cgroup_memory(pid):
    """Read only the unified cgroup of this container; never accept traversal."""
    root = Path("/sys/fs/cgroup").resolve()
    for line in read(f"/proc/{pid}/cgroup").splitlines():
        if not line.startswith("0::"):
            continue
        directory = (root / line[3:].lstrip("/")).resolve()
        if not directory.is_relative_to(root):
            return {"status": "cgroup path outside mounted hierarchy"}
        result = {}
        for filename in ("memory.current", "memory.max", "memory.peak", "memory.swap.current", "memory.events", "memory.pressure"):
            result[filename] = read(directory / filename)
        return result
    return {"status": "unified cgroup unavailable; use Docker metrics/kernel logs"}


def containers(report):
    report.section("Container state, memory limits and cgroup OOM counters")
    report.command("Container list", ["docker", "ps", "-a", "--format",
                   "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"])
    report.command("Current container usage", ["docker", "stats", "--no-stream", "--format",
                   "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}\t{{.PIDs}}"], 15)
    code, names = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if code:
        return
    template = ('{"name":{{json .Name}},"image":{{json .Config.Image}},'
                '"status":{{json .State.Status}},"pid":{{.State.Pid}},'
                '"oom_killed":{{.State.OOMKilled}},"exit_code":{{.State.ExitCode}},'
                '"restarts":{{.RestartCount}},"started":{{json .State.StartedAt}},'
                '"health":"{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",'
                '"memory_limit":{{.HostConfig.Memory}},"memory_swap_limit":{{.HostConfig.MemorySwap}}}')
    for name in names.splitlines():
        if "autoxak" not in name.lower():
            continue
        code, output = run(["docker", "inspect", "--format", template, name])
        if code:
            report.write(f"{name}: inspect failed")
            continue
        try:
            state = json.loads(output)
        except ValueError:
            report.write(f"{name}: invalid inspect result")
            continue
        report.write(json.dumps(state, ensure_ascii=False))
        if state["pid"] > 0:
            report.write(json.dumps(cgroup_memory(state["pid"]), ensure_ascii=False))
        if state["status"] != "running":
            continue
        if "postgres" in name.lower():
            sql = ("SELECT name,setting,unit FROM pg_settings WHERE name IN "
                   "('shared_buffers','work_mem','maintenance_work_mem','max_connections','effective_cache_size');"
                   "SELECT COALESCE(state,'background'),count(*) FROM pg_stat_activity GROUP BY state;")
            report.command("PostgreSQL memory settings/activity (no SQL text/user data)",
                           ["docker", "exec", name, "psql", "-X", "-U", "autoXAK_user", "-d", "autoXAK_db", "-Atc", sql])
        elif "redis" in name.lower():
            code, output = run(["docker", "exec", name, "redis-cli", "INFO", "memory"])
            fields = ("used_memory:", "used_memory_rss:", "used_memory_peak:", "maxmemory:", "maxmemory_policy:", "mem_fragmentation_ratio:")
            report.write("Redis memory (no keys/values):\n" + "\n".join(line for line in output.splitlines() if line.startswith(fields)))


def nginx(report):
    report.section("Nginx and host listeners")
    report.command("Nginx syntax", ["nginx", "-t"])
    report.command("Nginx service", ["systemctl", "show", "nginx", "--property=ActiveState,SubState,NRestarts,MemoryCurrent,MemoryPeak"])
    report.command("Listening TCP sockets", ["ss", "-lntp"])
    code, output = run(["nginx", "-T"])
    if code == 0:
        report.write("Selected directives only; environment, headers and full configuration are not exported:")
        for line in output.splitlines():
            if line.startswith("# configuration file") or NGINX_DIRECTIVE.match(line):
                report.write(line.strip())
    report.command("Firewall status", ["ufw", "status"])
    report.command("Interface addresses", ["ip", "-brief", "address"])
    report.command("Interface MTU", ["ip", "-o", "link", "show"])
    report.command("Outbound IPv4 route", ["ip", "route", "get", "1.1.1.1"])
    report.command("TCP MTU/syn-cookie settings", ["sysctl", "net.ipv4.tcp_mtu_probing", "net.ipv4.tcp_syncookies"])


def probes(report, domain):
    report.section("DNS and HTTP/TLS latency (server-side tests)")
    domains = [domain] if domain.startswith("www.") else [domain, "www." + domain]
    for host in domains:
        for family, label in ((socket.AF_INET, "A"), (socket.AF_INET6, "AAAA")):
            try:
                addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)})
                report.write(f"{host} {label}: {', '.join(addresses)}")
            except socket.gaierror as exc:
                report.write(f"{host} {label}: unavailable (resolver error {exc.errno}; may mean no record)")
    base = ["curl", "-q", "--noproxy", "*", "--silent", "--show-error", "--connect-timeout", "4", "--max-time", "10",
            "--output", "/dev/null", "--write-out",
            "http=%{http_code} remote=%{remote_ip} bytes=%{size_download} dns=%{time_namelookup}s connect=%{time_connect}s tls=%{time_appconnect}s first_byte=%{time_starttransfer}s total=%{time_total}s\n"]
    jobs = [
        ("Direct API ready", base + ["http://127.0.0.1:8000/ready"]),
        ("Direct API root", base + ["http://127.0.0.1:8000/"]),
    ]
    for host in domains:
        jobs.append((f"Local Nginx ready ({host})", base + ["--resolve", f"{host}:443:127.0.0.1", f"https://{host}/ready"]))
        jobs.append((f"Public DNS/HTTPS ready ({host}, IPv4)", base + ["--ipv4", f"https://{host}/ready"]))
    for agent, ua in (
        ("iPhone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"),
        ("desktop", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"),
    ):
        jobs.append((f"Local Nginx root, {agent} UA", base + ["--resolve", f"{domain}:443:127.0.0.1", "--user-agent", ua, f"https://{domain}/"]))
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda job: run(job[1], 12), jobs))
    for (label, _), (code, output) in zip(jobs, results):
        report.write(f"{label}: exit={code} {output}")
    for protocol in ("-tls1_2", "-tls1_3"):
        code, output = run(["openssl", "s_client", "-connect", "127.0.0.1:443", "-servername", domain,
                            "-verify_hostname", domain, "-verify_return_error", protocol, "-brief"], 8, "")
        report.write(f"Local TLS {protocol}: exit={code}\n{output}")
    report.write("Local probes do not establish reachability or TLS behavior from the phone's network.")


def recent_lines(path, size=96000):
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - size))
            return log.read().decode(errors="replace").splitlines()[-160:]
    except OSError:
        return []


def logs(report):
    report.section("Kernel OOM evidence and relevant Nginx logs")
    code, output = run(["journalctl", "-k", "--since", "24 hours ago", "--no-pager", "-n", "15000"], 10)
    matches = [line for line in output.splitlines() if OOM_PATTERN.search(line)]
    report.write("Kernel OOM matches (24h, last 15000 kernel messages):\n" + ("\n".join(matches[-40:]) if matches else f"none found (journal exit={code}; this does not exclude older events)"))
    errors = recent_lines("/var/log/nginx/error.log")
    matches = [line for line in errors if re.search(r"upstream|SSL|handshake|timed out|memory|too many open", line, re.I)]
    report.write("Relevant tail of default Nginx error.log (timestamps may precede this audit):\n" + ("\n".join(matches[-25:]) or "none found / default log unavailable"))
    access = recent_lines("/var/log/nginx/access.log")
    matches = [line for line in access if re.search(r'"(?:GET|HEAD) /(?:ready|health)?(?:[? ]| HTTP)', line) or "iPhone" in line or "Android" in line]
    report.write("Root/ready/mobile tail of default access.log (URL queries removed):\n" + ("\n".join(matches[-30:]) or "none found / default log unavailable"))
    report.write("Access log entries normally appear when a request finishes; an in-flight TLS/request stall may have no entry yet.")


def observe(report, seconds, domain):
    report.section(f"Phone reproduction window ({seconds}s)")
    report.write(f"NOW open https://{domain}/ready on a phone, then the site root. Match the timestamps below.")
    packet_pool = ThreadPoolExecutor(max_workers=1)
    packet_future = None
    if os.geteuid() == 0 and shutil.which("tcpdump") and shutil.which("timeout"):
        # Text TCP headers only: no -A/-X, packet payload output or pcap file.
        packet_future = packet_pool.submit(run, [
            "timeout", "--signal=INT", str(seconds), "tcpdump", "-nn", "-l", "-i", "any",
            "-s", "96", "-c", "200", "tcp port 443 and not host 127.0.0.1 and not host ::1",
        ], seconds + 5)
    else:
        report.write("Packet header capture skipped (requires existing tcpdump/timeout and root); using ss snapshots.")
    deadline = time.monotonic() + seconds
    while True:
        snap = memory_snapshot()
        mem = snap["memory_bytes"]
        report.write(json.dumps({
            "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "available_mib": round(mem.get("MemAvailable", 0) / 1024**2),
            "swap_used_mib": round((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / 1024**2),
            "pressure": read("/proc/pressure/memory"),
            "oom_kill": snap["vm_counters"].get("oom_kill"),
        }))
        code, connections = run(["ss", "-ntio", "state", "all", "( sport = :443 )"], 3)
        report.write(f"HTTPS TCP queues/retransmits (exit={code}):\n" + "\n".join(connections.splitlines()[:80]))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(5, remaining))
    if packet_future:
        code, headers = packet_future.result()
        report.write(f"HTTPS packet headers (exit={code}; 124 is expected at end of capture window):\n{headers}")
    packet_pool.shutdown(wait=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="autoxak.ru")
    parser.add_argument("--observe", type=int, default=45, choices=range(0, 61), metavar="0..60")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", args.domain):
        parser.error("--domain must be a hostname, not a URL or IP")
    report_path = args.report or Path("/tmp") / f"autoxak-server-audit-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}.txt"
    report = Report(report_path)
    try:
        report.write(f"autoXAK read-only server audit; UTC {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        report.write(f"Report: {report_path} (mode 600); root privileges: {os.geteuid() == 0}")
        report.write("No configuration changes, restarts, installs, load tests or environment dumps.")
        first = memory_snapshot()
        report.section("Host RAM, swap, pressure and CPU")
        for title, command in (
            ("Uptime/load", ["uptime"]), ("RAM (MiB)", ["free", "-m"]),
            ("Swap", ["swapon", "--show"]), ("5 second CPU/swap/IO sample", ["vmstat", "1", "5"]),
            ("Top RSS processes; no command-line arguments", ["ps", "-eo", "pid,ppid,comm,rss,%mem,%cpu", "--sort=-rss"]),
        ):
            code, output = run(command)
            report.write(f"-- {title} (exit={code})\n" + "\n".join(output.splitlines()[:24]))
        for name in ("memory", "cpu", "io"):
            report.write(f"PSI {name}:\n{read('/proc/pressure/' + name)}")
        report.command("Disk space", ["df", "-h", "/", "/var/lib/docker"])
        report.command("Disk inodes", ["df", "-i", "/", "/var/lib/docker"])
        containers(report)
        nginx(report)
        probes(report, args.domain)
        if args.observe:
            observe(report, args.observe, args.domain)
        logs(report)
        report.section("Memory interpretation")
        for finding in memory_conclusions(first, memory_snapshot()):
            report.write(finding)
        report.write("Container memory.events oom_kill > 0 / State.OOMKilled=true prove a past OOM event, not necessarily the cause of this phone failure.")
        report.write("Compare event timestamps/restarts with the failed phone request before changing RAM limits or networking.")
        report.write(f"Completed. Private report: {report_path}")
    finally:
        report.file.close()


if __name__ == "__main__":
    main()
