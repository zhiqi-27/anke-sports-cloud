import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db import Base, User
from app.service import ensure_user


def test_existing_mysql_tables_migrate_without_merging_identities(mysql_engine):
    if mysql_engine is None:
        pytest.skip("Requires explicitly disposable MySQL")
    url = mysql_engine.url.render_as_string(hide_password=False)
    env = {**os.environ, "ANKE_SPORTS_DATABASE_URL": url}

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr

    alembic("upgrade", "e42c08f771d3")
    sessions = sessionmaker(mysql_engine, expire_on_commit=False)
    with sessions() as db:
        first = ensure_user(db, "CaseSensitiveUID")
        before = first.config.copy()
        db.commit()
    alembic("upgrade", "head")
    alembic("check")
    with sessions() as db:
        ensure_user(db, "casesensitiveuid")
        db.commit()
    for target in ["e42c08f771d3", "head"]:
        alembic("downgrade" if target != "head" else "upgrade", target)
        with sessions() as db:
            assert db.get(User, "CaseSensitiveUID").config == before
            assert db.get(User, "casesensitiveuid").id == "casesensitiveuid"
            collations = db.execute(
                text(
                    "SELECT TABLE_NAME,TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME!='alembic_version'"
                )
            ).all()
            assert len(collations) == len(Base.metadata.tables)
            assert all(value == "utf8mb4_0900_bin" for _, value in collations)
    alembic("check")
