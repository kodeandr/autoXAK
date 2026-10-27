"""Trusted Docker controller. The public API and candidates never get its socket."""

import json
import os
from pathlib import Path
import re
import subprocess
import time

from app.services.ml_runs import Runs, atomic_json, report
from app.services.model_registry import strict_json


class Controller:
    def __init__(self):
        self.runs = Runs()
        self.image = os.environ["ML_RUN_IMAGE"]
        self.volume = os.environ.get("ML_STORAGE_VOLUME", "autoxak-test_ml")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.volume):
            raise ValueError("Invalid storage volume")
        self.timeout = 30
        self.runtime_id = None

    def docker(self, arguments, timeout=10):
        # Never inherit Docker endpoint/config credentials from application settings.
        env = {"PATH": os.defpath + ":/usr/local/bin", "HOME": "/tmp"}
        return subprocess.run(["docker", "--host=unix:///var/run/docker.sock", *arguments],
                              capture_output=True, timeout=timeout, env=env)

    def heartbeat(self, ready=True, message=None):
        atomic_json(self.runs.registry.root / "runner.json", {
            "updated_at": time.time(), "ready": ready, "message": message,
            "image": self.image, "image_id": self.runtime_id, "limits": {"seconds": self.timeout, "memory_mb": 512, "cpu": .5},
        })

    def command(self, run_id):
        self.runs.directory(run_id)
        prefix = f"runs/{run_id}"
        arguments = ["run", "-d", "--pull=never", "--name", f"autoxak-ml-job-{run_id}",
                     "--label", "autoxak.ml.volume=" + self.volume,
                     "--network=none", "--read-only", "--user=10001:10001", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--ipc=private", "--pids-limit=64",
                     "--memory=512m", "--memory-swap=512m", "--cpus=.5",
                     "--ulimit", "cpu=15:15", "--ulimit", "nofile=128:128", "--ulimit", "fsize=1048576:1048576",
                     "--log-driver=local", "--log-opt=max-size=1m", "--log-opt=max-file=1", "--log-opt=compress=false",
                     "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,uid=10001,gid=10001",
                     "--tmpfs", "/output:rw,noexec,nosuid,size=1m,uid=10001,gid=10001,mode=0700",
                     "--mount", f"type=volume,src={self.volume},dst=/model,volume-subpath={prefix}/model,readonly",
                     "--mount", f"type=volume,src={self.volume},dst=/input,volume-subpath={prefix}/input,readonly",
                     "--workdir=/model"]
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            arguments.extend(["--env", name + "="])
        arguments.append(self.runtime_id or self.image)
        return arguments

    def execute(self, run_id):
        row = self.runs.read(run_id)
        if row["status"] != "queued":
            return
        name = f"autoxak-ml-job-{run_id}"
        row.update(status="running", started_at=time.time(), runtime_image=self.image, runtime_image_id=self.runtime_id)
        atomic_json(self.runs.directory(run_id) / "state.json", row)
        started = time.monotonic()
        try:
            created = self.docker(self.command(run_id))
            if created.returncode:
                raise RuntimeError("Не удалось запустить вычислитель: " + created.stderr.decode(errors="replace")[-1500:])
            while time.monotonic() - started < self.timeout:
                self.heartbeat()
                check = self.docker(["exec", name, "python", "-I", "-B", "/runner.py", "--read-result"], timeout=4)
                if check.returncode == 0:
                    result = strict_json(check.stdout)
                    if not isinstance(result, dict) or not result.get("ok"):
                        raise ValueError((result.get("error", "Ошибка модели") + "\n" + result.get("traceback", ""))[:5500])
                    original = strict_json((self.runs.directory(run_id) / "original.json").read_bytes())
                    measured = report(original, result.get("predictions"))
                    row.update(status="succeeded", result={**measured, "runtime_seconds": result.get("runtime_seconds")})
                    break
                state = self.docker(["inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}}", name])
                if state.returncode or not state.stdout.startswith(b"true"):
                    raise RuntimeError("Процесс модели завершился до результата (ошибка, лимит CPU или памяти)")
                if check.returncode not in (3,):
                    raise RuntimeError("Результат отсутствует, превышает лимит или не является обычным файлом")
                time.sleep(.2)
            else:
                raise TimeoutError(f"Выполнение модели превысило {self.timeout} секунд")
        except Exception as error:
            row.update(status="failed", error=str(error)[:6000])
        finally:
            try:
                logs = self.docker(["logs", "--tail=100", name])
                row["logs"] = (logs.stdout + logs.stderr).decode(errors="replace")[-4000:]
            finally:
                self.docker(["rm", "-f", name])
            row["finished_at"] = time.time()
            row["wall_seconds"] = time.monotonic() - started
            atomic_json(self.runs.directory(run_id) / "state.json", row)
            self.heartbeat()

    def serve(self):
        with self.runs.registry.lock("controller", blocking=False):
            for row in self.runs.rows():
                if row["status"] == "running":
                    self.docker(["rm", "-f", f'autoxak-ml-job-{row["id"]}'])
                    row.update(status="failed", error="Вычислитель был перезапущен", finished_at=time.time())
                    atomic_json(self.runs.directory(row["id"]) / "state.json", row)
            while True:
                inspect = self.docker(["image", "inspect", self.image, "--format", "{{.Id}}"])
                self.runtime_id = inspect.stdout.decode().strip() if inspect.returncode == 0 else None
                self.heartbeat(ready=inspect.returncode == 0,
                               message=None if inspect.returncode == 0 else "Вычислитель: " + inspect.stderr.decode(errors="replace")[-1000:])
                if inspect.returncode == 0:
                    queued = [row for row in self.runs.rows() if row["status"] == "queued"]
                    if queued:
                        self.execute(queued[-1]["id"])
                time.sleep(1)


if __name__ == "__main__":
    Controller().serve()
