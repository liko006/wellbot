"""report_checker 전용 설정 로더.

모듈 폴더의 report_checker.yaml 을 읽어 CheckerConfig 로 제공한다.
앱의 services.core.settings 와 독립적으로 동작한다(자기완결 모듈).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# 설정파일은 모듈과 함께 배치 — 앱의 config/ 디렉터리에 의존하지 않는다(자기완결).
REPORT_CHECKER_YAML = Path(__file__).resolve().parent / "report_checker.yaml"


@dataclass(frozen=True)
class CheckerConfig:
    """보고서 오류 검출 설정."""

    agent_id: str = "RPT_INTEG_CHK"
    model_id: str = "global.anthropic.claude-opus-4-8"
    region: str = ""
    max_tokens: int = 8192
    temperature: float | None = None

    typo_chunk_size: int = 10
    extract_chunk_size: int = 20
    validate_batch_size: int = 50

    max_retries: int = 3
    retry_delay_sec: float = 3.0
    call_interval_sec: float = 0.4

    max_upload_mb: int = 50
    allowed_extensions: tuple[str, ...] = (".pdf",)


def _load() -> CheckerConfig:
    if not REPORT_CHECKER_YAML.exists():
        log.warning(
            "report_checker.yaml 없음 → 기본값 사용 path=%s", REPORT_CHECKER_YAML
        )
        return CheckerConfig()

    raw = yaml.safe_load(REPORT_CHECKER_YAML.read_text(encoding="utf-8")) or {}
    exts = raw.get("allowed_extensions") or [".pdf"]
    return CheckerConfig(
        agent_id=raw.get("agent_id", CheckerConfig.agent_id),
        model_id=raw.get("model_id", CheckerConfig.model_id),
        region=raw.get("region", "") or "",
        max_tokens=int(raw.get("max_tokens", CheckerConfig.max_tokens)),
        temperature=raw.get("temperature", None),
        typo_chunk_size=int(raw.get("typo_chunk_size", CheckerConfig.typo_chunk_size)),
        extract_chunk_size=int(
            raw.get("extract_chunk_size", CheckerConfig.extract_chunk_size)
        ),
        validate_batch_size=int(
            raw.get("validate_batch_size", CheckerConfig.validate_batch_size)
        ),
        max_retries=int(raw.get("max_retries", CheckerConfig.max_retries)),
        retry_delay_sec=float(raw.get("retry_delay_sec", CheckerConfig.retry_delay_sec)),
        call_interval_sec=float(
            raw.get("call_interval_sec", CheckerConfig.call_interval_sec)
        ),
        max_upload_mb=int(raw.get("max_upload_mb", CheckerConfig.max_upload_mb)),
        allowed_extensions=tuple(str(e).lower() for e in exts),
    )


@lru_cache(maxsize=1)
def get_config() -> CheckerConfig:
    """설정 싱글턴. 변경 반영이 필요하면 reload_config() 호출."""
    return _load()


def reload_config() -> CheckerConfig:
    """캐시를 비우고 파일을 다시 읽는다(테스트/핫리로드용)."""
    get_config.cache_clear()
    return get_config()
