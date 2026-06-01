"""중앙 로깅 설정.

setup_logging() 을 엔트리포인트에서 1회 호출하여 전체 로깅 구성.
환경변수로 동작을 제어하므로 코드 수정 없이 운영/개발 전환 가능.

환경변수
--------
LOG_LEVEL          : 루트 wellbot 로거 레벨. 기본 INFO.
LOG_FORMAT         : "console" | "json". 미설정 시 LOG_ENV 로 자동 결정.
LOG_ENV            : "dev" | "prod". 기본 dev (LOG_FORMAT 미설정 시 console).
LOG_TO_FILE        : "true"|"false". 기본 prod=true, dev=false.
LOG_DIR            : 로그 파일 디렉토리. 기본 <project>/logs (paths.LOG_DIR).
LOG_FILE_MAX_MB    : 회전 파일 1개 최대 크기(MB). 기본 50.
LOG_FILE_BACKUPS   : 보관할 회전 파일 수. 기본 5.
LOG_COLOR          : "true"|"false". console 포맷 컬러 출력. 기본 dev=true.

설계 원칙
--------
- root 가 아닌 "wellbot" 네임스페이스 로거에 핸들러 부착.
  → uvicorn/sqlalchemy/boto3 의 자체 로깅과 충돌 방지.
- 모든 LogRecord 에 log_context (emp_no/conversation_id/request_id) 를
  ContextFilter 로 주입.
- 외부 라이브러리 소음(boto3/botocore/pdfminer/sqlalchemy.engine)은
  WARNING 으로 일괄 하향.
- 잡히지 않은(uncaught) 예외는 sys.excepthook / asyncio 핸들러로 포착해
  wellbot.uncaught 로거에 기록 (전역 안전망).
- 재호출은 무시(idempotent).
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.config
import os
import sys
from pathlib import Path

from wellbot.logger import context as log_context
from wellbot.paths import LOG_DIR as DEFAULT_LOG_DIR

# 모든 wellbot.* 로거가 이 아래로 모임
ROOT_LOGGER = "wellbot"

# 기본 WARNING 으로 하향할 외부 라이브러리 목록
_NOISY_LIBRARIES = (
    "boto3",
    "botocore",
    "urllib3",
    "pdfminer",
    "sqlalchemy.engine",
    "asyncio",
)

# LogRecord 에 항상 존재하도록 보장할 상관관계 필드
_CONTEXT_FIELDS = ("emp_no", "conversation_id", "message_id", "request_id")

_configured = False


# ── Filter: log_context 값 주입 ──────────────────────────────────────


class ContextFilter(logging.Filter):
    """모든 레코드에 log_context 상관관계 필드 주입"""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = log_context.current()
        for field in _CONTEXT_FIELDS:
            setattr(record, field, ctx.get(field, "-"))
        return True


# ── Formatter: 사람이 읽는 컬러 콘솔 ─────────────────────────────────


class ConsoleFormatter(logging.Formatter):
    """개발용 사람이 읽기 좋은 포맷. 선택적으로 레벨에 컬러 적용"""

    _COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[41m",  # red bg
    }
    _RESET = "\033[0m"

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(
            fmt=(
                "%(asctime)s %(levelname)-7s %(name)s "
                "[emp=%(emp_no)s conv=%(conversation_id)s msg=%(message_id)s req=%(request_id)s] "
                "%(message)s"
            ),
            datefmt="%H:%M:%S",
        )
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self.use_color:
            color = self._COLORS.get(record.levelname, "")
            if color:
                return f"{color}{msg}{self._RESET}"
        return msg


# ── Formatter: 구조화 JSON (운영) ────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """운영용 1줄 JSON 포맷. CloudWatch/Loki/ELK 수집기 적재에 적합"""

    # LogRecord 표준 속성 (extra 추출 시 제외)
    _RESERVED = frozenset(
        logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "emp_no": getattr(record, "emp_no", "-"),
            "conversation_id": getattr(record, "conversation_id", "-"),
            "message_id": getattr(record, "message_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # log.info("msg", extra={...}) 로 전달된 임의 필드 병합
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and key not in payload:
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


# ── 환경변수 헬퍼 ───────────────────────────────────────────────────


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_options() -> dict[str, object]:
    """환경변수에서 설정 옵션 해석"""
    env = os.environ.get("LOG_ENV", "dev").strip().lower()
    is_prod = env == "prod"

    fmt = os.environ.get("LOG_FORMAT", "").strip().lower()
    if fmt not in ("console", "json"):
        fmt = "json" if is_prod else "console"

    log_dir = Path(os.environ.get("LOG_DIR") or DEFAULT_LOG_DIR)

    return {
        "level": os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        "format": fmt,
        "to_file": _env_bool("LOG_TO_FILE", default=is_prod),
        "log_dir": log_dir,
        "max_mb": int(os.environ.get("LOG_FILE_MAX_MB", "50")),
        "backups": int(os.environ.get("LOG_FILE_BACKUPS", "5")),
        "use_color": _env_bool("LOG_COLOR", default=not is_prod),
    }


def setup_logging(*, force: bool = False) -> None:
    """전체 로깅 구성. 엔트리포인트에서 1회 호출.

    재호출은 무시. force=True 시 강제 재구성.
    """
    global _configured
    if _configured and not force:
        return

    opts = _resolve_options()

    handlers: dict[str, dict] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "filters": ["context"],
            "formatter": opts["format"],
        }
    }
    handler_names = ["console"]

    if opts["to_file"]:
        log_dir: Path = opts["log_dir"]  # type: ignore[assignment]
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_dir / "wellbot.log"),
            "maxBytes": int(opts["max_mb"]) * 1024 * 1024,
            "backupCount": int(opts["backups"]),
            "encoding": "utf-8",
            "filters": ["context"],
            # 파일은 분석 용이성을 위해 항상 JSON
            "formatter": "json",
        }
        handler_names.append("file")

    config: dict[str, object] = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "context": {"()": "wellbot.logger.config.ContextFilter"},
        },
        "formatters": {
            "console": {
                "()": "wellbot.logger.config.ConsoleFormatter",
                "use_color": opts["use_color"],
            },
            "json": {
                "()": "wellbot.logger.config.JsonFormatter",
            },
        },
        "handlers": handlers,
        "loggers": {
            ROOT_LOGGER: {
                "level": opts["level"],
                "handlers": handler_names,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(config)

    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    # 전역 안전망 설치 (uncaught 예외 포착)
    _install_global_handlers()

    _configured = True

    log = logging.getLogger(f"{ROOT_LOGGER}.logger.config")
    log.info(
        "logging initialized: format=%s level=%s to_file=%s",
        opts["format"],
        opts["level"],
        opts["to_file"],
    )


# ── 전역 uncaught 예외 안전망 ────────────────────────────────────────

# 재호출·중복 설치 방지용 플래그
_hooks_installed = False


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
    """sys.excepthook: 동기 uncaught 예외 기록"""
    # Ctrl+C 는 정상 종료로 취급 — 노이즈 방지
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logging.getLogger(f"{ROOT_LOGGER}.uncaught").critical(
        "uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
    )


def _log_asyncio_exception(loop, context: dict) -> None:
    """asyncio 이벤트 루프의 uncaught 예외 기록"""
    exc = context.get("exception")
    msg = context.get("message", "asyncio error")
    log = logging.getLogger(f"{ROOT_LOGGER}.uncaught")
    if exc is not None:
        log.error("unhandled asyncio exception: %s", msg, exc_info=exc)
    else:
        log.error("unhandled asyncio error: %s", msg)


def _install_global_handlers() -> None:
    """동기 전역 예외 후크(sys.excepthook) 설치 (1회).

    asyncio 핸들러는 실행 중인 루프가 있어야 설치되므로 여기서 한 번 시도.
    setup_logging() 이 루프 밖(앱 import 시점)에서 호출되는 일반적 경우를 위해
    install_asyncio_handler() 를 별도 노출 — 루프 안(서버 startup)에서 호출.
    """
    global _hooks_installed
    if _hooks_installed:
        return

    sys.excepthook = _log_uncaught
    install_asyncio_handler()  # 루프가 있으면 설치, 없으면 no-op

    _hooks_installed = True


def install_asyncio_handler() -> bool:
    """실행 중인 이벤트 루프에 asyncio 예외 핸들러 설치.

    루프가 없으면 아무것도 하지 않고 False 반환.
    서버 startup 훅(루프 안)에서 호출하면 비동기 uncaught 예외도 포착.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    loop.set_exception_handler(lambda lp, ctx: _log_asyncio_exception(lp, ctx))
    return True
