"""
Core transformation and fraud-signal logic, extracted so it can be
unit tested independently of S3/Spark/file I/O.
"""
import pandas as pd


def clean_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies the pipeline's data quality rules:
    - drop records missing transaction_id entirely
    - deduplicate on transaction_id
    - drop records with missing amount or location
    - standardize invalid payment_method values to 'other'
    """
    if "transaction_id" in df.columns:
        df = df[df["transaction_id"].notna()]
        df = df.drop_duplicates(subset=["transaction_id"])

    df = df[df["amount"].notna()]
    df = df[df["location"].notna()]

    df["payment_method"] = df["payment_method"].apply(
        lambda x: "other" if x == "UNKNOWN_METHOD" else x
    )

    return df.reset_index(drop=True)


def flag_amount_outliers(df: pd.DataFrame, z_threshold: float = 3.0) -> pd.DataFrame:
    """Flags transactions whose amount is more than z_threshold standard deviations from the mean."""
    mean_amount = df["amount"].mean()
    std_amount = df["amount"].std()

    if std_amount == 0 or pd.isna(std_amount):
        df["flag_amount_outlier"] = False
        return df

    df["amount_zscore"] = (df["amount"] - mean_amount) / std_amount
    df["flag_amount_outlier"] = df["amount_zscore"].abs() > z_threshold
    return df


def flag_high_velocity(df: pd.DataFrame, percentile: float = 0.99) -> pd.DataFrame:
    """Flags transactions from users in the top percentile of transaction frequency."""
    user_txn_counts = df.groupby("user_id")["transaction_id"].transform("count")
    df["user_transaction_count"] = user_txn_counts
    threshold = df["user_transaction_count"].quantile(percentile)
    df["flag_high_velocity"] = df["user_transaction_count"] > threshold
    return df


def build_health_record(raw_count: int, clean_count: int, null_amount: int,
                          null_location: int, bad_payment: int, duplicate: int) -> dict:
    """Builds the pipeline health summary dict used for observability logging."""
    return {
        "raw_row_count": raw_count,
        "clean_row_count": clean_count,
        "rows_dropped": raw_count - clean_count,
        "null_amount_count": null_amount,
        "null_location_count": null_location,
        "bad_payment_method_count": bad_payment,
        "duplicate_count": duplicate,
        "null_rate_pct": round((null_amount + null_location) / raw_count * 100, 2) if raw_count else 0,
        "duplicate_rate_pct": round(duplicate / raw_count * 100, 2) if raw_count else 0,
    }