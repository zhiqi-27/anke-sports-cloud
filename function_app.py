import json
import os

import azure.functions as func
from azure.storage.queue import QueueClient
from sqlalchemy import select

from app.db import Job, SessionLocal, now
from app.main import app as fastapi_app
from app.worker import run_one, schedule_providers
from app.websub import schedule_content

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)


@app.timer_trigger(schedule="0 * * * * *", arg_name="timer", use_monitor=True)
def dispatch_outbox(timer: func.TimerRequest):
    client = QueueClient.from_connection_string(os.environ["AzureQueueConnection"], "anke-sports-jobs")
    with SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(Job.state.in_(["pending", "running"]), Job.due_at <= now()).limit(100)
        ).all()
        for job in jobs:
            client.send_message(json.dumps({"job_id": job.id}))


@app.queue_trigger(arg_name="message", queue_name="anke-sports-jobs", connection="AzureQueueConnection")
def process_job(message: func.QueueMessage):
    payload = json.loads(message.get_body())
    run_one(payload["job_id"])


@app.timer_trigger(schedule="0 0 */6 * * *", arg_name="timer", use_monitor=True)
def update_schedules(timer: func.TimerRequest):
    schedule_providers()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", use_monitor=True)
def update_content(timer: func.TimerRequest):
    from app.oauth import clean_expired_connections

    schedule_content()
    clean_expired_connections()
