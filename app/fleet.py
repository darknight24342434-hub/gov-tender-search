"""Cross-machine Codex worker helpers for distributed enrichment."""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .ai import codex_client


# 這些參數會帶著標案文字（外部輸入）去跑模型，所以刻意不加任何
# 繞過核准／沙箱的旗標。要改請先想清楚 prompt injection 的後果。
CODEX_ARGS = [
    "exec",
    "--ignore-user-config",
    "--skip-git-repo-check",
    "-c",
    "model=gpt-5.5",
    "--output-last-message",
    "-",
]


@dataclass(frozen=True)
class Worker:
    name: str
    kind: str
    command: list[str]
    timeout: int = 240


@dataclass
class WorkerResult:
    worker: str
    ok: bool
    text: str = ""
    error: str = ""
    attempts: int = 1
    elapsed_sec: float = 0.0


def workers_config_path() -> Path:
    """workers.json 的位置。設 GTS_WORKERS_FILE 可覆寫。"""
    env_path = os.getenv("GTS_WORKERS_FILE")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent / "workers.json"


def default_workers() -> dict[str, Worker]:
    """從 workers.json 讀出可用的 Codex worker。

    這份檔案描述你自己的機器（本機執行檔路徑、SSH 目標、金鑰位置），所以它
    **不進版控**——見 workers.example.json 與 .gitignore。檔案不存在時只回
    一個本機 worker，讓單機使用不需要任何設定。
    """
    path = workers_config_path()
    if not path.exists():
        return {"local": Worker("local", "local", [os.getenv("CODEX_BIN", "codex"), *CODEX_ARGS])}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read workers config {path}: {exc}") from exc

    entries = raw.get("workers") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise RuntimeError(f"{path}: expected a list of workers, or an object with a 'workers' list")

    workers: dict[str, Worker] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"{path}: every worker must be an object")
        name = str(entry.get("name") or "").strip().lower()
        kind = str(entry.get("kind") or "local").strip().lower()
        command = entry.get("command")
        if not name:
            raise RuntimeError(f"{path}: a worker is missing 'name'")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise RuntimeError(f"{path}: worker {name!r} needs 'command' as a list of strings")
        timeout = int(entry.get("timeout", 240))
        if entry.get("append_codex_args", True):
            command = [*command, *CODEX_ARGS]
        workers[name] = Worker(name, kind, command, timeout)

    if not workers:
        raise RuntimeError(f"{path}: no workers defined")
    return workers


def select_workers(names: Iterable[str]) -> list[Worker]:
    workers = default_workers()
    selected = []
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        if key not in workers:
            raise ValueError(f"unknown worker: {name}")
        selected.append(workers[key])
    return selected


def _is_retryable(text: str) -> bool:
    lowered = text.lower()
    needles = [
        "rate limit",
        "rate_limit",
        "429",
        "too many requests",
        "temporarily unavailable",
        "overloaded",
    ]
    return any(needle in lowered for needle in needles)


def run_codex(
    worker: Worker,
    prompt: str,
    *,
    timeout: Optional[int] = None,
    retries: int = 2,
    backoff: float = 3.0,
) -> WorkerResult:
    """Run one prompt on a worker and return stdout text."""
    attempts = 0
    started = time.monotonic()
    last_error = ""
    last_text = ""
    max_attempts = max(1, retries + 1)
    for attempts in range(1, max_attempts + 1):
        try:
            proc = subprocess.run(
                worker.command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or worker.timeout,
            )
        except FileNotFoundError as exc:
            last_error = str(exc)
            break
        except subprocess.TimeoutExpired:
            last_error = "timeout"
            break

        last_text = (proc.stdout or "").strip()
        last_error = (proc.stderr or "").strip()
        combined = f"{last_text}\n{last_error}".strip()
        if proc.returncode == 0 and last_text:
            return WorkerResult(
                worker=worker.name,
                ok=True,
                text=last_text,
                attempts=attempts,
                elapsed_sec=time.monotonic() - started,
            )
        if attempts < max_attempts and _is_retryable(combined):
            time.sleep(backoff * attempts)
            continue
        break

    return WorkerResult(
        worker=worker.name,
        ok=False,
        text=last_text,
        error=last_error or "codex returned no output",
        attempts=attempts,
        elapsed_sec=time.monotonic() - started,
    )


def health_check(worker: Worker) -> WorkerResult:
    prompt = (
        'Return only this JSON object, no markdown: '
        f'{{"ok":true,"machine":"{worker.name}"}}'
    )
    result = run_codex(worker, prompt, timeout=90, retries=0)
    parsed = codex_client.extract_json(result.text)
    if result.ok and isinstance(parsed, dict) and parsed.get("ok") is True:
        machine = str(parsed.get("machine") or "").strip().lower()
        if machine == worker.name:
            return result
    return WorkerResult(
        worker=worker.name,
        ok=False,
        text=result.text,
        error=result.error or f"invalid health JSON: {result.text[:300]}",
        attempts=result.attempts,
        elapsed_sec=result.elapsed_sec,
    )
