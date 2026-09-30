import boto3
import pandas as pd
from sqlalchemy import create_engine
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

# Prefer DATABASE_URL from the environment (set in GitHub Actions to point at
# the cloud Postgres). Falls back to the original local Docker settings so
# this still runs unchanged from Airflow on the laptop.
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
else:
    DB_USER = "pipeline_user"
    DB_PASS = "pipeline_pass"
    DB_HOST = "localhost"
    DB_PORT = "5433"
    DB_NAME = "ecommerce_pipeline"
    engine = create_engine(f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

s3 = boto3.client("s3")


def load_parquet_from_s3(key: str) -> pd.DataFrame:
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
    return pd.read_parquet(io.BytesIO(obj["Body"].read()))


def load_json_from_s3(key: str) -> pd.DataFrame:
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
    data = json.loads(obj["Body"].read())
    return pd.DataFrame([data])


flagged_key = f"gold/flagged_transactions/year={today.year}/month={today.month:02d}/day={today.day:02d}/flagged_transactions.parquet"
summary_key = f"gold/transaction_summary/year={today.year}/month={today.month:02d}/day={today.day:02d}/summary.json"
health_key = f"gold/pipeline_health/year={today.year}/month={today.month:02d}/day={today.day:02d}/health.json"

with engine.begin() as conn:
    flagged_df = load_parquet_from_s3(flagged_key)
    flagged_df.to_sql("flagged_transactions", conn, if_exists="replace", index=False)
    print(f"Loaded {len(flagged_df)} rows into flagged_transactions")

    summary_df = load_json_from_s3(summary_key)
    summary_df.to_sql("transaction_summary", conn, if_exists="replace", index=False)
    print(f"Loaded {len(summary_df)} rows into transaction_summary")

    health_df = load_json_from_s3(health_key)
    health_df.to_sql("pipeline_health", conn, if_exists="replace", index=False)
    print(f"Loaded {len(health_df)} rows into pipeline_health")

print("\nAll gold-layer data loaded into Postgres successfully.")