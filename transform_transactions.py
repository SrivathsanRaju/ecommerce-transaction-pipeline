from pyspark.sql import SparkSession
from pyspark.sql import functions as F
import boto3
import json
import os
import shutil
import argparse
from datetime import date, datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--run-date", type=str, default=None, help="Date in YYYY-MM-DD format")
args = parser.parse_args()

BUCKET_NAME = "sriv-de"
today = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()

s3_bronze_key = f"bronze/year={today.year}/month={today.month:02d}/day={today.day:02d}/transactions.json"
s3_silver_prefix = f"silver/year={today.year}/month={today.month:02d}/day={today.day:02d}/"
s3_health_prefix = f"gold/pipeline_health/year={today.year}/month={today.month:02d}/day={today.day:02d}/"

LOCAL_TMP = "tmp_pipeline"
LOCAL_BRONZE = f"{LOCAL_TMP}/bronze_transactions.json"
LOCAL_SILVER_DIR = f"{LOCAL_TMP}/silver"
LOCAL_HEALTH_FILE = f"{LOCAL_TMP}/health.json"

os.makedirs(LOCAL_TMP, exist_ok=True)
s3 = boto3.client("s3")

# ---- Step 1: Download bronze file locally ----
s3.download_file(BUCKET_NAME, s3_bronze_key, LOCAL_BRONZE)
print(f"Downloaded bronze file to {LOCAL_BRONZE}")

# ---- Step 2: Spark reads LOCAL file only ----
spark = SparkSession.builder.appName("TransactionTransform").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

raw_df = spark.read.json(LOCAL_BRONZE)
raw_count = raw_df.count()
print(f"Read {raw_count} raw records from bronze")

# ---- Step 3: Data quality checks BEFORE cleaning ----
null_amount_count = raw_df.filter(F.col("amount").isNull()).count()
null_location_count = raw_df.filter(F.col("location").isNull()).count()
bad_payment_count = raw_df.filter(F.col("payment_method") == "UNKNOWN_METHOD").count()
dup_count = raw_count - raw_df.dropDuplicates(["transaction_id"]).count()

# ---- Step 4: Clean ----
clean_df = raw_df.dropDuplicates(["transaction_id"])
clean_df = clean_df.filter(F.col("amount").isNotNull())
clean_df = clean_df.filter(F.col("location").isNotNull())
clean_df = clean_df.withColumn(
    "payment_method",
    F.when(F.col("payment_method") == "UNKNOWN_METHOD", "other").otherwise(F.col("payment_method"))
)

clean_count = clean_df.count()
print(f"Clean records after validation: {clean_count}")

# ---- Step 5: Write clean data locally as Parquet (via Pandas, avoids Windows Hadoop issue) ----
os.makedirs(LOCAL_SILVER_DIR, exist_ok=True)
clean_pdf = clean_df.toPandas()
silver_file = os.path.join(LOCAL_SILVER_DIR, "transactions.parquet")
clean_pdf.to_parquet(silver_file, engine="pyarrow", index=False)
print(f"Wrote clean Parquet locally to {silver_file}")

# ---- Step 6: Build health record ----
health_record = {
    "run_timestamp": datetime.now(timezone.utc).isoformat(),
    "stage": "bronze_to_silver",
    "raw_row_count": raw_count,
    "clean_row_count": clean_count,
    "rows_dropped": raw_count - clean_count,
    "null_amount_count": null_amount_count,
    "null_location_count": null_location_count,
    "bad_payment_method_count": bad_payment_count,
    "duplicate_count": dup_count,
    "null_rate_pct": round((null_amount_count + null_location_count) / raw_count * 100, 2) if raw_count else 0,
    "duplicate_rate_pct": round(dup_count / raw_count * 100, 2) if raw_count else 0,
}
print("Pipeline health summary:")
print(json.dumps(health_record, indent=2))

with open(LOCAL_HEALTH_FILE, "w") as f:
    json.dump(health_record, f, indent=2)

spark.stop()

# ---- Step 7: Upload everything back to S3 ----
s3_key = s3_silver_prefix + "transactions.parquet"
s3.upload_file(silver_file, BUCKET_NAME, s3_key)
print(f"Uploaded transactions.parquet to s3://{BUCKET_NAME}/{s3_key}")

health_s3_key = s3_health_prefix + "health.json"
s3.upload_file(LOCAL_HEALTH_FILE, BUCKET_NAME, health_s3_key)
print(f"Uploaded health record to s3://{BUCKET_NAME}/{health_s3_key}")

print("Done.")