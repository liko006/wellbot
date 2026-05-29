"""Database engine and session management.

엔진/세션팩토리는 `get_session()` 첫 호출 시점에 생성한다.
모듈 import 시점에 `DB_URL` 을 강제 검증하면, 단위 테스트나 CLI 스크립트에서
DB 가 필요 없는 코드 경로조차 import 만으로 실패하기 때문에 lazy 초기화로 전환한다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_engine() -> sessionmaker[Session]:
    """엔진과 세션팩토리를 최초 1회 생성한다."""
    global _engine, _session_factory
    if _session_factory is not None:
        return _session_factory

    db_url = os.environ.get("DB_URL")
    if not db_url:
        raise RuntimeError(
            "DB_URL 환경변수가 설정되지 않았습니다. "
            "엔트리포인트에서 wellbot.env.init_env() 가 호출되었는지 확인하세요."
        )
    _engine = create_engine(db_url, echo=False, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine)
    return _session_factory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    factory = _ensure_engine()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
