FROM apache/airflow:3.0.2

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jdk-headless && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

USER airflow
RUN pip install --no-cache-dir \
    pyspark==3.5.1 \
    pandas \
    pyarrow \
    boto3