"""report_checker 결과물 저장/조회.

S3 의존을 이 파일 하나로 격리한다. 앱의 storage_service(범용 S3 유틸)를
재사용하되, report_checker 잡별 prefix 규칙과 다운로드 URL 발급만 노출한다.

잡 prefix: {S3_KEY_PREFIX}/report_checker/{emp_no}/{job_id}/
  - source.pdf        업로드 원본
  - report.html       분석 결과 HTML (다운로드 대상)
"""

from __future__ import annotations

import logging
import os

from wellbot.services.files import storage_service

log = logging.getLogger(__name__)

_SOURCE_NAME = "source.pdf"
_RESULT_NAME = "report.html"


def _base_prefix() -> str:
    root = os.environ.get("S3_KEY_PREFIX", "files").strip("/")
    return f"{root}/report_checker" if root else "report_checker"


def job_prefix(emp_no: str, job_id: str) -> str:
    return f"{_base_prefix()}/{emp_no}/{job_id}/"


def source_key(emp_no: str, job_id: str) -> str:
    return f"{job_prefix(emp_no, job_id)}{_SOURCE_NAME}"


def result_key(emp_no: str, job_id: str) -> str:
    return f"{job_prefix(emp_no, job_id)}{_RESULT_NAME}"


def save_source(emp_no: str, job_id: str, data: bytes) -> str:
    """업로드 PDF 원본을 S3 에 저장하고 key 반환."""
    key = source_key(emp_no, job_id)
    storage_service.upload_bytes(data, key, content_type="application/pdf")
    return key


def download_source(emp_no: str, job_id: str) -> bytes:
    """저장된 PDF 원본을 바이트로 반환."""
    return storage_service.download_bytes(source_key(emp_no, job_id))


def save_result_html(emp_no: str, job_id: str, html: str) -> str:
    """분석 결과 HTML 을 S3 에 저장하고 key 반환."""
    key = result_key(emp_no, job_id)
    storage_service.upload_bytes(
        html.encode("utf-8"), key, content_type="text/html; charset=utf-8"
    )
    return key


def result_exists(emp_no: str, job_id: str) -> bool:
    """결과 HTML 이 S3 에 존재하는지."""
    return storage_service.object_exists(result_key(emp_no, job_id))


def iter_result(emp_no: str, job_id: str):
    """결과 HTML 을 청크 스트리밍 (다운로드 프록시용)."""
    return storage_service.iter_download_stream(result_key(emp_no, job_id))


def cleanup_job(emp_no: str, job_id: str) -> int:
    """잡 prefix 하위 오브젝트 전체 삭제. 삭제 개수 반환."""
    return storage_service.delete_prefix(job_prefix(emp_no, job_id))
