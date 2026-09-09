from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import broadcasts
from app.broadcast_schemas import (
    BroadcastAction,
    BroadcastDecision,
    BroadcastDraft,
    BroadcastEdit,
    BroadcastList,
    BroadcastView,
    DeviceEvidence,
)
from app.config import settings
from app.db import BroadcastRecord, get_db
from app.platforms import registry
from app.security import actor, problem

router = APIRouter()


def maintainer(request: Request, db=Depends(get_db)):
    identity = actor(request, db)
    if identity not in settings().maintainer_ids:
        problem("MAINTAINER_REQUIRED", "仅维护者可以管理公共直播入口", 403)
    return identity


def finish(db, operation):
    try:
        record = operation()
        db.commit()
        return broadcasts.record_view(db, record)
    except IntegrityError:
        db.rollback()
        problem("BROADCAST_CONFLICT", "记录发生冲突，请重新读取后重试", 409)


@router.get("/api/v1/platforms")
def platforms():
    return {"items": registry()}


@router.get("/api/v1/maintenance/broadcasts", response_model=BroadcastList)
def list_records(
    event_id: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    identity=Depends(maintainer),
    db=Depends(get_db),
):
    from app.db import Link

    query = (
        select(BroadcastRecord)
        .order_by(BroadcastRecord.updated_at.desc(), BroadcastRecord.link_id)
        .limit(limit + 1)
    )
    if event_id:
        query = query.join(Link, Link.id == BroadcastRecord.link_id).where(Link.event_id == event_id)
    rows = db.scalars(query).all()
    return {"items": [broadcasts.record_view(db, row) for row in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/api/v1/maintenance/broadcasts/{ident}", response_model=BroadcastView)
def get_record(ident: str, identity=Depends(maintainer), db=Depends(get_db)):
    return broadcasts.record_view(db, broadcasts.record_for(db, ident))


@router.post("/api/v1/maintenance/broadcasts", response_model=BroadcastView)
def create_record(data: BroadcastDraft, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(db, lambda: broadcasts.create_record(db, identity, data.model_dump()))


@router.put("/api/v1/maintenance/broadcasts/{ident}", response_model=BroadcastView)
def edit_record(ident: str, data: BroadcastEdit, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(
        db,
        lambda: broadcasts.edit_record(
            db, identity, ident, data.model_dump(exclude={"expected_revision"}), data.expected_revision
        ),
    )


@router.post("/api/v1/maintenance/broadcasts/{ident}/publish", response_model=BroadcastView)
def publish(ident: str, data: BroadcastDecision, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(db, lambda: broadcasts.approve_record(db, identity, ident, data))


@router.post("/api/v1/maintenance/broadcasts/{ident}/suspend", response_model=BroadcastView)
def suspend(ident: str, data: BroadcastAction, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(
        db, lambda: broadcasts.suspend_record(db, identity, ident, data.expected_revision, data.reason)
    )


@router.post("/api/v1/maintenance/broadcasts/{ident}/check", response_model=BroadcastView)
def check(ident: str, data: BroadcastAction, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(db, lambda: broadcasts.queue_check(db, identity, ident, data.expected_revision))


@router.post("/api/v1/maintenance/broadcasts/{ident}/device-evidence", response_model=BroadcastView)
def device_evidence(ident: str, data: DeviceEvidence, identity=Depends(maintainer), db=Depends(get_db)):
    return finish(db, lambda: broadcasts.add_device_evidence(db, identity, ident, data))
