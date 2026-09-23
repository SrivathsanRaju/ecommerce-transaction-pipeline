from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta

default_args = {
    "owner": "srivathsan",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

with DAG(
    dag_id="ecommerce_transaction_pipeline",
    description="Daily batch pipeline: ingest -> transform -> fraud-signal flagging",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 9, 1),
    catchup=False,          # don't backfill past dates — only run for today forward
    tags=["capstone", "data-engineering"],
) as dag:

    ingest = BashOperator(
        task_id="ingest_transactions",
        bash_command="python /opt/airflow/dags/generate_transactions.py",
    )

    transform = BashOperator(
        task_id="transform_bronze_to_silver",
        bash_command="python /opt/airflow/dags/transform_transactions.py",
    )

    fraud_flagging = BashOperator(
        task_id="fraud_signal_flagging",
        bash_command="python /opt/airflow/dags/fraud_signal_flagging.py",
    )

    # This defines the actual pipeline order/dependency chain
    ingest >> transform >> fraud_flagging