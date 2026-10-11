import boto3
import pandas as pd
from sqlalchemy import create_engine, inspect, text
import argparse
import os
from datetime import date, datetime
import io
import json

parser = argparse.ArgumentParser()
parser.add_argument("--run-date", type=str, default=None, help="Date in YYYY-MM-DD format")
args = parser.parse_args()

BUCKET_NAME = "sriv-de"
today = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL)
else:
    DB_USER = "pipeline_user"
    DB_PASS = "pipeline_pass"
    DB_HOST = "localhost"
    DB_PORT = "5433"
    DB_NAME = "ecommerce_pipeline"
    engine = create_engine(f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

s3 = boto3.client("s3")

HISTORY_TABLES = ["transaction_summary", "pipeline_health"]


def load_parquet_from_s3(key: str) -> pd.DataFrame:
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
    return pd.read_parquet(io.BytesIO(obj["Body"].read()))


def load_json_from_s3(key: str) -> pd.DataFrame:
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
    data = json.loads(obj["Body"].read())
    return pd.DataFrame([data])


def ensure_run_date_column(conn, table: str):
    """History tables get a run_date column (one-time upgrade; safe to run every time)."""
    if not inspect(conn).has_table(table):
        return  # first ever run: to_sql will create the table, run_date included
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS run_date TEXT"))
    # old rows: derive the date from their timestamp (first 10 chars of the ISO string)
    conn.execute(text(
        f"UPDATE {table} SET run_date = substring(run_timestamp::text from 1 for 10) "
        f"WHERE run_date IS NULL"
    ))


def replace_day(conn, table: str, df: pd.DataFrame):
    """Idempotent load: remove this run date's rows, then insert the fresh ones."""
    df = df.copy()
    df["run_date"] = today.isoformat()
    if inspect(conn).has_table(table):
        deleted = conn.execute(
            text(f"DELETE FROM {table} WHERE run_date = :d"), {"d": today.isoformat()}
        ).rowcount
        if deleted:
            print(f"Removed {deleted} existing row(s) for {today} from {table}")
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"Inserted {len(df)} row(s) into {table} for {today}")


flagged_key = f"gold/flagged_transactions/year={today.year}/month={today.month:02d}/day={today.day:02d}/flagged_transactions.parquet"
summary_key = f"gold/transaction_summary/year={today.year}/month={today.month:02d}/day={today.day:02d}/summary.json"
health_key = f"gold/pipeline_health/year={today.year}/month={today.month:02d}/day={today.day:02d}/health.json"

# Read everything from S3 first, so a missing file fails before we touch the database
flagged_df = load_parquet_from_s3(flagged_key)
summary_df = load_json_from_s3(summary_key)
health_df = load_json_from_s3(health_key)

# One transaction: if anything fails, the delete is rolled back too (never half-loaded)
with engine.begin() as conn:
    # flagged_transactions: replaced each run - only the latest flagged list is needed
    # (export_data.py just takes the most recent 25), so it stays small.
    flagged_df.to_sql("flagged_transactions", conn, if_exists="replace", index=False)
    print(f"Loaded {len(flagged_df)} rows into flagged_transactions")

    # transaction_summary and pipeline_health: history accumulates, one row per run date.
    # Re-running the same date replaces that date's row instead of duplicating it.
    for table in HISTORY_TABLES:
        ensure_run_date_column(conn, table)
    replace_day(conn, "transaction_summary", summary_df)
    replace_day(conn, "pipeline_health", health_df)

print("\nAll gold-layer data loaded into Postgres successfully.")