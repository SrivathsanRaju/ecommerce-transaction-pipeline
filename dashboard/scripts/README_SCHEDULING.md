# Scheduling the daily update

Your pipeline is already orchestrated by Airflow (`airflow/dags/transaction_pipeline_dag.py`,
`schedule="@daily"`) running in Docker. That DAG does NOT need a duplicate
cron job — it's already the scheduler for ingest → transform → fraud
flagging → load_to_postgres.

What Airflow's container *can't* do is export `dashboard/data.json` or
`git push` it, because docker-compose.yml only bind-mounts `dags/`, `logs/`,
`config/`, `plugins/` into the container — not the repo root or `.git`.
So two small pieces run on the host instead:

1. `daily_update.py` — waits for the DAG's completion marker, then exports
   `data.json` and commits/pushes it.
2. Your OS's own scheduler (cron / Task Scheduler) — fires it once a day,
   a little after the DAG's start time.

## First: fix the DAG's timezone

`schedule="@daily"` with a naive `start_date=datetime(2026, 9, 1)` runs at
**00:00 UTC = 5:30 AM IST**, not midnight IST. In
`airflow/dags/transaction_pipeline_dag.py`, change the DAG's timezone, e.g.:

```python
import pendulum

with DAG(
    dag_id="ecommerce_transaction_pipeline",
    ...
    schedule="@daily",
    start_date=pendulum.datetime(2026, 9, 1, tz="Asia/Kolkata"),
    catchup=False,
    ...
) as dag:
```

## Second: add a completion marker as the DAG's last task

The host script needs a reliable signal that today's run actually finished
— rather than the host guessing how long the pipeline takes. Since
`airflow/dags/` is bind-mounted, a task can drop a file there and it shows
up on your Windows filesystem immediately:

```python
mark_done = BashOperator(
    task_id="mark_pipeline_complete",
    bash_command="touch /opt/airflow/dags/.pipeline_done_{{ ds_nodash }}",
)

ingest >> transform >> fraud_flagging >> load_to_postgres >> mark_done
```

Add `.pipeline_done_*` to your root `.gitignore` so these marker files don't
get committed.

After editing the DAG file, either wait for the `airflow-dag-processor`
container to pick up the change (it polls periodically) or restart it:
`docker compose restart airflow-dag-processor`.

## The "machine asleep at midnight" problem

If your laptop is closed or asleep at 00:00, the scheduled run just won't
fire — cron/Task Scheduler don't queue missed runs by default. Two fixes,
both set up below:
- **Catch-up on login/startup**: a second trigger that runs the same script
  when you next log in. Since `daily_update.py` checks `data.json`'s date
  and skips if already current, this is a no-op on a day that already ran,
  and a same-morning catch-up on a day that was missed.
- **(Optional) wake the machine**: Windows Task Scheduler can wake a sleeping
  PC to run a task; macOS can do it with `pmset`. Covered below.

---

## macOS / Linux — cron

Open your crontab:

```bash
crontab -e
```

Add (adjust the path to your repo):

```cron
# Fires a couple minutes after the DAG starts; the script itself polls
# up to 45 min for the completion marker, so the exact offset isn't critical.
2 0 * * * /usr/bin/python3 /path/to/your/repo/dashboard/scripts/daily_update.py >> /path/to/your/repo/dashboard/scripts/cron.log 2>&1

# Catch-up: also try once when you log in, in case midnight was missed
@reboot /usr/bin/python3 /path/to/your/repo/dashboard/scripts/daily_update.py >> /path/to/your/repo/dashboard/scripts/cron.log 2>&1
```

cron uses your system clock's local timezone, so `2 0 * * *` is ~00:02 IST
as long as your machine's timezone is set to IST (check with `timedatectl` on
Linux, or System Settings → Date & Time on Mac). Also make sure Docker
Desktop / your Airflow containers are actually set to start on login if the
machine reboots overnight — a scheduled script can't wait for a marker that
never gets created because Airflow itself isn't running.

**macOS specifics:**
- Terminal (or `cron`'s parent process) needs Full Disk Access under
  System Settings → Privacy & Security, or cron jobs silently fail to read
  your repo.
- To also wake the Mac for this: `sudo pmset repeat wakeorpoweron MTWRFSU 23:58:00`
  wakes it 1–2 min before midnight so it's not asleep when cron fires.

## Windows — Task Scheduler

1. Open **Task Scheduler** → **Create Task** (not "Basic Task" — you need the extra options).
2. **General** tab: name it `Daily Pipeline Update`. Check "Run whether user is logged on or not." Check "Wake the computer to run this task."
3. **Triggers** tab → New:
   - Trigger 1: **Daily**, start time **12:02:00 AM** (a couple minutes after the DAG starts — the script polls for up to 45 min for the completion marker, so this doesn't need to be exact).
   - Trigger 2: **At log on** (this is the catch-up trigger).
4. **Actions** tab → New:
   - Program/script: `python`
   - Arguments: `C:\path\to\your\repo\dashboard\scripts\daily_update.py`
   - Start in: `C:\path\to\your\repo\dashboard`
5. **Conditions** tab: uncheck "Start the task only if the computer is on AC power" if this is a laptop, otherwise it won't run on battery.
6. Save. Test it immediately with `python daily_update.py --force` from a
   terminal first to confirm the pipeline + git push actually work end to
   end before trusting the schedule.

---

## Git push without a password prompt

The scheduled run can't type a GitHub password/2FA code, so make sure
`git push` already works non-interactively in that repo — either:
- an SSH remote with a key that has no passphrase (or is loaded in an agent
  that persists), or
- an HTTPS remote with a **credential helper** caching a PAT
  (`git config credential.helper store` after one manual push+prompt, or
  `manager` on Windows, `osxkeychain` on Mac).

Test with `git push` in a fresh terminal (no cached agent) to make sure it
doesn't hang waiting for input.

---

## End-to-end loop, once this is set up

```
00:00 IST → cron/Task Scheduler fires daily_update.py
          → runs your pipeline (ingest → bronze → silver → gold → Postgres)
          → export_data.py reads Postgres → writes dashboard/data.json
          → git commit + push
          → Vercel/GitHub Pages sees the push → redeploys automatically
          → dashboard shows today's data, no manual step
```

If you later move Postgres to a free managed host (Neon, Supabase, Railway)
instead of local, this whole thing can move into a GitHub Actions scheduled
workflow instead — no machine has to be awake at midnight. Worth doing if
you want the pipeline to keep running even when your laptop is off; happy to
set that up when you get there.
