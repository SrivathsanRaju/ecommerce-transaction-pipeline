import pandas as pd
import pytest
from pipeline_logic import (
    clean_transactions,
    flag_amount_outliers,
    flag_high_velocity,
    build_health_record,
)


# ---------- Fixtures: small, hand-built test data ----------

@pytest.fixture
def sample_transactions():
    return pd.DataFrame([
        {"transaction_id": "t1", "user_id": "u1", "amount": 100.0, "location": "Chennai", "payment_method": "upi"},
        {"transaction_id": "t2", "user_id": "u2", "amount": 200.0, "location": "Mumbai", "payment_method": "credit_card"},
        {"transaction_id": "t1", "user_id": "u1", "amount": 100.0, "location": "Chennai", "payment_method": "upi"},  # exact duplicate of t1
        {"transaction_id": "t3", "user_id": "u3", "amount": None, "location": "Delhi", "payment_method": "upi"},  # null amount
        {"transaction_id": "t4", "user_id": "u4", "amount": 150.0, "location": None, "payment_method": "wallet"},  # null location
        {"transaction_id": "t5", "user_id": "u5", "amount": 300.0, "location": "Pune", "payment_method": "UNKNOWN_METHOD"},  # bad enum
        {"transaction_id": None, "user_id": "u6", "amount": 400.0, "location": "Chennai", "payment_method": "upi"},  # missing ID
    ])


# ---------- clean_transactions tests ----------

def test_removes_exact_duplicates(sample_transactions):
    result = clean_transactions(sample_transactions)
    assert result["transaction_id"].value_counts()["t1"] == 1


def test_drops_null_amount(sample_transactions):
    result = clean_transactions(sample_transactions)
    assert "t3" not in result["transaction_id"].values


def test_drops_null_location(sample_transactions):
    result = clean_transactions(sample_transactions)
    assert "t4" not in result["transaction_id"].values


def test_drops_missing_transaction_id(sample_transactions):
    result = clean_transactions(sample_transactions)
    assert result["transaction_id"].isna().sum() == 0


def test_standardizes_bad_payment_method(sample_transactions):
    result = clean_transactions(sample_transactions)
    t5_row = result[result["transaction_id"] == "t5"]
    assert t5_row["payment_method"].iloc[0] == "other"


def test_clean_output_has_expected_row_count(sample_transactions):
    # 7 raw rows -> minus 1 duplicate, 1 null amount, 1 null location, 1 missing ID = 3 clean rows
    result = clean_transactions(sample_transactions)
    assert len(result) == 3


# ---------- flag_amount_outliers tests ----------

def test_flags_extreme_outlier():
    # 20 tightly clustered normal values, plus one extreme outlier —
    # a large enough sample that the outlier doesn't itself distort
    # the mean/std used to judge it (which was the bug in the original test)
    normal_values = [100 + i for i in range(20)]  # 100–119
    df = pd.DataFrame({"amount": normal_values + [50000]})
    result = flag_amount_outliers(df)
    assert result["flag_amount_outlier"].iloc[-1] == True
    assert result["flag_amount_outlier"].iloc[:-1].sum() == 0


def test_no_outliers_in_uniform_data():
    df = pd.DataFrame({"amount": [100, 101, 99, 100, 102]})
    result = flag_amount_outliers(df)
    assert result["flag_amount_outlier"].sum() == 0


def test_handles_zero_variance_without_crashing():
    # all identical amounts -> std is 0, should not divide by zero
    df = pd.DataFrame({"amount": [100, 100, 100, 100]})
    result = flag_amount_outliers(df)
    assert result["flag_amount_outlier"].sum() == 0


# ---------- flag_high_velocity tests ----------

def test_flags_high_frequency_user():
    # 30 other users with 1 transaction each (30 rows) heavily outweigh
    # u1's 5 transactions in the per-row weighted distribution, so the
    # 80th percentile lands at 1, and u1 (count=5) clearly exceeds it.
    rows = [{"transaction_id": f"t{i}", "user_id": "u1"} for i in range(5)]
    rows += [{"transaction_id": f"t{i+5}", "user_id": f"u{i+2}"} for i in range(30)]
    df = pd.DataFrame(rows)
    result = flag_high_velocity(df, percentile=0.8)
    flagged_users = result[result["flag_high_velocity"]]["user_id"].unique()
    assert "u1" in flagged_users


# ---------- build_health_record tests ----------

def test_health_record_math_is_consistent():
    record = build_health_record(
        raw_count=100, clean_count=85, null_amount=5,
        null_location=5, bad_payment=3, duplicate=5
    )
    assert record["rows_dropped"] == 15
    assert record["null_rate_pct"] == 10.0
    assert record["duplicate_rate_pct"] == 5.0


def test_health_record_handles_zero_raw_count():
    # shouldn't crash on divide-by-zero if raw_count is 0
    record = build_health_record(0, 0, 0, 0, 0, 0)
    assert record["null_rate_pct"] == 0