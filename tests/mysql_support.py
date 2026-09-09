"""Opt-in disposable loopback MySQL databases. Never accepts a cloud/server database URL."""

from contextlib import contextmanager
import os
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.db import engine_options


@contextmanager
def disposable_mysql():
    value = os.getenv("ANKE_TEST_MYSQL_URL")
    if not value:
        yield None
        return
    url = make_url(value)
    if (
        os.getenv("ANKE_TEST_MYSQL_DISPOSABLE") != "1"
        or url.drivername != "mysql+pymysql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database
    ):
        raise RuntimeError("Use an explicitly disposable loopback MySQL server URL without a database")
    name = "anke_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, pool_pre_ping=True)
    created = False
    engine = None
    try:
        with admin.begin() as db:
            db.exec_driver_sql(f"CREATE DATABASE `{name}`")
            created = True
        engine = create_engine(url.set(database=name), **engine_options(url))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.begin() as db:
                db.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()
