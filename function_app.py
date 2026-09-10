import json

import azure.functions as func
from sqlalchemy import select

from app.db import Job, SessionLocal, now
from app.main import app as fastapi_app
from app.worker import run_maintenance, run_one, schedule_providers
from app.jobs import error_code
from app.azure_queue import outbox_queue

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)


@app.timer_trigger(schedule="0 * * * * *", arg_name="timer", use_monitor=True)
def dispatch_outbox(timer: func.TimerRequest):
    with outbox_queue() as client, SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(Job.state.in_(["pending", "running"]), Job.due_at <= now()).limit(100)
        ).all()
        for job in jobs:
            client.send_message(json.dumps({"job_id": job.id}))


@app.queue_trigger(arg_name="message", queue_name="anke-sports-jobs", connection="AzureQueueConnection")
def process_job(message: func.QueueMessage):
    import re

    try:
        payload = json.loads(message.get_body())
        job_id = payload["job_id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("QUEUE_MESSAGE_INVALID") from None
    try:
        run_one(job_id)
    except Exception as exc:
        # Storage outages should be retried by Azure without logging SQL/payload details.
        raise RuntimeError("JOB_DISPATCH_FAILED_" + error_code(exc)) from None


@app.timer_trigger(schedule="0 * * * * *", arg_name="timer", use_monitor=True)
def update_schedules(timer: func.TimerRequest):
    schedule_providers()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", use_monitor=True)
def update_content(timer: func.TimerRequest):
    if not run_maintenance():
        raise RuntimeError("CONTENT_MAINTENANCE_INCOMPLETE")
