"""Exercise restoration on failure without changing any network interface."""
import pytest

from deploy import probe_network_offload as probe


FEATURES = """Features for enp3s0:
rx-checksumming: on [fixed]
tx-checksumming: on
        tx-checksum-ip-generic: on
        tx-checksum-ipv4: off [fixed]
scatter-gather: on
        tx-scatter-gather: on
        tx-scatter-gather-fraglist: off [fixed]
tcp-segmentation-offload: on
        tx-tcp-segmentation: on
        tx-tcp6-segmentation: on
        tx-tcp-ecn-segmentation: on
        tx-tcp-mangleid-segmentation: off
generic-segmentation-offload: on
generic-receive-offload: on
rx-gro-hw: on
loopback: off [fixed]
"""


def test_plan_restores_dependencies_and_excludes_aggregate_headings():
    features = probe.parse_features(FEATURES)
    disable, restore, _ = probe.make_plan("ethtool", "enp3s0", features)
    assert "tx-checksumming" not in features
    assert "scatter-gather" not in features
    assert "tcp-segmentation-offload" not in features
    assert "rx-checksumming" not in restore
    assert "tx-scatter-gather" in restore
    assert restore[restore.index("tx-tcp-mangleid-segmentation") + 1] == "off"
    assert disable[disable.index("tx-checksum-ip-generic") + 1] == "off"


def test_refuses_ambiguous_pending_requests():
    with pytest.raises(ValueError, match="Pending"):
        probe.parse_features(FEATURES.replace("rx-gro-hw: on", "rx-gro-hw: off [requested on]"))
    changed = FEATURES.replace("rx-gro-hw: on", "rx-gro-hw: off [requested on]")
    assert probe.parse_features(changed, allow_pending=True)["rx-gro-hw"]["active"] == "off"


@pytest.mark.parametrize("failure", ["", "arm", "timer-check", "apply", "observe", "restore"])
def test_independent_rollback_precedes_changes_and_survives_restore_failure(monkeypatch, failure):
    features = probe.parse_features(FEATURES)
    disable, restore, targets = probe.make_plan("ethtool", "enp3s0", features)
    current = {name: dict(value) for name, value in features.items()}
    actions = []

    def command(argv, timeout=10):
        actions.append(argv)
        if argv[0] == "systemd-run" and failure == "arm":
            raise RuntimeError("arm failed")
        if argv[:2] == ["systemctl", "is-active"] and failure == "timer-check":
            raise RuntimeError("timer failed")
        if argv == disable:
            for name in targets:
                current[name]["active"] = "off"
            # Deliberately simulate an automatically changed dependent feature.
            current["tx-scatter-gather"]["active"] = "off"
            if failure == "apply":
                raise RuntimeError("partial apply failed")
        if argv == restore:
            if failure == "restore":
                raise RuntimeError("restore failed")
            current.clear()
            current.update({name: dict(value) for name, value in features.items()})
        if argv[:2] == ["ethtool", "-k"]:
            return "\n".join(f"{name}: {value['active']}" + (" [fixed]" if value["fixed"] else "") for name, value in current.items())
        return ""

    def observe(*args):
        if failure == "observe":
            raise KeyboardInterrupt()

    monkeypatch.setattr(probe, "command", command)
    monkeypatch.setattr(probe, "observe", observe)
    if failure:
        with pytest.raises((RuntimeError, KeyboardInterrupt)):
            probe.comparison("ethtool", "enp3s0", features, 45, "autoxak.ru")
    else:
        probe.comparison("ethtool", "enp3s0", features, 45, "autoxak.ru")
    assert actions[0][0] == "systemd-run"
    if failure == "arm":
        assert disable not in actions
        return
    assert restore in actions
    stopped = any(argv[:2] == ["systemctl", "stop"] for argv in actions)
    if failure == "restore":
        assert not stopped
    else:
        probe.assert_restored(features, current)
        assert stopped


def test_detects_mismatched_restore():
    features = probe.parse_features(FEATURES)
    changed = {name: dict(value) for name, value in features.items()}
    changed["tx-scatter-gather"]["active"] = "off"
    with pytest.raises(RuntimeError, match="tx-scatter-gather"):
        probe.assert_restored(features, changed)
