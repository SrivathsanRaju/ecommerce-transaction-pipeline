import json
import random
import uuid
import boto3
from datetime import datetime, timedelta, date

# ---- CONFIG ----
BUCKET_NAME = "sriv-de"
NUM_RECORDS = 500

# Realistic value pools
MERCHANT_CATEGORIES = ["electronics", "groceries", "fashion", "travel", "food_delivery", "utilities"]
PAYMENT_METHODS = ["credit_card", "debit_card", "upi", "net_banking", "wallet"]
LOCATIONS = ["Chennai", "Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune"]

def generate_transaction(user_pool):
    """Generate one realistic transaction record."""
    user_id = random.choice(user_pool)
    amount = round(random.uniform(50, 5000), 2)

    # Inject occasional outlier amounts (for fraud-signal layer to catch later)
    if random.random() < 0.02:  # 2% of transactions are outliers
        amount = round(random.uniform(50000, 200000), 2)

    timestamp = datetime.now() - timedelta(minutes=random.randint(0, 1440))

    record = {
        "transaction_id": str(uuid.uuid4()),
        "user_id": user_id,
        "amount": amount,
        "currency": "INR",
        "timestamp": timestamp.isoformat(),
        "location": random.choice(LOCATIONS),
        "payment_method": random.choice(PAYMENT_METHODS),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
    }

    # Inject occasional realistic messiness (~5% of records)
    roll = random.random()
    if roll < 0.02:
        record["amount"] = None  # missing amount
    elif roll < 0.04:
        record["location"] = None  # missing location
    elif roll < 0.05:
        record["payment_method"] = "UNKNOWN_METHOD"  # bad enum value

    return record


def generate_batch(num_records=NUM_RECORDS):
    user_pool = [f"user_{i:04d}" for i in range(1, 101)]  # 100 synthetic users
    records = [generate_transaction(user_pool) for _ in range(num_records)]

    # Inject a small number of exact duplicates (realistic pipeline scenario)
    duplicates = random.sample(records, k=int(num_records * 0.01))
    records.extend(duplicates)

    random.shuffle(records)
    return records


def upload_to_s3(records, bucket_name=BUCKET_NAME):
    today = date.today()
    key = f"bronze/year={today.year}/month={today.month:02d}/day={today.day:02d}/transactions.json"

    s3 = boto3.client("s3")
    body = "\n".join(json.dumps(r) for r in records)  # newline-delimited JSON — standard for data lakes

    s3.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=body,
        ContentType="application/json"
    )

    print(f"Uploaded {len(records)} records to s3://{bucket_name}/{key}")
    return key


if __name__ == "__main__":
    batch = generate_batch()
    upload_to_s3(batch)