"""
Real data export for the pipeline health dashboard.

Matches the actual schema of the serving-db (verified against the live
tables on 2026-09-26):

  pipeline_health      — one row per pipeline run
  transaction_summary  — one row per pipeline run
  flagged_transactions — one row per individual flagged transaction

Run this after your Airflow DAG completes (daily_update.py calls it once
it sees the .pipeline_done_<date> marker). Set DATABASE_URL first, e.g.:

  $env:DATABASE_URL = "postgresql://pipeline_user:pipeline_pass@localhost:5433/ecommerce_pipeline"
"""
import json
import os
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

DB_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://pipeline_user:pipeline_pass@localhost:5433/ecommerce_pipeline",
)
LOOKBACK_DAYS = 30
RECENT_FLAGGED_LIMIT = 25
OUT_PATH = os.path.join(os.path.dirname(__file__), "data.json")


def fetch_all(cur, query, params=None):
    cur.execute(query, params or {})
    return [dict(row) for row in cur.fetchall()]


def main():
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    conn = psycopg2.connect(DB_DSN, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            pipeline_health = fetch_all(cur, """
                SELECT
                    run_timestamp,
                    stage,
                    raw_row_count,
                    clean_row_count,
                    rows_dropped,
                    null_amount_count,
                    null_location_count,
                    bad_payment_method_count,
                    duplicate_count,
                    null_rate_pct,
                    duplicate_rate_pct
                FROM pipeline_health
                WHERE run_timestamp >= %(since)s
                ORDER BY run_timestamp
            """, {"since": since})

            transaction_summary = fetch_all(cur, """
                SELECT
                    run_timestamp,
                    total_transactions,
                    flagged_transactions,
                    flagged_rate_pct,
                    amount_outlier_count,
                    high_velocity_count,
                    total_transaction_value,
                    average_transaction_value,
                    note
                FROM transaction_summary
                WHERE run_timestamp >= %(since)s
                ORDER BY run_timestamp
            """, {"since": since})

            recent_flagged = fetch_all(cur, """
                SELECT
                    transaction_id,
                    user_id,
                    amount,
                    currency,
                    location,
                    merchant_category,
                    payment_method,
                    timestamp,
                    amount_zscore,
                    flag_amount_outlier,
                    flag_high_velocity
                FROM flagged_transactions
                WHERE is_flagged = true
                ORDER BY timestamp DESC
                LIMIT %(limit)s
            """, {"limit": RECENT_FLAGGED_LIMIT})
    finally:
        conn.close()

    now = datetime.now()
    data = {
        "generated_at": now.isoformat(),
        "generated_date": now.date().isoformat(),  # daily_update.py checks this
        "pipeline_health": pipeline_health,
        "transaction_summary": transaction_summary,
        "recent_flagged": recent_flagged,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"Wrote {OUT_PATH}: "
          f"{len(pipeline_health)} pipeline_health rows, "
          f"{len(transaction_summary)} transaction_summary rows, "
          f"{len(recent_flagged)} recent flagged transactions.")


if __name__ == "__main__":
    main()
