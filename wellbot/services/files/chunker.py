"""텍스트 청킹 서비스.

파싱된 문서 텍스트를 검색용 청크로 분할.
토큰 카운팅은 간단한 추정(공백 단위 단어 × 1.3)을 사용.

Bedrock Titan embedding 의 입력 한도는 8192 토큰
→ CHUNK_SIZE_TOKENS=1000 은 안전.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from wellbot.constants import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS, AVG_TOKENS_PER_WORD


@dataclass(frozen=True)
class Chunk:
    """청킹 결과"""

    seq: int          # 청크 순번 (0부터)
    text: str         # 청크 텍스트
    token_count: int  # 추정 토큰 수


def estimate_tokens(text: str) -> int:
    """간단한 토큰 수 추정.

    정확한 토크나이저 없이도 청킹/가드 용도로 충분.
    """
    if not text:
        return 0
    words = text.split()
    return max(1, int(len(words) * AVG_TOKENS_PER_WORD))


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
        4. 단일 문단이 size 초과면 단어 기준 강제 분할

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
            # 문단 끝에서 overlap 토큰만큼만 (단어 기준)
            words = tail.split()
            overlap_words = max(1, int(overlap / AVG_TOKENS_PER_WORD))
            tail_text = " ".join(words[-overlap_words:])
            buffer = [tail_text]
            buffer_tokens = estimate_tokens(tail_text)
        else:
            buffer = []
            buffer_tokens = 0

    for paragraph in paragraphs:
        p_tokens = estimate_tokens(paragraph)

        if p_tokens > size:
            # 단일 문단이 size 초과 → 의미 단위 보존 불가. 단어 기준 강제 분할
            flush()
            for sub in _force_split_by_words(paragraph, size, overlap):
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
    return chunks


def _force_split_by_words(
    text: str,
    size: int,
    overlap: int,
) -> list[str]:
    """단일 문단이 size 초과 시 단어 단위 강제 분할"""
    words = text.split()
    if not words:
        return []

    # 토큰 ≈ 단어 × AVG_TOKENS_PER_WORD → 단어 수 = size / AVG_TOKENS_PER_WORD
    words_per_chunk = max(1, int(size / AVG_TOKENS_PER_WORD))
    overlap_words = max(0, int(overlap / AVG_TOKENS_PER_WORD)) if overlap > 0 else 0

    parts: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + words_per_chunk, len(words))
        parts.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap_words if overlap_words > 0 else end
    return parts


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
