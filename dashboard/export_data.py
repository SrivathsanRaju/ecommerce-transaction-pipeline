"""
Real data export for the pipeline health dashboard.

Matches the actual schema of the serving-db:

  pipeline_health      - one row per run date
  transaction_summary  - one row per run date
  detection_eval       - one row per run date: how well the fraud rules did vs planted labels
  flagged_transactions - one row per individual flagged transaction (latest run only)

Run this after the pipeline loads Postgres. Set DATABASE_URL first, e.g.:

  $env:DATABASE_URL = "postgresql://pipeline_user:pipeline_pass@localhost:5433/ecommerce_pipeline"
"""
import json
import os
from datetime import datetime, timedelta, timezone
import psycopg2
import psycopg2.extras

DB_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://pipeline_user:pipeline_pass@localhost:5433/ecommerce_pipeline",
)
LOOKBACK_DAYS = 30
RECENT_FLAGGED_LIMIT = 25
OUT_PATH = os.path.join(os.path.dirname(__file__), "data.json")

# columns the dashboard shows for each flagged transaction
FLAGGED_KEEP = [
    "transaction_id", "user_id", "payee_id", "amount", "currency", "location",
    "merchant_category", "payment_method", "timestamp", "amount_zscore",
    "flag_amount_outlier", "flag_high_velocity", "flag_fan_in", "flag_pass_through",
]


def fetch_all(cur, query, params=None):
    cur.execute(query, params or {})
    return [dict(row) for row in cur.fetchall()]


def table_exists(cur, name):
    cur.execute("SELECT to_regclass(%(n)s) IS NOT NULL AS ok", {"n": name})
    return cur.fetchone()["ok"]


def main():
    since_dt = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    since = since_dt.isoformat()
    since_date = since_dt.date().isoformat()

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

            # detection quality (table appears after the first run that evaluates detection)
            detection_eval = []
            if table_exists(cur, "detection_eval"):
                detection_eval = fetch_all(cur, """
                    SELECT
                        run_timestamp,
                        run_date,
                        flagged_rows,
                        planted_fraud_rows,
                        caught,
                        false_positives,
                        precision_pct,
                        recall_pct,
                        by_type,
                        by_rule
                    FROM detection_eval
                    WHERE run_date >= %(since_date)s
                    ORDER BY run_date
                """, {"since_date": since_date})
                for row in detection_eval:           # stored as JSON text -> real objects
                    for key in ("by_type", "by_rule"):
                        if isinstance(row.get(key), str):
                            row[key] = json.loads(row[key])

            flagged_rows = fetch_all(cur, """
                SELECT *
                FROM flagged_transactions
                WHERE is_flagged = true
                ORDER BY timestamp DESC
                LIMIT %(limit)s
            """, {"limit": RECENT_FLAGGED_LIMIT})
            recent_flagged = [{k: r.get(k) for k in FLAGGED_KEEP} for r in flagged_rows]
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    data = {
        "generated_at": now.isoformat(),
        "generated_date": now.date().isoformat(),
        "pipeline_health": pipeline_health,
        "transaction_summary": transaction_summary,
        "detection_eval": detection_eval,
        "recent_flagged": recent_flagged,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"Wrote {OUT_PATH}: "
          f"{len(pipeline_health)} pipeline_health rows, "
          f"{len(transaction_summary)} transaction_summary rows, "
          f"{len(detection_eval)} detection_eval rows, "
          f"{len(recent_flagged)} recent flagged transactions.")


if __name__ == "__main__":
    main()