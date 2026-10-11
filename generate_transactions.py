"""
UPI-style transaction simulator with planted, labeled fraud scenarios.

Writes two things per run date:
  1. bronze/.../transactions.json   -> what the pipeline sees (NO fraud labels in it)
  2. labels/.../labels.json         -> ground truth, kept separate so detection can't "peek"
"""
import argparse
import json
import math
import os
import random
import uuid
from collections import Counter
from datetime import date, datetime, timedelta

import boto3

# ---- CONFIG ----
BUCKET_NAME = "sriv-de"

LOCATIONS = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Kolkata", "Jaipur"]
PAYMENT_WEIGHTS = {"upi": 62, "credit_card": 14, "debit_card": 12, "wallet": 7, "net_banking": 5}
CATEGORY_WEIGHTS = {"groceries": 22, "food_delivery": 20, "fashion": 14,
                    "utilities": 12, "travel": 10, "electronics": 7}
CATEGORY_MEDIAN_INR = {"groceries": 450, "food_delivery": 380, "fashion": 1800,
                       "utilities": 1200, "travel": 4500, "electronics": 9000}

# Busier in the evening, quiet at night (index = hour of day, 24 values)
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 7,
                9, 8, 6, 6, 7, 8, 10, 12, 12, 10, 6, 3]

# EDIT: put the real festive-sale dates here (volume goes up 1.8x inside a window)
FESTIVE_WINDOWS = [(date(2026, 11, 5), date(2026, 11, 9))]


# ---- small helpers ----
def weighted_choice(table):
    return random.choices(list(table), weights=list(table.values()))[0]


def midnight(run_date):
    return datetime.combine(run_date, datetime.min.time())


def random_timestamp(run_date):
    hour = random.choices(range(24), weights=HOUR_WEIGHTS)[0]
    return midnight(run_date) + timedelta(
        hours=hour, minutes=random.randint(0, 59), seconds=random.randint(0, 59)
    )


def make_record(user_id, payee_id, amount, ts, location, method, category):
    return {
        "transaction_id": str(uuid.uuid4()),
        "user_id": user_id,
        "payee_id": payee_id,
        "amount": round(amount, 2),
        "currency": "INR",
        "timestamp": ts.isoformat(),
        "location": location,
        "payment_method": method,
        "merchant_category": category,
    }


def label_rows(records, fraud_type, scenario_id):
    return [{"transaction_id": r["transaction_id"],
             "fraud_type": fraud_type,
             "scenario_id": scenario_id} for r in records]


def daily_volume(run_date):
    base = 650 if run_date.weekday() < 5 else 850          # weekends are busier
    if any(start <= run_date <= end for start, end in FESTIVE_WINDOWS):
        base = int(base * 1.8)
    return int(base * random.uniform(0.8, 1.2))


# ---- normal behaviour ----
def normal_transaction(run_date, users, user_weights, merchants):
    # ~8% of everyday payments are ordinary person-to-person transfers, so that
    # "P2P" on its own is never a giveaway for fraud
    if random.random() < 0.08:
        payer = random.choices(users, weights=user_weights)[0]
        payee = random.choice([u for u in users if u != payer])
        amount = min(max(random.lognormvariate(math.log(1000), 0.9), 10), 30000)
        return make_record(payer, payee, amount, random_timestamp(run_date),
                           random.choice(LOCATIONS), "upi", "p2p_transfer")

    category = weighted_choice(CATEGORY_WEIGHTS)
    # log-normal: most payments are small, a few are large (like real spending)
    amount = random.lognormvariate(math.log(CATEGORY_MEDIAN_INR[category]), 0.8)
    amount = min(max(amount, 10), 60000)
    return make_record(
        user_id=random.choices(users, weights=user_weights)[0],
        payee_id=random.choice(merchants),
        amount=amount,
        ts=random_timestamp(run_date),
        location=random.choice(LOCATIONS),
        method=weighted_choice(PAYMENT_WEIGHTS),
        category=category,
    )


# ---- planted fraud scenarios (each returns records + their labels) ----
def scenario_mule_chain(run_date, users):
    """Money hops victim -> mule1 -> mule2 -> mule3 within minutes, late at night."""
    sid = f"mule_{uuid.uuid4().hex[:6]}"
    path = [random.choice(users)] + [f"user_{random.randint(5000, 9999)}" for _ in range(3)]
    amount = random.uniform(20000, 90000)
    ts = midnight(run_date) + timedelta(hours=random.randint(0, 4), minutes=random.randint(0, 59))
    city = random.choice(LOCATIONS)
    records = []
    for sender, receiver in zip(path, path[1:]):
        records.append(make_record(sender, receiver, amount, ts, city, "upi", "p2p_transfer"))
        amount *= random.uniform(0.90, 0.98)               # each hop skims a little
        ts += timedelta(minutes=random.randint(1, 6))
    return records, label_rows(records, "mule_chain", sid)


def scenario_probe_drain(run_date, users, merchants):
    """A compromised account makes many tiny test payments, then one big drain."""
    sid = f"probe_{uuid.uuid4().hex[:6]}"
    victim = random.choice(users)
    city = random.choice(LOCATIONS)
    ts = random_timestamp(run_date)
    records = []
    for _ in range(random.randint(6, 12)):
        records.append(make_record(victim, random.choice(merchants), random.uniform(1, 10),
                                   ts, city, "upi", weighted_choice(CATEGORY_WEIGHTS)))
        ts += timedelta(seconds=random.randint(10, 45))
    records.append(make_record(victim, random.choice(merchants), random.uniform(15000, 60000),
                               ts, city, "upi", "electronics"))
    return records, label_rows(records, "probe_then_drain", sid)


def scenario_collect_scam(run_date, users):
    """One scammer account receives mid-size UPI payments from several victims in ~1 hour."""
    sid = f"collect_{uuid.uuid4().hex[:6]}"
    scammer = f"user_{random.randint(5000, 9999)}"
    victims = random.sample(users, random.randint(3, 6))
    start = random_timestamp(run_date)
    records = [
        make_record(v, scammer, random.uniform(2000, 25000),
                    start + timedelta(minutes=random.randint(0, 90)),
                    random.choice(LOCATIONS), "upi", "p2p_transfer")
        for v in victims
    ]
    return records, label_rows(records, "collect_request_scam", sid)


# ---- data-quality defects (same idea as before, but the rate varies by day) ----
def apply_defects(records, scale):
    for r in records:
        roll = random.random()
        if roll < 0.02 * scale:
            r["amount"] = None
        elif roll < 0.04 * scale:
            r["location"] = None
        elif roll < 0.05 * scale:
            r["payment_method"] = "UNKNOWN_METHOD"


def add_duplicates(records, scale):
    k = min(int(len(records) * 0.01 * scale), len(records))
    records.extend(random.sample(records, k))


# ---- batch ----
def generate_batch(run_date):
    users = [f"user_{i:04d}" for i in range(1, 301)]
    user_weights = [min(random.paretovariate(2.5), 6) for _ in users]   # a few very active users
    merchants = [f"mrc_{i:03d}" for i in range(1, 81)]

    records, labels = [], []

    for _ in range(daily_volume(run_date)):
        rec = normal_transaction(run_date, users, user_weights, merchants)
        if random.random() < 0.008 and rec["merchant_category"] != "p2p_transfer":  # rare very large card/net-banking payment
            rec["amount"] = round(random.uniform(50000, 200000), 2)
            rec["payment_method"] = random.choice(["credit_card", "debit_card", "net_banking"])
            labels.extend(label_rows([rec], "large_value", f"large_{uuid.uuid4().hex[:6]}"))
        records.append(rec)

    for _ in range(random.randint(1, 3)):
        recs, labs = scenario_mule_chain(run_date, users)
        records.extend(recs); labels.extend(labs)
    for _ in range(random.randint(1, 3)):
        recs, labs = scenario_probe_drain(run_date, users, merchants)
        records.extend(recs); labels.extend(labs)
    for _ in range(random.randint(1, 2)):
        recs, labs = scenario_collect_scam(run_date, users)
        records.extend(recs); labels.extend(labs)

    # most days are normal, ~10% are "bad data days" with 3x the defects
    scale = random.choices([0.5, 1.0, 3.0], weights=[20, 70, 10])[0]
    apply_defects(records, scale)
    add_duplicates(records, scale)
    random.shuffle(records)
    return records, labels, scale


# ---- output ----
def s3_key(prefix, run_date, filename):
    return f"{prefix}/year={run_date.year}/month={run_date.month:02d}/day={run_date.day:02d}/{filename}"


def to_json_lines(rows):
    return "\n".join(json.dumps(r) for r in rows)


def upload_to_s3(records, labels, run_date, bucket_name=BUCKET_NAME):
    s3 = boto3.client("s3")
    for key, rows in [(s3_key("bronze", run_date, "transactions.json"), records),
                      (s3_key("labels", run_date, "labels.json"), labels)]:
        s3.put_object(Bucket=bucket_name, Key=key, Body=to_json_lines(rows),
                      ContentType="application/json")
        print(f"Uploaded {len(rows)} rows to s3://{bucket_name}/{key}")


def save_locally(records, labels):
    os.makedirs("tmp_pipeline", exist_ok=True)
    for name, rows in [("dry_run_transactions.json", records), ("dry_run_labels.json", labels)]:
        with open(os.path.join("tmp_pipeline", name), "w") as f:
            f.write(to_json_lines(rows))
        print(f"Saved {len(rows)} rows to tmp_pipeline/{name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", type=str, default=None, help="Date in YYYY-MM-DD format")
    parser.add_argument("--dry-run", action="store_true", help="Write local files, skip S3")
    parser.add_argument("--seed", type=int, default=None, help="Make the run reproducible")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
    run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()

    batch, labels, scale = generate_batch(run_date)
    print(f"Run date {run_date}: {len(batch)} rows, defect scale x{scale}")
    print("Planted fraud rows by type:", dict(Counter(l["fraud_type"] for l in labels)))

    if args.dry_run:
        save_locally(batch, labels)
    else:
        upload_to_s3(batch, labels, run_date)