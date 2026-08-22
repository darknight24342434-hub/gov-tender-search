# -*- coding: utf-8 -*-
import _bootstrap  # noqa: F401

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from app import db
from app.crawlers import pcc_g0v


CANDIDATE_SQL = """
SELECT id, unit_id, job_number, filename, type
FROM tenders
WHERE tags LIKE '%教育%'
  AND (deadline IS NULL OR deadline='')
  AND unit_id IS NOT NULL AND unit_id<>''
  AND job_number IS NOT NULL AND job_number<>''
  AND type IS NOT NULL AND type NOT LIKE '%決標%'
  AND (type LIKE '%招標%' OR type LIKE '%公開取得%' OR type LIKE '%資格%' OR type LIKE '%報價%')
ORDER BY id
"""

UPDATE_SQL = """
UPDATE tenders
SET deadline=?, deadline_time=?, budget=COALESCE(?,budget), updated_at=?
WHERE id=?
"""

BACKOFF_SECONDS = (30, 60, 120, 240)
COMMIT_EVERY = 20


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill deadline/deadline_time for education tenders."
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum rows to scan.")
    return parser.parse_args()


def _delay_seconds() -> float:
    raw = os.getenv("BACKFILL_DELAY", "6.0")
    try:
        delay = float(raw)
    except ValueError:
        raise SystemExit(f"Invalid BACKFILL_DELAY: {raw!r}") from None
    if delay < 0:
        raise SystemExit("BACKFILL_DELAY must be >= 0")
    return delay


def _is_429(exc: httpx.HTTPStatusError) -> bool:
    return exc.response is not None and exc.response.status_code == 429


def _fetch_detail_with_429_backoff(client: httpx.Client, unit_id: str, job_number: str):
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            return pcc_g0v.fetch_detail(client, unit_id, job_number), False
        except httpx.HTTPStatusError as exc:
            if not _is_429(exc):
                raise
            if attempt >= len(BACKOFF_SECONDS):
                return None, True
            sleep_for = BACKOFF_SECONDS[attempt]
            print(f"429 {job_number}: sleep {sleep_for}s before retry {attempt + 1}")
            time.sleep(sleep_for)
    return None, True


def _candidate_rows(conn, limit: int | None):
    sql = CANDIDATE_SQL
    params = ()
    if limit is not None:
        sql += "\nLIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def _write_sentinel(stats_line: str) -> None:
    sentinel = Path("_派工") / "DONE_教育截止日backfill.txt"
    stamp = datetime.now().isoformat(timespec="seconds")
    sentinel.write_text(f"{stamp} {stats_line}\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    delay = _delay_seconds()
    stats = {
        "filled": 0,
        "no_deadline_found": 0,
        "skipped_429": 0,
        "errors": 0,
        "scanned": 0,
    }

    with pcc_g0v._client() as client, db.get_conn() as conn:
        rows = _candidate_rows(conn, args.limit)
        last_detail_at = None

        for row in rows:
            stats["scanned"] += 1
            if not pcc_g0v._is_tender_type(row["type"]):
                continue

            if last_detail_at is not None:
                elapsed = time.monotonic() - last_detail_at
                remaining = delay - elapsed
                if remaining > 0:
                    time.sleep(remaining)

            try:
                detail_resp, skipped_429 = _fetch_detail_with_429_backoff(
                    client, row["unit_id"], row["job_number"]
                )
                last_detail_at = time.monotonic()
                if skipped_429:
                    stats["skipped_429"] += 1
                    continue

                fields = pcc_g0v._extract_detail_fields(detail_resp, row["filename"])
                deadline = fields.get("deadline")
                if not deadline:
                    stats["no_deadline_found"] += 1
                    continue

                conn.execute(
                    UPDATE_SQL,
                    (
                        deadline,
                        fields.get("deadline_time"),
                        fields.get("budget"),
                        datetime.now().isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                stats["filled"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"error id={row['id']} job_number={row['job_number']}: {exc}")

            if stats["scanned"] % COMMIT_EVERY == 0:
                conn.commit()
                print(
                    "progress "
                    f"filled={stats['filled']} "
                    f"no_deadline_found={stats['no_deadline_found']} "
                    f"skipped_429={stats['skipped_429']} "
                    f"errors={stats['errors']} "
                    f"scanned={stats['scanned']}"
                )

        conn.commit()

    stats_line = (
        f"filled={stats['filled']} "
        f"no_deadline_found={stats['no_deadline_found']} "
        f"skipped_429={stats['skipped_429']} "
        f"errors={stats['errors']} "
        f"scanned={stats['scanned']}"
    )
    print(stats_line)
    if args.limit is None:
        _write_sentinel(stats_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
