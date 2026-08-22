"""Dispatch AI enrichment jobs across the Codex worker fleet.

Examples:
  python scripts/dispatch_enrich.py --dry-run
  python scripts/dispatch_enrich.py --health-only
  python scripts/dispatch_enrich.py --max-calls 6 --concurrency 3
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from app import db
from app.ai import codex_client, dissect, summarize, tagging
from app.fleet import Worker, health_check, run_codex, select_workers


REPORT_DIR = Path("reports")


@dataclass(frozen=True)
class EnrichJob:
    kind: str
    row_id: int
    field: str
    title: str
    prompt: str


@dataclass
class JobOutcome:
    job: EnrichJob
    worker: str
    ok: bool
    value: Any = None
    error: str = ""
    attempts: int = 1


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _missing_text(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _missing_tags(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    text = str(value).strip()
    return text in ("", "[]")


def _decode_raw_json(row: dict) -> dict:
    raw = row.get("raw_json") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _summary_body(kind: str, row: dict) -> tuple[str, str, str, str]:
    if kind == "tenders":
        body = summarize.tender_body(row)
        return row.get("title") or "", row.get("agency") or "", body, "標案"
    body = summarize.grant_body(row)
    return row.get("title") or "", row.get("agency") or "", body, "補助案"


def _make_job(kind: str, row: dict, field: str) -> EnrichJob:
    title, agency, body, label = _summary_body(kind, row)
    if field == "summary":
        prompt = summarize.build_prompt(body, kind=label)
    elif field == "tags":
        prompt = tagging.build_prompt(title, agency, body)
    elif field == "dissect" and kind == "tenders":
        prompt = dissect.build_prompt(row)
    else:
        raise ValueError(f"unsupported job: {kind}/{field}")
    return EnrichJob(kind=kind, row_id=int(row["id"]), field=field, title=title, prompt=prompt)


# kind 會被拼進 SQL 的資料表名稱，所以只准這兩個值，且在碰到資料庫之前就擋。
ALLOWED_KINDS = ("tenders", "grants")


def collect_jobs(kinds: list[str], fields: list[str]) -> list[EnrichJob]:
    for kind in kinds:
        if kind not in ALLOWED_KINDS:
            raise ValueError(
                f"unsupported kind {kind!r}; allowed kinds are {', '.join(ALLOWED_KINDS)}"
            )
    jobs: list[EnrichJob] = []
    with db.get_conn() as conn:
        for kind in kinds:
            allowed_fields = ["summary", "tags"]
            if kind == "tenders":
                allowed_fields.append("dissect")
            wanted = [field for field in fields if field in allowed_fields]
            rows = conn.execute(f"SELECT * FROM {kind} ORDER BY id DESC").fetchall()
            for sqlite_row in rows:
                row = dict(sqlite_row)
                row["raw_json"] = _decode_raw_json(row)
                for field in wanted:
                    if field == "tags" and _missing_tags(row.get("tags")):
                        jobs.append(_make_job(kind, row, field))
                    elif field in ("summary", "dissect") and _missing_text(row.get(field)):
                        jobs.append(_make_job(kind, row, field))
    return jobs


def count_jobs(jobs: list[EnrichJob]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        counts[job.kind][job.field] += 1
    return {kind: dict(fields) for kind, fields in sorted(counts.items())}


def print_counts(counts: dict[str, dict[str, int]]) -> None:
    print("Dry-run job counts by kind/field")
    for kind in ("tenders", "grants"):
        if kind not in counts:
            continue
        for field in ("summary", "tags", "dissect"):
            if field in counts[kind]:
                print(f"  {kind}.{field}: {counts[kind][field]}")


def print_health(results) -> None:
    print("Stage 0 worker health")
    for name, result in results.items():
        status = "OK" if result.ok else "FAIL"
        detail = result.text.strip() if result.ok else result.error.strip()
        print(f"  {name}: {status} ({detail[:220]})")


def run_health(workers: list[Worker]):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers) or 1) as pool:
        futures = {pool.submit(health_check, worker): worker.name for worker in workers}
        return {futures[fut]: fut.result() for fut in concurrent.futures.as_completed(futures)}


def _normalize(job: EnrichJob, text: str):
    parsed = codex_client.extract_json(text)
    if job.field == "summary":
        return summarize.normalize_result(parsed)
    if job.field == "tags":
        return tagging.normalize_result(parsed)
    if job.field == "dissect":
        return dissect.normalize_result(parsed)
    return None


def _write_result(job: EnrichJob, value: Any) -> None:
    with db.get_conn() as conn:
        if job.field == "summary":
            db.set_summary(conn, job.kind, job.row_id, value)
        elif job.field == "tags":
            db.set_tags(conn, job.kind, job.row_id, value)
        elif job.field == "dissect":
            db.set_dissect(conn, job.row_id, value)
        else:
            raise ValueError(f"unsupported field: {job.field}")


def _run_one(job: EnrichJob, worker: Worker) -> JobOutcome:
    result = run_codex(worker, job.prompt)
    if not result.ok:
        return JobOutcome(job, worker.name, False, error=result.error, attempts=result.attempts)
    value = _normalize(job, result.text)
    if value in (None, []):
        return JobOutcome(
            job,
            worker.name,
            False,
            error=f"invalid JSON for {job.kind}.{job.field}: {result.text[:300]}",
            attempts=result.attempts,
        )
    _write_result(job, value)
    return JobOutcome(job, worker.name, True, value=value, attempts=result.attempts)


def dispatch_jobs(jobs: list[EnrichJob], workers: list[Worker], concurrency: int) -> list[JobOutcome]:
    if not jobs or not workers:
        return []
    outcomes: list[JobOutcome] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {}
        for index, job in enumerate(jobs):
            worker = workers[index % len(workers)]
            futures[pool.submit(_run_one, job, worker)] = (job, worker.name)
        for fut in concurrent.futures.as_completed(futures):
            job, worker_name = futures[fut]
            try:
                outcomes.append(fut.result())
            except Exception as exc:  # keep dispatch running for other jobs
                outcomes.append(JobOutcome(job, worker_name, False, error=str(exc)))
    return outcomes


def write_report(
    *,
    stamp: str,
    counts: dict[str, dict[str, int]],
    health: dict,
    outcomes: list[JobOutcome],
    max_calls: int | None,
    dry_run: bool,
) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"enrich_run_{stamp}.md"
    distribution = Counter(outcome.worker for outcome in outcomes)
    ok_count = sum(1 for outcome in outcomes if outcome.ok)
    lines = [
        f"# Enrichment Run {stamp}",
        "",
        f"- dry_run: {dry_run}",
        f"- max_calls: {max_calls if max_calls is not None else 'none'}",
        f"- outcomes: {ok_count}/{len(outcomes)} ok",
        "",
        "## Dry-run Counts",
        "```json",
        json.dumps(counts, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Stage 0 Health",
        "| worker | status | detail |",
        "|---|---|---|",
    ]
    for name, result in health.items():
        status = "OK" if result.ok else "FAIL"
        detail = (result.text if result.ok else result.error).replace("\n", " ")[:500]
        lines.append(f"| {name} | {status} | {detail} |")
    lines += [
        "",
        "## Distribution",
        "```json",
        json.dumps(dict(distribution), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Outcomes",
    ]
    for outcome in outcomes:
        lines.append(
            f"- {outcome.worker} {outcome.job.kind}#{outcome.job.row_id} "
            f"{outcome.job.field}: {'OK' if outcome.ok else 'FAIL'}"
        )
        if not outcome.ok:
            lines.append(f"  error: {outcome.error[:500]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _sample_json(outcomes: list[JobOutcome]) -> dict[str, Any]:
    for outcome in outcomes:
        if outcome.ok:
            return {
                "worker": outcome.worker,
                "kind": outcome.job.kind,
                "id": outcome.job.row_id,
                "field": outcome.job.field,
                "value": outcome.value,
            }
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--health-only", action="store_true")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--kinds", default="tenders,grants")
    ap.add_argument("--fields", default="summary,tags,dissect")
    ap.add_argument("--workers", default="pc1,pc2,mac")
    ap.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    kinds = _parse_csv(args.kinds)
    fields = _parse_csv(args.fields)
    workers = select_workers(_parse_csv(args.workers))
    jobs = collect_jobs(kinds, fields)
    counts = count_jobs(jobs)

    if args.dry_run:
        print_counts(counts)
        write_report(
            stamp=args.stamp,
            counts=counts,
            health={},
            outcomes=[],
            max_calls=args.max_calls,
            dry_run=True,
        )
        return 0

    health = run_health(workers)
    print_health(health)
    if args.health_only:
        write_report(
            stamp=args.stamp,
            counts=counts,
            health=health,
            outcomes=[],
            max_calls=args.max_calls,
            dry_run=False,
        )
        return 0

    active_workers = [worker for worker in workers if health.get(worker.name) and health[worker.name].ok]
    if not active_workers:
        print("No healthy workers; no enrichment jobs were run.")
        write_report(
            stamp=args.stamp,
            counts=counts,
            health=health,
            outcomes=[],
            max_calls=args.max_calls,
            dry_run=False,
        )
        return 2

    selected_jobs = jobs[: args.max_calls] if args.max_calls is not None else jobs
    outcomes = dispatch_jobs(selected_jobs, active_workers, args.concurrency)
    distribution = Counter(outcome.worker for outcome in outcomes)
    print("Run distribution")
    for worker in [worker.name for worker in active_workers]:
        print(f"  {worker}: {distribution.get(worker, 0)}")
    print("Sample enriched JSON written to DB")
    print(json.dumps(_sample_json(outcomes), ensure_ascii=False, indent=2))
    report = write_report(
        stamp=args.stamp,
        counts=counts,
        health=health,
        outcomes=outcomes,
        max_calls=args.max_calls,
        dry_run=False,
    )
    ok_count = sum(1 for outcome in outcomes if outcome.ok)
    print(f"Report: {report}")
    print(f"Completed: {ok_count}/{len(outcomes)}")
    return 0 if ok_count == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
