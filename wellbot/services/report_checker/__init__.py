"""보고서 오류 검출 서비스 (자기완결 모듈).

PDF 보고서의 오탈자와 수치/기술 일관성 오류를 AWS Bedrock(Claude)으로 검출한다.
앱의 settings/bedrock 인프라와 독립적으로 전용 config(모듈 내 report_checker.yaml)와
전용 Bedrock 래퍼를 사용한다.
"""

from wellbot.services.report_checker.config import CheckerConfig, get_config
from wellbot.services.report_checker.models import (
    AnalysisResult,
    AttentionIssue,
    ConsistencyError,
    Fact,
    ProgressEvent,
    TypoError,
    UserDictionary,
)
from wellbot.services.report_checker.pdf_extract import (
    extract_pages,
    extract_pages_from_bytes,
)
from wellbot.services.report_checker.pipeline import run_analysis
from wellbot.services.report_checker.report_html import generate_html

__all__ = [
    "CheckerConfig",
    "get_config",
    "AnalysisResult",
    "AttentionIssue",
    "ConsistencyError",
    "Fact",
    "ProgressEvent",
    "TypoError",
    "UserDictionary",
    "extract_pages",
    "extract_pages_from_bytes",
    "run_analysis",
    "generate_html",
]
