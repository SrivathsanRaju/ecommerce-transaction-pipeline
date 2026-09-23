import boto3
import json
import random
import uuid
from datetime import datetime, timedelta, date

BUCKET_NAME = "sriv-de"
today = date.today()

# We write chaos-test data to a SEPARATE bronze path so it doesn't
# contaminate today's real run — this is its own controlled experiment
CHAOS_BRONZE_KEY = f"bronze/year={today.year}/month={today.month:02d}/day={today.day:02d}/chaos_test_transactions.json"

MERCHANT_CATEGORIES = ["electronics", "groceries", "fashion", "travel", "food_delivery", "utilities"]
PAYMENT_METHODS = ["credit_card", "debit_card", "upi", "net_banking", "wallet"]
LOCATIONS = ["Chennai", "Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune"]


def generate_clean_record(user_pool):
    """A normal, valid transaction — the baseline."""
    return {
        "transaction_id": str(uuid.uuid4()),
        "user_id": random.choice(user_pool),
        "amount": round(random.uniform(50, 5000), 2),
        "currency": "INR",
        "timestamp": (datetime.now() - timedelta(minutes=random.randint(0, 1440))).isoformat(),
        "location": random.choice(LOCATIONS),
        "payment_method": random.choice(PAYMENT_METHODS),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
    }


def build_chaos_batch():
    """
    Deliberately construct a batch with KNOWN, COUNTED problems,
    so we can verify the pipeline catches exactly what we planted.
    """
    user_pool = [f"user_{i:04d}" for i in range(1, 101)]

    clean_records = [generate_clean_record(user_pool) for _ in range(300)]

    # --- Known duplicates: exact copies of existing records ---
    duplicate_records = random.sample(clean_records, k=25)

    # --- Known nulls: missing amount ---
    null_amount_records = []
    for _ in range(15):
        r = generate_clean_record(user_pool)
        r["amount"] = None
        null_amount_records.append(r)

    # --- Known nulls: missing location ---
    null_location_records = []
    for _ in range(10):
        r = generate_clean_record(user_pool)
        r["location"] = None
        null_location_records.append(r)

    # --- Known bad enum values ---
    bad_payment_records = []
    for _ in range(8):
        r = generate_clean_record(user_pool)
        r["payment_method"] = "UNKNOWN_METHOD"
        bad_payment_records.append(r)

    # --- Known malformed records: missing transaction_id entirely ---
    # (a real-world scenario: an upstream system drops a required field)
    malformed_records = []
    for _ in range(5):
        r = generate_clean_record(user_pool)
        del r["transaction_id"]
        malformed_records.append(r)

    all_records = (
        clean_records + duplicate_records + null_amount_records +
        null_location_records + bad_payment_records + malformed_records
    )
    random.shuffle(all_records)

    injected_counts = {
        "clean_baseline": len(clean_records),
        "duplicates_injected": len(duplicate_records),
        "null_amount_injected": len(null_amount_records),
        "null_location_injected": len(null_location_records),
        "bad_payment_method_injected": len(bad_payment_records),
        "malformed_missing_id_injected": len(malformed_records),
        "total_records": len(all_records),
    }

    return all_records, injected_counts


if __name__ == "__main__":
    batch, counts = build_chaos_batch()

    print("=== CHAOS TEST: Injected Data Summary ===")
    print(json.dumps(counts, indent=2))

    s3 = boto3.client("s3")
    body = "\n".join(json.dumps(r) for r in batch)
    s3.put_object(Bucket=BUCKET_NAME, Key=CHAOS_BRONZE_KEY, Body=body, ContentType="application/json")

    print(f"\nUploaded chaos batch to s3://{BUCKET_NAME}/{CHAOS_BRONZE_KEY}")
    print("\nNext: run the transform script against THIS file and compare")
    print("the health-table output against the counts above.")