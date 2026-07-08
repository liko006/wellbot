"""텍스트 청킹 서비스.

파싱된 문서 텍스트를 검색용 청크로 분할.

토큰 카운팅은 정확한 토크나이저 없이 문자 기반으로 추정한다.
공백 어절 기반 추정(단어 × 1.4)은 한국어/중국어/일본어를 크게 과소추정해
Bedrock Titan embedding 입력 한도(8192 토큰)를 넘겨 임베딩이 통째로 실패했다.
→ CJK 문자당 ~1토큰(상한적)으로 세고, 마지막에 하드 상한 가드로 재분할해
   어떤 청크도 EMBED_TOKEN_HARD_MAX 를 넘지 않도록 보장한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from wellbot.constants import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    CJK_TOKENS_PER_CHAR,
    EMBED_TOKEN_HARD_MAX,
    LATIN_CHARS_PER_TOKEN,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    """청킹 결과"""

    seq: int          # 청크 순번 (0부터)
    text: str         # 청크 텍스트
    token_count: int  # 추정 토큰 수


# 한글(자모/음절)·가나·한자·CJK 기호 등. 문자당 ~1토큰으로 세는 대상.
_CJK_RE = re.compile(
    "[ᄀ-ᇿ"   # Hangul Jamo
    "⺀-⿟"    # CJK Radicals, Kangxi
    "぀-ヿ"    # Hiragana, Katakana
    "㄰-㆏"    # Hangul Compatibility Jamo
    "㇀-㇯"    # CJK Strokes
    "㐀-䶿"    # CJK Ext A
    "一-鿿"    # CJK Unified
    "ꥠ-꥿"    # Hangul Jamo Ext-A
    "가-힯"    # Hangul Syllables
    "豈-﫿"    # CJK Compatibility Ideographs
    "ｦ-ￜ]"   # Halfwidth Kana/Hangul
)


def estimate_tokens(text: str) -> int:
    """CJK 인식 토큰 수 추정 (상한적).

    CJK 문자는 문자당 CJK_TOKENS_PER_CHAR, 그 외는 LATIN_CHARS_PER_TOKEN 문자당
    1토큰으로 세어 실제보다 크게(=안전하게) 추정한다. 공백 어절 수에 의존하지
    않으므로 띄어쓰기 없는 중국어/일본어도 올바르게 추정된다.
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    est = cjk * CJK_TOKENS_PER_CHAR + other / LATIN_CHARS_PER_TOKEN
    # 올림(ceil): 경계에서 과소추정 방지
    return max(1, int(est) + (1 if est > int(est) else 0))


def _split_paragraphs(text: str) -> list[str]:
    """빈 줄 기준 문단 분리. 문단 내부는 그대로 유지"""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        else:
            if current:
                paragraphs.append("\n".join(current))
                current = []
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _split_by_token_budget(text: str, size: int, overlap: int) -> list[str]:
    """텍스트를 토큰 예산(size) 이하 조각으로 문자 기준 분할.

    텍스트 자체의 토큰 밀도(토큰/문자)로 조각당 문자 수를 계산하므로
    CJK(밀도 ~1)와 라틴(밀도 ~0.25) 모두에서 각 조각이 대략 size 토큰 이하가 된다.
    라틴 텍스트는 가능하면 공백 경계에서 잘라 단어를 보존한다.
    """
    text = text.strip()
    if not text:
        return []
    total = estimate_tokens(text)
    if total <= size:
        return [text]

    n = len(text)
    density = total / n  # 토큰/문자 (>0)
    chars = max(1, int(size / density))
    ov_chars = max(0, int(overlap / density)) if overlap > 0 else 0
    if ov_chars >= chars:  # 진행 불가 방지
        ov_chars = chars // 4

    parts: list[str] = []
    start = 0
    while start < n:
        end = min(start + chars, n)
        if end < n:
            # 라틴: 조각 끝 부근의 공백에서 잘라 단어 보존 (CJK 는 공백이 없어 그대로 컷)
            sp = text.rfind(" ", start + int(chars * 0.6), end)
            if sp > start:
                end = sp
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= n:
            break
        start = max(end - ov_chars, start + 1)
    return parts


def chunk_text(
    text: str,
    size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[Chunk]:
    """텍스트를 토큰 기반 청크로 분할.

    전략:
        1. 문단 단위로 먼저 그룹핑 (가능한 한 의미 단위 보존)
        2. 문단을 쌓다가 size 초과하면 청크 종결
        3. 다음 청크는 직전 청크의 마지막 overlap 토큰만큼 겹치게 시작
        4. 단일 문단이 size 초과면 토큰 예산 기준 강제 분할
        5. 마지막에 하드 상한 가드로 초과 청크 재분할 (임베딩 오버플로 방지)

    Args:
        text: 분할할 원본 텍스트
        size: 청크당 최대 토큰 수
        overlap: 청크 간 겹침 토큰 수

    Returns:
        순서대로 정렬된 청크 목록
    """
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = _split_paragraphs(text) or [text]

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_tokens = 0
    seq = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens, seq
        if not buffer:
            return
        chunk_text_val = "\n\n".join(buffer).strip()
        if not chunk_text_val:
            buffer = []
            buffer_tokens = 0
            return
        chunks.append(
            Chunk(
                seq=seq,
                text=chunk_text_val,
                token_count=estimate_tokens(chunk_text_val),
            )
        )
        seq += 1
        if overlap > 0 and buffer:
            tail = buffer[-1]
            tail_tokens = estimate_tokens(tail)
            if tail_tokens <= overlap:
                # 문단 전체가 overlap 범위 안이면 다음 청크에 그대로 포함
                buffer = [tail]
                buffer_tokens = tail_tokens
                return
            # 문단 끝에서 overlap 토큰만큼만 (문자 기준 — CJK 안전)
            density = tail_tokens / max(1, len(tail))
            keep_chars = max(1, int(overlap / density))
            tail_text = tail[-keep_chars:].strip()
            buffer = [tail_text]
            buffer_tokens = estimate_tokens(tail_text)
        else:
            buffer = []
            buffer_tokens = 0

    for paragraph in paragraphs:
        p_tokens = estimate_tokens(paragraph)

        if p_tokens > size:
            # 단일 문단이 size 초과 → 의미 단위 보존 불가. 토큰 예산 기준 강제 분할
            flush()
            for sub in _split_by_token_budget(paragraph, size, overlap):
                chunks.append(
                    Chunk(
                        seq=seq,
                        text=sub,
                        token_count=estimate_tokens(sub),
                    )
                )
                seq += 1
            continue

        if buffer_tokens + p_tokens > size and buffer:
            flush()

        buffer.append(paragraph)
        buffer_tokens += p_tokens

    flush()
    return _enforce_hard_max(chunks, size, overlap)


def _enforce_hard_max(chunks: list[Chunk], size: int, overlap: int) -> list[Chunk]:
    """어떤 청크도 EMBED_TOKEN_HARD_MAX 를 넘지 않도록 보장.

    정상 경로/강제 분할이 이미 size 이하로 만들지만, 추정 오차·예외 입력에 대비한
    최종 안전망. 초과분은 토큰 예산 기준으로 재분할한다.
    """
    if all(c.token_count <= EMBED_TOKEN_HARD_MAX for c in chunks):
        return chunks

    out: list[Chunk] = []
    for c in chunks:
        if c.token_count <= EMBED_TOKEN_HARD_MAX:
            out.append(c)
            continue
        log.warning(
            "chunk 추정 토큰 %d > 하드상한 %d → 재분할 (seq=%d)",
            c.token_count, EMBED_TOKEN_HARD_MAX, c.seq,
        )
        budget = min(size, EMBED_TOKEN_HARD_MAX)
        for sub in _split_by_token_budget(c.text, budget, overlap):
            out.append(Chunk(seq=0, text=sub, token_count=estimate_tokens(sub)))
    # 순번 재부여
    return [Chunk(seq=i, text=c.text, token_count=c.token_count) for i, c in enumerate(out)]


def chunks_to_jsonl(chunks: Iterable[Chunk]) -> bytes:
    """청크 목록을 JSONL 바이트로 직렬화 (S3 저장용)"""
    import json

    lines = [
        json.dumps(
            {"seq": c.seq, "text": c.text, "tokens": c.token_count},
            ensure_ascii=False,
        )
        for c in chunks
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def chunks_from_jsonl(data: bytes) -> list[Chunk]:
    """JSONL 바이트를 청크 목록으로 역직렬화"""
    import json

    chunks: list[Chunk] = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        chunks.append(
            Chunk(
                seq=int(obj["seq"]),
                text=obj["text"],
                token_count=int(obj.get("tokens", 0)),
            )
        )
    return chunks
