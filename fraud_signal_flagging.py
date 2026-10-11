import boto3
import numpy as np
import pandas as pd
import json
import os
import argparse
from datetime import date, datetime, timezone

# ---- Rule settings (tune these, then re-run evaluate_detection.py to see the effect) ----
Z_THRESHOLD = 3                 # amount outlier: |z-score| above this
BURST_WINDOW_MIN = 5            # velocity: look +/- 5 minutes around each transaction
BURST_MIN_TXNS = 5              # ... flag if the same user made >= 5 transactions in that window
FAN_IN_WINDOW_MIN = 90          # fan-in: look at payments to the same person within 90 minutes
FAN_IN_MIN_PAYERS = 3           # ... flag if >= 3 different people paid them
PASS_THROUGH_MAX_MIN = 15       # pass-through: money received then forwarded within 15 minutes
PASS_THROUGH_MIN_RATIO = 0.80   # ... forwarding at least 80% of it (a small cut is kept)


# ---------------------------------------------------------------------------
# Rules. Each takes the cleaned DataFrame and returns True/False per transaction.
# ---------------------------------------------------------------------------
def flag_amount_outliers(df):
    """Unusually large amount compared with the day's average (z-score)."""
    zscore = (df["amount"] - df["amount"].mean()) / df["amount"].std()
    return zscore, zscore.abs() > Z_THRESHOLD


def flag_bursts(df):
    """Same user, many transactions within a few minutes (card-testing / account drain)."""
    window = np.timedelta64(BURST_WINDOW_MIN, "m")
    flags = pd.Series(False, index=df.index)
    for _, group in df.groupby("user_id"):
        if len(group) < BURST_MIN_TXNS:
            continue
        group = group.sort_values("_ts")
        ts = group["_ts"].values
        start = np.searchsorted(ts, ts - window, side="left")
        end = np.searchsorted(ts, ts + window, side="right")
        flags.loc[group.index] = (end - start) >= BURST_MIN_TXNS
    return flags


def flag_fan_in(df):
    """One person receives P2P payments from several different people in a short time."""
    window = np.timedelta64(FAN_IN_WINDOW_MIN, "m")
    flags = pd.Series(False, index=df.index)
    p2p = df[df["merchant_category"] == "p2p_transfer"]
    for _, group in p2p.groupby("payee_id"):
        if group["user_id"].nunique() < FAN_IN_MIN_PAYERS:
            continue
        ts, payers = group["_ts"].values, group["user_id"].values
        for i, row_index in enumerate(group.index):
            nearby = np.abs(ts - ts[i]) <= window
            if len(set(payers[nearby])) >= FAN_IN_MIN_PAYERS:
                flags.loc[row_index] = True
    return flags


def flag_pass_through(df):
    """Money arrives in an account and is forwarded almost in full within minutes (mule chain)."""
    flags = pd.Series(False, index=df.index)
    p2p = df[df["merchant_category"] == "p2p_transfer"]
    cols = ["transaction_id", "user_id", "payee_id", "amount", "_ts"]
    # match every incoming payment (payee = X) with X's own outgoing payments (user = X)
    pairs = p2p[cols].merge(p2p[cols], left_on="payee_id", right_on="user_id", suffixes=("_in", "_out"))
    seconds = (pairs["_ts_out"] - pairs["_ts_in"]).dt.total_seconds()
    ratio = pairs["amount_out"] / pairs["amount_in"]
    hits = pairs[(seconds >= 0) & (seconds <= PASS_THROUGH_MAX_MIN * 60)
                 & (ratio >= PASS_THROUGH_MIN_RATIO) & (ratio <= 1.0)
                 & (pairs["transaction_id_in"] != pairs["transaction_id_out"])]
    hit_ids = set(hits["transaction_id_in"]) | set(hits["transaction_id_out"])
    flags[df["transaction_id"].isin(hit_ids)] = True
    return flags


def detect(df):
    """Run all rules. Returns the DataFrame with one flag column per rule."""
    df = df.copy()
    df["_ts"] = pd.to_datetime(df["timestamp"])

    df["amount_zscore"], df["flag_amount_outlier"] = flag_amount_outliers(df)
    df["user_transaction_count"] = df.groupby("user_id")["transaction_id"].transform("count")
    df["flag_high_velocity"] = flag_bursts(df)

    # the two "money movement" rules need payee_id + category (older silver files don't have them)
    if {"payee_id", "merchant_category"}.issubset(df.columns):
        df["flag_fan_in"] = flag_fan_in(df)
        df["flag_pass_through"] = flag_pass_through(df)
    else:
        df["flag_fan_in"] = False
        df["flag_pass_through"] = False

    rule_columns = ["flag_amount_outlier", "flag_high_velocity", "flag_fan_in", "flag_pass_through"]
    df["is_flagged"] = df[rule_columns].any(axis=1)
    return df.drop(columns=["_ts"])


def main():
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

    # ---- Step 2: Apply all rules ----
    df = detect(df)
    flagged_df = df[df["is_flagged"]].copy()
    print(f"Flagged {len(flagged_df)} of {len(df)} transactions ({round(len(flagged_df)/len(df)*100, 2)}%)")
    for rule in ["flag_amount_outlier", "flag_high_velocity", "flag_fan_in", "flag_pass_through"]:
        print(f"  {rule}: {int(df[rule].sum())}")

    # ---- Step 3: Write flagged records ----
    flagged_df.to_parquet(LOCAL_FLAGGED_FILE, engine="pyarrow", index=False)
    s3.upload_file(LOCAL_FLAGGED_FILE, BUCKET_NAME, s3_gold_flagged_prefix + "flagged_transactions.parquet")
    print(f"Uploaded flagged transactions to s3://{BUCKET_NAME}/{s3_gold_flagged_prefix}")

    # ---- Step 4: Build and write summary ----
    # NOTE: keys are unchanged on purpose - the Postgres table has fixed columns and appends daily.
    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_transactions": len(df),
        "flagged_transactions": len(flagged_df),
        "flagged_rate_pct": round(len(flagged_df) / len(df) * 100, 2),
        "amount_outlier_count": int(df["flag_amount_outlier"].sum()),
        "high_velocity_count": int(df["flag_high_velocity"].sum()),
        "total_transaction_value": round(df["amount"].sum(), 2),
        "average_transaction_value": round(df["amount"].mean(), 2),
        "note": "Rule-based flags (amount outlier, burst velocity, fan-in, pass-through) scored against planted synthetic fraud labels - see detection_eval."
    }

    print("Transaction summary:")
    print(json.dumps(summary, indent=2))

    with open(LOCAL_SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)

    s3.upload_file(LOCAL_SUMMARY_FILE, BUCKET_NAME, s3_gold_summary_prefix + "summary.json")
    print(f"Uploaded summary to s3://{BUCKET_NAME}/{s3_gold_summary_prefix}")

    print("Done.")


if __name__ == "__main__":
    main()