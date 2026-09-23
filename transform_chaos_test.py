from pyspark.sql import SparkSession
from pyspark.sql import functions as F
import boto3
import json
import os
from datetime import date, datetime

BUCKET_NAME = "sriv-de"
today = date.today()

s3_chaos_bronze_key = f"bronze/year={today.year}/month={today.month:02d}/day={today.day:02d}/chaos_test_transactions.json"
s3_chaos_silver_prefix = f"silver/chaos_test/year={today.year}/month={today.month:02d}/day={today.day:02d}/"
s3_chaos_health_prefix = f"gold/pipeline_health/chaos_test/year={today.year}/month={today.month:02d}/day={today.day:02d}/"

LOCAL_TMP = "tmp_pipeline"
LOCAL_CHAOS_BRONZE = f"{LOCAL_TMP}/chaos_bronze_transactions.json"
LOCAL_CHAOS_SILVER_DIR = f"{LOCAL_TMP}/chaos_silver"
LOCAL_CHAOS_HEALTH_FILE = f"{LOCAL_TMP}/chaos_health.json"

os.makedirs(LOCAL_TMP, exist_ok=True)
s3 = boto3.client("s3")

# ---- Step 1: Download the chaos bronze file ----
s3.download_file(BUCKET_NAME, s3_chaos_bronze_key, LOCAL_CHAOS_BRONZE)
print(f"Downloaded chaos file to {LOCAL_CHAOS_BRONZE}")

# ---- Step 2: Read with Spark ----
spark = SparkSession.builder.appName("ChaosTest").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

raw_df = spark.read.json(LOCAL_CHAOS_BRONZE)
raw_count = raw_df.count()
print(f"Read {raw_count} raw records from chaos batch")

# ---- Step 3: Quality checks BEFORE cleaning ----
null_txn_id_count = raw_df.filter(F.col("transaction_id").isNull()).count()
null_amount_count = raw_df.filter(F.col("amount").isNull()).count()
null_location_count = raw_df.filter(F.col("location").isNull()).count()
bad_payment_count = raw_df.filter(F.col("payment_method") == "UNKNOWN_METHOD").count()

# Duplicate detection — only meaningful where transaction_id actually exists
non_null_id_df = raw_df.filter(F.col("transaction_id").isNotNull())
dup_count = non_null_id_df.count() - non_null_id_df.dropDuplicates(["transaction_id"]).count()

# ---- Step 4: Clean — this now handles the missing-ID case explicitly ----
clean_df = raw_df.filter(F.col("transaction_id").isNotNull())     # NEW: drop records with no ID at all
clean_df = clean_df.dropDuplicates(["transaction_id"])
clean_df = clean_df.filter(F.col("amount").isNotNull())
clean_df = clean_df.filter(F.col("location").isNotNull())
clean_df = clean_df.withColumn(
    "payment_method",
    F.when(F.col("payment_method") == "UNKNOWN_METHOD", "other").otherwise(F.col("payment_method"))
)

clean_count = clean_df.count()
print(f"Clean records after validation: {clean_count}")

# ---- Step 5: Write clean chaos-test output locally, then to S3 ----
os.makedirs(LOCAL_CHAOS_SILVER_DIR, exist_ok=True)
clean_pdf = clean_df.toPandas()
silver_file = os.path.join(LOCAL_CHAOS_SILVER_DIR, "transactions.parquet")
clean_pdf.to_parquet(silver_file, engine="pyarrow", index=False)

s3_key = s3_chaos_silver_prefix + "transactions.parquet"
s3.upload_file(silver_file, BUCKET_NAME, s3_key)
print(f"Uploaded chaos-test clean output to s3://{BUCKET_NAME}/{s3_key}")

# ---- Step 6: Health record, with the injected counts alongside for direct comparison ----
health_record = {
    "run_timestamp": datetime.now().isoformat(),
    "stage": "chaos_test_bronze_to_silver",
    "raw_row_count": raw_count,
    "clean_row_count": clean_count,
    "rows_dropped": raw_count - clean_count,
    "null_transaction_id_count": null_txn_id_count,
    "null_amount_count": null_amount_count,
    "null_location_count": null_location_count,
    "bad_payment_method_count": bad_payment_count,
    "duplicate_count": dup_count,
}

print("\nChaos test health summary:")
print(json.dumps(health_record, indent=2))

with open(LOCAL_CHAOS_HEALTH_FILE, "w") as f:
    json.dump(health_record, f, indent=2)

health_s3_key = s3_chaos_health_prefix + "health.json"
s3.upload_file(LOCAL_CHAOS_HEALTH_FILE, BUCKET_NAME, health_s3_key)
print(f"Uploaded chaos health record to s3://{BUCKET_NAME}/{health_s3_key}")

spark.stop()
print("\nDone.")