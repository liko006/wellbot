"""report_maker 전용 설정 로더.

모듈 폴더의 report_maker.yaml 을 읽어 ReportMakerConfig 로 제공한다.
report_checker 와 동일하게 앱의 services.core.settings 와 독립적으로 동작한다.

환경별 리소스 식별자(model_id / memory_id / region)는 환경변수로 오버라이드한다:
    REPORT_MAKER_MODEL_ID, REPORT_MAKER_MEMORY_ID, AWS_REGION
S3 버킷/prefix 는 앱 storage_service(S3_KEY_PREFIX)를 재사용하므로 여기서 다루지 않는다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# 설정파일은 모듈과 함께 배치 (자기완결).
REPORT_MAKER_YAML = Path(__file__).resolve().parent / "report_maker.yaml"

# chtb_msg_d / agnt_mmry_use_n 의 AGNT_ID 태그 기본값(yaml agent_id 미설정 시 폴백).
# 실제 태깅·조회에는 get_config().agent_id (yaml agent_id) 를 사용한다.
# 매칭되는 agnt_m 행이 없어도 fallback 동작한다. 표시명은 관리자 화면에서 등록한다.
AGNT_ID = "RPT_DRFT_GEN"

# 문서 파싱 지원 확장자 분류 (스타일 학습 입력)
IMAGE_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
DOC_EXTS: frozenset[str] = frozenset({".pptx", ".pdf"})


@dataclass(frozen=True)
class ReportMakerConfig:
    """보고서 문구 작성 지원 설정."""

    agent_id: str = AGNT_ID
    model_id: str = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    region: str = ""
    read_timeout_sec: int = 300

    max_tokens_outline: int = 50000
    max_tokens_analysis: int = 30000
    max_tokens_style: int = 10000
    max_tokens_vision: int = 2000

    memory_id: str = ""

    max_templates: int = 5

    max_upload_mb: int = 50
    allowed_extensions: tuple[str, ...] = (
        ".pdf", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    )

    max_retries: int = 2
    retry_base_delay_sec: float = 1.0


def _load() -> ReportMakerConfig:
    raw: dict = {}
    if REPORT_MAKER_YAML.exists():
        raw = yaml.safe_load(REPORT_MAKER_YAML.read_text(encoding="utf-8")) or {}
    else:
        log.warning("report_maker.yaml 없음 → 기본값 사용 path=%s", REPORT_MAKER_YAML)

    exts = raw.get("allowed_extensions") or list(ReportMakerConfig.allowed_extensions)

    # 환경변수 오버라이드(환경별 리소스 식별자). 없으면 yaml, 그것도 없으면 기본값.
    model_id = (
        os.environ.get("REPORT_MAKER_MODEL_ID")
        or raw.get("model_id")
        or ReportMakerConfig.model_id
    )
    memory_id = os.environ.get("REPORT_MAKER_MEMORY_ID") or raw.get("memory_id", "") or ""
    region = os.environ.get("AWS_REGION") or raw.get("region", "") or ""

    return ReportMakerConfig(
        agent_id=raw.get("agent_id", ReportMakerConfig.agent_id),
        model_id=model_id,
        region=region,
        read_timeout_sec=int(raw.get("read_timeout_sec", ReportMakerConfig.read_timeout_sec)),
        max_tokens_outline=int(raw.get("max_tokens_outline", ReportMakerConfig.max_tokens_outline)),
        max_tokens_analysis=int(raw.get("max_tokens_analysis", ReportMakerConfig.max_tokens_analysis)),
        max_tokens_style=int(raw.get("max_tokens_style", ReportMakerConfig.max_tokens_style)),
        max_tokens_vision=int(raw.get("max_tokens_vision", ReportMakerConfig.max_tokens_vision)),
        memory_id=memory_id,
        max_templates=int(raw.get("max_templates", ReportMakerConfig.max_templates)),
        max_upload_mb=int(raw.get("max_upload_mb", ReportMakerConfig.max_upload_mb)),
        allowed_extensions=tuple(str(e).lower() for e in exts),
        max_retries=int(raw.get("max_retries", ReportMakerConfig.max_retries)),
        retry_base_delay_sec=float(
            raw.get("retry_base_delay_sec", ReportMakerConfig.retry_base_delay_sec)
        ),
    )


@lru_cache(maxsize=1)
def get_config() -> ReportMakerConfig:
    """설정 싱글턴. 변경 반영이 필요하면 reload_config() 호출."""
    return _load()


def reload_config() -> ReportMakerConfig:
    """캐시를 비우고 파일을 다시 읽는다(테스트/핫리로드용)."""
    get_config.cache_clear()
    return get_config()
