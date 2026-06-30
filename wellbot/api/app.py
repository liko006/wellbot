"""Wellbot FastAPI 앱 (Reflex api_transformer 에 마운트).

Reflex State 로 처리할 수 없거나 비효율적인 케이스 담당.
- 파일 업로드 (대용량 스트리밍, multipart/form-data)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from wellbot.api.upload import router as upload_router
from wellbot.api.download import router as download_router
from wellbot.api.kb_upload import router as kb_upload_router
from wellbot.api.kb_download import router as kb_download_router
from wellbot.api.client_log import router as client_log_router
from wellbot.logger import install_asyncio_handler, log_context


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """서버 이벤트 루프에 asyncio uncaught 예외 핸들러 설치.

    setup_logging() 은 앱 import 시점(루프 밖)에 실행되므로
    asyncio 핸들러는 루프가 살아있는 startup 에서 설치 필요.
    """
    install_asyncio_handler()
    yield


api_app = FastAPI(title="WellBot API", docs_url=None, redoc_url=None, lifespan=_lifespan)


@api_app.middleware("http")
async def _bind_log_context(request: Request, call_next):
    """요청마다 request_id 바인딩 → 로그 상관관계 추적.

    emp_no 는 인증을 거친 엔드포인트에서 log_context.bind() 로 보강.
    """
    with log_context.scope(request_id=log_context.new_request_id()):
        return await call_next(request)


api_app.include_router(upload_router)
api_app.include_router(download_router)
api_app.include_router(kb_upload_router)
api_app.include_router(kb_download_router)
api_app.include_router(client_log_router)
