"""
Scores the fraud flags against the planted ground-truth labels for one run date.

Reads:  silver transactions, gold flagged transactions, labels (all from S3)
Writes: gold/detection_eval/.../eval.json  (and prints a readable report)
"""
import argparse
import json
import os
from datetime import date, datetime, timezone

import boto3
import pandas as pd

BUCKET_NAME = "sriv-de"


def pct(part, whole):
    return round(part / whole * 100, 2) if whole else None


def evaluate(silver_df, flagged_df, labels_df):
    """Compare flagged rows with planted fraud rows. Pure function, easy to test."""
    fraud_ids = set(labels_df["transaction_id"])
    silver_ids = set(silver_df["transaction_id"])
    flagged_ids = set(flagged_df["transaction_id"])
    caught_ids = flagged_ids & fraud_ids

    result = {
        "flagged_rows": len(flagged_ids),
        "planted_fraud_rows": len(fraud_ids),
        "caught": len(caught_ids),
        "false_positives": len(flagged_ids - fraud_ids),
        "precision_pct": pct(len(caught_ids), len(flagged_ids)),   # of flags, how many were real
        "recall_pct": pct(len(caught_ids), len(fraud_ids)),        # of real fraud, how much was flagged
        "by_type": {},
    }

    for fraud_type, group in labels_df.groupby("fraud_type"):
        ids = set(group["transaction_id"])
        scenarios = group.groupby("scenario_id")["transaction_id"].apply(set)
        detected = sum(1 for rows in scenarios if rows & flagged_ids)   # >=1 row flagged
        result["by_type"][fraud_type] = {
            "planted_rows": len(ids),
            "lost_in_cleaning": len(ids - silver_ids),    # dropped before detection even ran
            "caught_rows": len(ids & flagged_ids),
            "row_recall_pct": pct(len(ids & flagged_ids), len(ids)),
            "scenarios": len(scenarios),
            "scenarios_detected": detected,
        }
    return result


def print_report(run_date, r):
    print(f"\nDetection report for {run_date}")
    print(f"  flagged {r['flagged_rows']} rows | planted fraud {r['planted_fraud_rows']} rows")
    print(f"  precision {r['precision_pct']}%  recall {r['recall_pct']}%  false positives {r['false_positives']}")
    print(f"  {'fraud type':<22}{'rows':>6}{'caught':>8}{'recall%':>9}{'scenarios caught':>18}")
    for name, t in r["by_type"].items():
        print(f"  {name:<22}{t['planted_rows']:>6}{t['caught_rows']:>8}"
              f"{str(t['row_recall_pct']):>9}{t['scenarios_detected']:>10}/{t['scenarios']}")


def s3_key(prefix, run_date, filename):
    return f"{prefix}/year={run_date.year}/month={run_date.month:02d}/day={run_date.day:02d}/{filename}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", type=str, default=None, help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()

    os.makedirs("tmp_pipeline", exist_ok=True)
    s3 = boto3.client("s3")

    s3.download_file(BUCKET_NAME, s3_key("silver", run_date, "transactions.parquet"), "tmp_pipeline/eval_silver.parquet")
    s3.download_file(BUCKET_NAME, s3_key("gold/flagged_transactions", run_date, "flagged_transactions.parquet"), "tmp_pipeline/eval_flagged.parquet")
    s3.download_file(BUCKET_NAME, s3_key("labels", run_date, "labels.json"), "tmp_pipeline/eval_labels.json")

    silver_df = pd.read_parquet("tmp_pipeline/eval_silver.parquet")
    flagged_df = pd.read_parquet("tmp_pipeline/eval_flagged.parquet")
    labels_df = pd.read_json("tmp_pipeline/eval_labels.json", lines=True)

    result = evaluate(silver_df, flagged_df, labels_df)
    result["run_date"] = run_date.isoformat()
    result["run_timestamp"] = datetime.now(timezone.utc).isoformat()
    print_report(run_date, result)

    with open("tmp_pipeline/detection_eval.json", "w") as f:
        json.dump(result, f, indent=2)
    key = s3_key("gold/detection_eval", run_date, "eval.json")
    s3.upload_file("tmp_pipeline/detection_eval.json", BUCKET_NAME, key)
    print(f"\nUploaded evaluation to s3://{BUCKET_NAME}/{key}")


if __name__ == "__main__":
    main()
