import json

import azure.functions as func
from app.main import app as fastapi_app
from app.azure_queue import outbox_queue
from app.config import settings

DOCUMENTS = settings().storage_backend != "sql"

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)


@app.timer_trigger(schedule="0 * * * * *", arg_name="timer", use_monitor=True)
def dispatch_outbox(timer: func.TimerRequest):
    if DOCUMENTS:
        from app.document_worker import dispatch, runtime_context

        with runtime_context() as runtime, outbox_queue() as client:
            dispatch(
                runtime.store,
                lambda body, delay: client.send_message(
                    body, visibility_timeout=delay, time_to_live=7 * 86400
                ),
            )
        return
    from sqlalchemy import select
    from app.db import Job, SessionLocal, now

    with outbox_queue() as client, SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(Job.state.in_(["pending", "running"]), Job.due_at <= now()).limit(100)
        ).all()
        for job in jobs:
            client.send_message(json.dumps({"job_id": job.id}))


@app.queue_trigger(arg_name="message", queue_name="anke-sports-jobs", connection="AzureQueueConnection")
def process_job(message: func.QueueMessage):
    import re

    if DOCUMENTS:
        from app.document_worker import run_job, runtime_context
        from app.document_store import StoreError

        try:
            payload = json.loads(message.get_body())
            with runtime_context() as runtime:
                run_job(runtime, payload)
        except Exception as exc:
            code = exc.code if isinstance(exc, StoreError) else "QUEUE_JOB_FAILED"
            raise RuntimeError(code) from None
        return
    from app.worker import run_one
    from app.jobs import error_code

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
    if DOCUMENTS:
        from app.document_worker import runtime_context

        with runtime_context() as runtime:
            runtime.providers.schedule()
        return
    from app.worker import schedule_providers

    schedule_providers()


if not DOCUMENTS:

    @app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", use_monitor=True)
    def update_content(timer: func.TimerRequest):
        from app.worker import run_maintenance

        if not run_maintenance():
            raise RuntimeError("CONTENT_MAINTENANCE_INCOMPLETE")
else:

    @app.timer_trigger(schedule="0 0 0 * * *", arg_name="timer", use_monitor=True)
    def advance_calendar_window(timer: func.TimerRequest):
        from app.document_worker import runtime_context, schedule_calendar_window

        with runtime_context() as runtime:
            schedule_calendar_window(runtime)
