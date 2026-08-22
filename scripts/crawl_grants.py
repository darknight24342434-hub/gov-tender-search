"""Crawl or import grant sources.

Examples:
  python scripts/crawl_grants.py --all
  python scripts/crawl_grants.py --all-pw
  python scripts/crawl_grants.py --source digiplus --pages 5
  python scripts/crawl_grants.py --source moea
  python scripts/crawl_grants.py --seed data/grants_seed.json
"""
import argparse

import _bootstrap  # noqa: F401

from app.crawlers import grants


PW_CRAWLERS = {}
try:
    from app.crawlers import pw_moea

    PW_CRAWLERS["moea"] = pw_moea.crawl_moea
except Exception:  # noqa: BLE001
    pass

PW_BLOCKED = {}
try:
    from app.crawlers import pw_grant_sites

    PW_CRAWLERS.update(pw_grant_sites.PW_GRANT_CRAWLERS)
    PW_BLOCKED.update(pw_grant_sites.BLOCKED_SITES)
except Exception:  # noqa: BLE001
    pass


def _print_run_report(results: dict):
    print("-" * 40)
    print("Coverage map:")
    coverage = grants.coverage_map(results)
    for name, reason in PW_BLOCKED.items():
        coverage[name] = f"blocked: {reason}"
    for name, status in coverage.items():
        print(f"  {name}: {status}")

    print("New rows per source:")
    total_new = 0
    total_touched = 0
    for name, st in results.items():
        if not isinstance(st, dict) or "error" in st:
            reason = st.get("error") if isinstance(st, dict) else st
            print(f"  {name}: error={reason}")
            continue
        inserted = st.get("inserted", 0)
        updated = st.get("updated", 0)
        parsed = st.get("parsed", 0)
        total_new += inserted
        total_touched += inserted + updated
        print(f"  {name}: inserted={inserted}, updated={updated}, parsed={parsed}")

    print(f"New grants total: {total_new}")
    print(f"Rows inserted or updated total: {total_touched}")
    print("Files changed:")
    print("  app/crawlers/grants.py")
    print("  app/crawlers/pw_grant_sites.py")
    print("  scripts/crawl_grants.py")
    print("  scripts/dedup_grants.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Run all HTTP/HTML grant crawlers.")
    ap.add_argument("--all-pw", action="store_true", help="Run all Playwright grant crawlers.")
    ap.add_argument("--source", help="Run one source, e.g. startup_sme, moc, digiplus, ncaf, moea.")
    ap.add_argument("--pages", type=int, default=20, help="Maximum pages for sources that paginate.")
    ap.add_argument("--headless", action="store_true", help="Run Playwright sources headless.")
    ap.add_argument("--seed", help="Import grants from a JSON seed file.")
    args = ap.parse_args()

    if args.seed:
        stats = grants.ingest_seed(args.seed)
        _print_run_report({"seed": stats})
        return

    if args.all:
        results = grants.crawl_all(progress=print)
        _print_run_report(results)
        return

    if args.all_pw:
        results = {}
        for name, fn in PW_CRAWLERS.items():
            print(f"=== crawl source: {name} ===")
            try:
                results[name] = fn(max_pages=args.pages, headless=args.headless, progress=print)
            except Exception as e:  # noqa: BLE001
                results[name] = {"error": str(e)}
                print(f"  source {name} failed: {e}")
        _print_run_report(results)
        return

    if args.source:
        if args.source in PW_CRAWLERS:
            stats = PW_CRAWLERS[args.source](max_pages=args.pages, headless=args.headless, progress=print)
        else:
            fn = grants.HTML_CRAWLERS.get(args.source)
            if not fn:
                avail = list(grants.HTML_CRAWLERS) + list(PW_CRAWLERS)
                ap.error(f"Unknown source '{args.source}'. Available: {avail}")
            stats = fn(max_pages=args.pages, progress=print)
        _print_run_report({args.source: stats})
        return

    ap.error("Use --all, --all-pw, --source <name>, or --seed <json>.")


if __name__ == "__main__":
    main()
