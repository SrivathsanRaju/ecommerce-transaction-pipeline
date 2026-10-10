import boto3
import pandas as pd
import json
import os
import argparse
from datetime import date, datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--run-date", type=str, default=None, help="Date in YYYY-MM-DD format")
args = parser.parse_args()

BUCKET_NAME = "sriv-de"
today = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()

s3_silver_key = f"silver/year={today.year}/month={today.month:02d}/day={today.day:02d}/transactions.parquet"
s3_gold_flagged_prefix = f"gold/flagged_transactions/year={today.year}/month={today.month:02d}/day={today.day:02d}/"
s3_gold_summary_prefix = f"gold/transaction_summary/year={today.year}/month={today.month:02d}/day={today.day:02d}/"

LOCAL_TMP = "tmp_pipeline"
LOCAL_SILVER_FILE = f"{LOCAL_TMP}/silver_transactions.parquet"
LOCAL_FLAGGED_FILE = f"{LOCAL_TMP}/flagged_transactions.parquet"
LOCAL_SUMMARY_FILE = f"{LOCAL_TMP}/transaction_summary.json"

os.makedirs(LOCAL_TMP, exist_ok=True)
s3 = boto3.client("s3")

# ---- Step 1: Download clean silver data ----
s3.download_file(BUCKET_NAME, s3_silver_key, LOCAL_SILVER_FILE)
df = pd.read_parquet(LOCAL_SILVER_FILE)
print(f"Loaded {len(df)} clean records from silver")

# ---- Step 2: Amount outliers ----
mean_amount = df["amount"].mean()
std_amount = df["amount"].std()
df["amount_zscore"] = (df["amount"] - mean_amount) / std_amount
df["flag_amount_outlier"] = df["amount_zscore"].abs() > 3

# ---- Step 3: Transaction velocity ----
user_txn_counts = df.groupby("user_id")["transaction_id"].transform("count")
df["user_transaction_count"] = user_txn_counts
velocity_threshold = df["user_transaction_count"].quantile(0.99)
df["flag_high_velocity"] = df["user_transaction_count"] > velocity_threshold

# ---- Step 4: Combine flags ----
df["is_flagged"] = df["flag_amount_outlier"] | df["flag_high_velocity"]
flagged_df = df[df["is_flagged"]].copy()

print(f"Flagged {len(flagged_df)} of {len(df)} transactions ({round(len(flagged_df)/len(df)*100, 2)}%)")

# ---- Step 5: Write flagged records ----
flagged_df.to_parquet(LOCAL_FLAGGED_FILE, engine="pyarrow", index=False)
s3.upload_file(LOCAL_FLAGGED_FILE, BUCKET_NAME, s3_gold_flagged_prefix + "flagged_transactions.parquet")
print(f"Uploaded flagged transactions to s3://{BUCKET_NAME}/{s3_gold_flagged_prefix}")

# ---- Step 6: Build and write summary ----
summary = {
    "run_timestamp": datetime.now(timezone.utc).isoformat(),
    "total_transactions": len(df),
    "flagged_transactions": len(flagged_df),
    "flagged_rate_pct": round(len(flagged_df) / len(df) * 100, 2),
    "amount_outlier_count": int(df["flag_amount_outlier"].sum()),
    "high_velocity_count": int(df["flag_high_velocity"].sum()),
    "total_transaction_value": round(df["amount"].sum(), 2),
    "average_transaction_value": round(df["amount"].mean(), 2),
    "note": "Fraud-signal rules are illustrative, threshold-based heuristics on synthetic data — not validated against real fraud labels."
}

print("Transaction summary:")
print(json.dumps(summary, indent=2))

with open(LOCAL_SUMMARY_FILE, "w") as f:
    json.dump(summary, f, indent=2)

s3.upload_file(LOCAL_SUMMARY_FILE, BUCKET_NAME, s3_gold_summary_prefix + "summary.json")
print(f"Uploaded summary to s3://{BUCKET_NAME}/{s3_gold_summary_prefix}")

print("Done.")