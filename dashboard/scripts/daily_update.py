"""
Daily automation entry point — runs on the HOST (not inside a container).

Airflow already owns the pipeline itself (ingest -> transform ->
fraud_signal_flagging -> load_gold_to_postgres), running in Docker on a
@daily schedule. This script does the two things Airflow's container
can't: export dashboard/data.json (Postgres is reachable from the host,
not necessarily meaningfully different, but .git and the dashboard/ folder
are only on the host filesystem) and git commit/push it.

It waits for a marker file that the DAG's last task writes into
airflow/dags/ — that folder IS bind-mounted into the container
(see docker-compose.yml), so a file the container creates shows up here
on the host immediately. This avoids guessing how long the pipeline takes
or re-running it a second time from the host.

Requires the DAG to have a final task like:

    mark_done = BashOperator(
        task_id="mark_pipeline_complete",
        bash_command="touch /opt/airflow/dags/.pipeline_done_{{ ds_nodash }}",
    )
    ingest >> transform >> fraud_flagging >> load_to_postgres >> mark_done

Scheduled via cron / Task Scheduler shortly after the DAG's start time
(see README_SCHEDULING.md) — it polls for the marker rather than assuming
an exact finish time.
"""
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent          # .../dashboard
REPO_ROOT = DASHBOARD_DIR.parent                                 # .../de-transaction-pipeline
DAGS_DIR = REPO_ROOT / "airflow" / "dags"
DATA_JSON = DASHBOARD_DIR / "data.json"
LOG_FILE = DASHBOARD_DIR / "scripts" / "daily_update.log"
EXPORT_CMD = ["python3", str(DASHBOARD_DIR / "export_data.py")]

POLL_SECONDS = 60
TIMEOUT_MINUTES = 45  # how long to wait for today's DAG run before giving up


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def already_exported_today() -> bool:
    if not DATA_JSON.exists():
        return False
    try:
        return json.loads(DATA_JSON.read_text())["generated_date"] == date.today().isoformat()
    except Exception:
        return False


def marker_path_for_today() -> Path:
    return DAGS_DIR / f".pipeline_done_{date.today().strftime('%Y%m%d')}"


def wait_for_dag_completion() -> bool:
    marker = marker_path_for_today()
    deadline = time.time() + TIMEOUT_MINUTES * 60
    log(f"Waiting for {marker.name} (created by the DAG's final task)...")
    while time.time() < deadline:
        if marker.exists():
            log("✓ Marker found — today's pipeline run completed.")
            return True
        time.sleep(POLL_SECONDS)
    log(f"✗ Timed out after {TIMEOUT_MINUTES} min waiting for the DAG. "
        f"Check the Airflow UI (http://localhost:8080) for a stuck/failed run.")
    return False


def run(cmd, step_name):
    log(f"→ {step_name}: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=DASHBOARD_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"✗ {step_name} FAILED (exit {result.returncode})\n{result.stderr[-2000:]}")
        return False
    log(f"✓ {step_name} done")
    return True


def git(*args):
    result = subprocess.run(["git", *args], cwd=DASHBOARD_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"✗ git {' '.join(args)} FAILED\n{result.stderr}")
    return result


def main():
    force = "--force" in sys.argv

    if not force and already_exported_today():
        log("data.json already reflects today — nothing to do (pass --force to override).")
        return

    if not force and not wait_for_dag_completion():
        log("Aborting: not touching data.json since the pipeline run isn't confirmed done.")
        sys.exit(1)

    if not run(EXPORT_CMD, "export_data.py"):
        log("Aborting: export step failed.")
        sys.exit(1)

    git("add", "data.json")
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=DASHBOARD_DIR)
    if diff.returncode == 0:
        log("data.json unchanged — nothing to commit.")
        return

    git("commit", "-m", f"Daily data update — {date.today().isoformat()}")
    push = git("push")
    if push.returncode == 0:
        log("✓ Pushed. Vercel/GitHub Pages will redeploy automatically.")
    else:
        log("✗ Push failed — check git credentials / network, then run again.")


if __name__ == "__main__":
    main()
