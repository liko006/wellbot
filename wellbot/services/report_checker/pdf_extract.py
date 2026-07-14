"""PDF 페이지 단위 텍스트 추출.

원본은 PyMuPDF(fitz)를 썼으나, 이 프로젝트에 이미 있는 pdfplumber 를 사용해
신규 의존성 없이 {page_no: text} 형태를 만든다. (page_no 는 1부터 시작)
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import BinaryIO

log = logging.getLogger(__name__)


def _extract(source) -> dict[int, str]:
    import logging as _logging

    import pdfplumber

    # pdfminer 의 과다한 경고 억제
    _logging.getLogger("pdfminer").setLevel(_logging.ERROR)

    pages: dict[int, str] = {}
    with pdfplumber.open(source) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages[i] = text
    log.info("report_checker PDF 로드 완료 pages=%d", len(pages))
    return pages


def extract_pages(pdf_path: str | Path) -> dict[int, str]:
    """PDF 파일 경로에서 페이지별 텍스트 dict 추출. 빈 페이지는 제외."""
    return _extract(str(pdf_path))


def extract_pages_from_bytes(data: bytes | BinaryIO) -> dict[int, str]:
    """PDF 바이트/스트림에서 페이지별 텍스트 dict 추출 (S3 다운로드 흐름용)."""
    source = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    return _extract(source)
