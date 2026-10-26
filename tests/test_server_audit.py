"""Verify privacy and diagnostic conclusions independently of a live server."""
from deploy import audit_server as audit


def test_redacts_oauth_query_url_password_and_bearer():
    source = (
        'request: "GET /api/v1/auth/yandex/callback?state=private-state&code=private-code HTTP/1.1", '
        'upstream: "https://user:private-password@127.0.0.1:8000/callback?code=private-code", '
        'referrer: "https://oauth.yandex.ru/?code=private-code" '
        'Authorization: Bearer private.jwt.token SECRET_KEY=private-key'
    )
    output = audit.redact(source)
    for private in ("private-state", "private-code", "private-password", "private.jwt.token", "private-key"):
        assert private not in output
    assert "127.0.0.1:8000/callback" in output
    assert "HTTP/1.1" in output


def test_available_memory_overrides_misleading_low_free_memory():
    snapshot = {"memory_bytes": {"MemTotal": 2048 * 1024**2, "MemAvailable": 1300 * 1024**2,
                                 "MemFree": 147 * 1024**2, "SwapTotal": 2048 * 1024**2},
                "vm_counters": {"oom_kill": 0, "pswpin": 10, "pswpout": 20}}
    output = "\n".join(audit.memory_conclusions(snapshot, snapshot))
    assert "does not show host RAM exhaustion" in output
    assert "CONFIRMED" not in output
    assert "pswpin: +0" in output


def test_distinguishes_past_and_new_oom_events():
    first = {"memory_bytes": {}, "vm_counters": {"oom_kill": 3}}
    assert "CONFIRMED" not in "\n".join(audit.memory_conclusions(first, first))
    last = {"memory_bytes": {}, "vm_counters": {"oom_kill": 4}}
    assert "CONFIRMED" in "\n".join(audit.memory_conclusions(first, last))
    unknown = {"memory_bytes": {}, "vm_counters": {}}
    assert "CONFIRMED" not in "\n".join(audit.memory_conclusions(unknown, last))


def test_private_report_refuses_existing_path_and_redacts_output(tmp_path, capsys):
    import os
    import pytest
    path = tmp_path / "report.txt"
    report = audit.Report(path)
    report.write('GET /ready?token=private-token HTTP/1.1')
    report.file.close()
    assert "private-token" not in path.read_text()
    assert "private-token" not in capsys.readouterr().out
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        audit.Report(path)


def test_missing_commands_do_not_stop_audit(monkeypatch):
    monkeypatch.setattr(audit.shutil, "which", lambda _: None)
    assert audit.run(["nginx", "-t"])[0] == 127


def test_cgroup_path_cannot_escape_mount(monkeypatch):
    monkeypatch.setattr(audit, "read", lambda _: "0::/../../etc")
    assert audit.cgroup_memory(123)["status"] == "cgroup path outside mounted hierarchy"
