"""Bedrock Converse 이미지 포맷 판별 + 크기/용량 정규화 헬퍼."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from wellbot.constants import IMAGE_MAX_DIMENSION, IMAGE_MAX_SIZE_MB

log = logging.getLogger(__name__)

# Bedrock Converse 지원 이미지 포맷
_BEDROCK_IMAGE_FORMATS = {"png", "jpeg", "gif", "webp"}

# fmt → PIL save format
_PIL_SAVE_FORMAT = {"png": "PNG", "jpeg": "JPEG", "gif": "GIF", "webp": "WEBP"}

# 용량 한도에 안전마진 10% 적용 (base64/오버헤드 대비)
_MAX_BYTES = int(IMAGE_MAX_SIZE_MB * 1024 * 1024 * 0.9)
_MIN_DIMENSION = 256  # 이 이하로는 더 줄이지 않음
_MAX_SHRINK_STEPS = 8


def image_format(filename: str) -> str | None:
    """파일명 확장자로 Bedrock Converse image format 판별. 미지원 확장자이면 None"""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    if ext in _BEDROCK_IMAGE_FORMATS:
        return ext
    return None


def _encode(im, save_format: str) -> bytes:
    buf = io.BytesIO()
    if save_format == "JPEG":
        im.save(buf, format="JPEG", quality=85, optimize=True)
    elif save_format == "WEBP":
        im.save(buf, format="WEBP", quality=85, method=4)
    elif save_format == "PNG":
        im.save(buf, format="PNG", optimize=True)
    else:  # GIF (애니메이션은 첫 프레임만)
        im.save(buf, format="GIF")
    return buf.getvalue()


def fit_image_for_bedrock(data: bytes, fmt: str) -> tuple[bytes, str] | None:
    """이미지를 Bedrock Converse 제약에 맞게 정규화.

    - 최대 변이 IMAGE_MAX_DIMENSION(px) 초과 → 종횡비 유지 다운스케일
    - 용량이 IMAGE_MAX_SIZE_MB(MB) 초과 → 재인코딩·점진 축소로 한도 이하로

    제약을 이미 만족하면 원본 bytes 를 그대로 반환(불필요한 재인코딩 방지).

    Returns:
        (bytes, fmt): 전송 가능한 이미지
        None: 이미지를 열 수 없거나 축소 후에도 한도 초과 → 전송 제외 권고
    """
    within_bytes = len(data) <= _MAX_BYTES

    try:
        from PIL import Image
    except Exception:
        # Pillow 부재 시 best-effort: 원본 그대로 (한도 초과분은 상위에서 실패 로깅)
        log.warning("Pillow 미설치 - 이미지 정규화 생략")
        return data, fmt

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        log.warning("이미지 열기 실패 → 전송 제외 (fmt=%s, %d bytes)", fmt, len(data), exc_info=True)
        return None

    w, h = im.size
    if within_bytes and max(w, h) <= IMAGE_MAX_DIMENSION:
        return data, fmt  # 제약 만족 - 손대지 않음

    save_format = _PIL_SAVE_FORMAT.get(fmt, "PNG")
    if save_format == "JPEG" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    # 1) 최대 변 기준 다운스케일
    if max(w, h) > IMAGE_MAX_DIMENSION:
        scale = IMAGE_MAX_DIMENSION / max(w, h)
        im = im.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )

    # 2) 용량 한도까지 점진 축소
    out = _encode(im, save_format)
    steps = 0
    while len(out) > _MAX_BYTES and steps < _MAX_SHRINK_STEPS:
        cw, ch = im.size
        if max(cw, ch) <= _MIN_DIMENSION:
            break
        im = im.resize(
            (max(1, int(cw * 0.8)), max(1, int(ch * 0.8))),
            Image.Resampling.LANCZOS,
        )
        out = _encode(im, save_format)
        steps += 1

    if len(out) > _MAX_BYTES:
        log.warning(
            "이미지 축소 후에도 %d bytes > 한도 %d bytes → 전송 제외",
            len(out), _MAX_BYTES,
        )
        return None

    log.info(
        "이미지 정규화: %dx%d %d bytes → %dx%d %d bytes (fmt=%s)",
        w, h, len(data), im.size[0], im.size[1], len(out), fmt,
    )
    return out, fmt
