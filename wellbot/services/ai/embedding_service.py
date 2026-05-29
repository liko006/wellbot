"""임베딩 및 FAISS 인덱스 서비스.

- Bedrock Titan Embeddings V2 호출로 텍스트 임베딩 생성
- FAISS 인덱스 빌드/직렬화/역직렬화
- 대화 단위 LRU 메모리 캐시 (다음 Phase 에서 활용)

업로드 시:
  1. 청크 텍스트 → Bedrock Titan 임베딩
  2. FAISS IndexFlatIP 인덱스 빌드
  3. faiss.serialize_index() → bytes → S3 저장

검색 시:
  1. S3 에서 index.faiss + chunks.jsonl 다운로드
  2. faiss.deserialize_index() → 메모리 인덱스
  3. query 임베딩 생성 → index.search() → top-k 청크
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Iterable, Sequence

import boto3
import numpy as np
from botocore.exceptions import ClientError

from wellbot.constants import (
    EMBED_MAX_RETRIES,
    EMBED_MAX_WORKERS,
    EMBED_RETRY_BASE_DELAY,
    FAISS_CACHE_MAX_CONVERSATIONS,
)
from wellbot.services.core.settings import get_config

log = logging.getLogger(__name__)


def _embedding_model_id() -> str:
    return get_config().embedding.model_id


def _embedding_dimension() -> int:
    return get_config().embedding.dimension


# ── Bedrock Titan 임베딩 호출 ──


@lru_cache(maxsize=1)
def _get_client():
    """Bedrock Runtime 클라이언트 (싱글턴). Bedrock 호출과 동일한 자격증명 사용."""
    region = os.environ.get(
        "AWS_REGION",
        os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    return boto3.client("bedrock-runtime", region_name=region)


def embed_text(text: str) -> np.ndarray:
    """단일 텍스트를 임베딩한다. 쓰로틀링 시 지수 백오프로 재시도.

    Returns:
        shape=(_embedding_dimension(),) float32 array.
    """
    client = _get_client()
    body = json.dumps({"inputText": text}).encode("utf-8")

    for attempt in range(EMBED_MAX_RETRIES + 1):
        try:
            response = client.invoke_model(
                modelId=_embedding_model_id(),
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
            embedding = payload.get("embedding") or []
            arr = np.asarray(embedding, dtype=np.float32)
            if arr.shape[0] != _embedding_dimension():
                raise RuntimeError(
                    f"임베딩 차원 불일치: 예상 {_embedding_dimension()}, 실제 {arr.shape[0]}"
                )
            return arr
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("ThrottlingException", "TooManyRequestsException"):
                if attempt < EMBED_MAX_RETRIES:
                    # 지수 백오프 + 지터(jitter)
                    delay = EMBED_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.3)
                    log.warning(
                        "임베딩 쓰로틀링 (attempt %d/%d), %.1f초 후 재시도",
                        attempt + 1, EMBED_MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
            raise

    # 여기 도달하면 안 되지만 안전장치
    raise RuntimeError(f"임베딩 재시도 {EMBED_MAX_RETRIES}회 초과")


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """여러 텍스트를 병렬 임베딩한다.

    ThreadPoolExecutor 로 동시 요청하여 I/O 대기 시간을 줄인다.
    빈 텍스트는 API 호출 없이 제로 벡터로 처리.

    Returns:
        shape=(N, _embedding_dimension()) float32 array.
    """
    if not texts:
        return np.empty((0, _embedding_dimension()), dtype=np.float32)

    # 빈 텍스트는 제로 벡터로 처리 (API 호출 불필요)
    results: list[tuple[int, np.ndarray]] = []
    to_embed: list[tuple[int, str]] = []

    for i, text in enumerate(texts):
        if not text.strip():
            results.append((i, np.zeros(_embedding_dimension(), dtype=np.float32)))
        else:
            to_embed.append((i, text))

    # 병렬 호출
    if to_embed:
        with ThreadPoolExecutor(max_workers=EMBED_MAX_WORKERS) as pool:
            futures = {
                pool.submit(embed_text, text): idx
                for idx, text in to_embed
            }
            for future in as_completed(futures):
                idx = futures[future]
                results.append((idx, future.result()))

    # 원래 순서로 정렬 후 합치기
    results.sort(key=lambda x: x[0])
    return np.vstack([vec for _, vec in results])


# ── FAISS 인덱스 빌드/직렬화 ──


def build_index(embeddings: np.ndarray):
    """임베딩 배열로 FAISS IndexFlatIP (내적) 인덱스를 빌드한다.

    Titan V2 는 정규화되지 않은 벡터를 반환하므로, 코사인 유사도를 위해
    명시적으로 L2 정규화한 뒤 내적 검색 사용.

    Args:
        embeddings: shape=(N, _embedding_dimension()) float32 array.

    Returns:
        faiss.IndexFlatIP 인덱스.
    """
    import faiss

    if embeddings.size == 0:
        # 빈 인덱스
        return faiss.IndexFlatIP(_embedding_dimension())

    # 정규화 (in-place)
    normalized = embeddings.copy()
    faiss.normalize_L2(normalized)

    index = faiss.IndexFlatIP(_embedding_dimension())
    index.add(normalized)
    return index


def serialize_index(index) -> bytes:
    """FAISS 인덱스를 바이트로 직렬화 (S3 PUT 용)."""
    import faiss

    buffer = faiss.serialize_index(index)
    # numpy array 로 반환되므로 bytes 로 변환
    return bytes(buffer)


def deserialize_index(data: bytes):
    """바이트에서 FAISS 인덱스를 역직렬화 (S3 GET 후)."""
    import faiss

    arr = np.frombuffer(data, dtype=np.uint8)
    return faiss.deserialize_index(arr)


def search_index(index, query_vec: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
    """인덱스에서 top-k 검색.

    Args:
        index: faiss 인덱스.
        query_vec: shape=(_embedding_dimension(),) float32.
        top_k: 반환 개수.

    Returns:
        (scores, indices) — 각 shape=(1, top_k) array.
    """
    import faiss

    q = query_vec.astype(np.float32).reshape(1, -1).copy()
    faiss.normalize_L2(q)
    k = min(top_k, index.ntotal) if index.ntotal > 0 else 0
    if k == 0:
        return np.empty((1, 0), dtype=np.float32), np.empty((1, 0), dtype=np.int64)
    return index.search(q, k)


# ── 대화 단위 LRU 캐시 ──


@dataclass
class ConversationIndex:
    """대화 하나에 속한 통합 FAISS 인덱스 + 청크 메타."""

    smry_id: str
    index: object                          # faiss.Index
    chunks: list[dict] = field(default_factory=list)
    # chunks[i] = {"file_no": int, "file_name": str, "seq": int, "text": str}
    missing_files: list[str] = field(default_factory=list)
    # 인덱스 다운로드/정합성 실패로 검색에서 제외된 파일명 목록


class FaissCache:
    """대화 단위 FAISS 인덱스 LRU 메모리 캐시."""

    def __init__(self, max_size: int = FAISS_CACHE_MAX_CONVERSATIONS) -> None:
        self._store: OrderedDict[str, ConversationIndex] = OrderedDict()
        self._max_size = max_size
        self._lock = Lock()

    def get(self, smry_id: str) -> ConversationIndex | None:
        with self._lock:
            if smry_id not in self._store:
                return None
            # LRU 갱신
            self._store.move_to_end(smry_id)
            return self._store[smry_id]

    def set(self, smry_id: str, conv_index: ConversationIndex) -> None:
        with self._lock:
            self._store[smry_id] = conv_index
            self._store.move_to_end(smry_id)
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, smry_id: str) -> None:
        with self._lock:
            self._store.pop(smry_id, None)


_cache = FaissCache()


def get_cache() -> FaissCache:
    """전역 FAISS 캐시 인스턴스."""
    return _cache


# ── 대화별 인덱스 로드 + 검색 ──


def load_conversation_index(smry_id: str) -> ConversationIndex:
    """대화에 속한 모든 파일의 청크/인덱스를 S3 에서 로드해 통합한다.

    - 각 파일의 chunks.jsonl + index.faiss 를 S3 에서 GET
    - 모든 임베딩을 하나의 IndexFlatIP 로 merge
    - chunks 리스트는 flat: [{file_no, file_name, seq, text}, ...]
      -> FAISS 검색 결과 인덱스 → chunks[idx] 로 바로 매핑 가능

    파일이 처리 중이거나 실패한 경우(S3 에 파생물 부재)는 스킵.
    """
    # 순환 import 방지 위해 lazy
    from wellbot.services.files import attachment_service
    from wellbot.services.files import chunker as chunker_mod
    from wellbot.services.files import storage_service

    import faiss

    atts = attachment_service.get_conversation_attachments(smry_id)

    flat_chunks: list[dict] = []
    vectors: list[np.ndarray] = []
    missing_files: list[str] = []

    for att in atts:
        if not att.s3_prefix:
            # 이미지 등 파생물이 없는 파일은 정상 스킵 (missing 으로 표기 안 함)
            continue

        # 처리 미완료 또는 검색 가능한 청크가 없는 파일은 S3 호출 없이 스킵
        # token_count is None: 처리 중
        # token_count == 0: 이미지, 파싱 결과 비어있음 등 (S3 파생물 없음)
        if not att.token_count:
            if att.token_count is None:
                log.info(
                    "load_conversation_index: 처리 미완료 스킵 file=%s (file_no=%d)",
                    att.file_name, att.file_no,
                )
                missing_files.append(att.file_name)
            continue

        chunks_key = f"{att.s3_prefix}chunks.jsonl"
        index_key = f"{att.s3_prefix}index.faiss"

        try:
            chunks_bytes = storage_service.download_bytes(chunks_key)
            index_bytes = storage_service.download_bytes(index_key)
        except Exception as exc:
            log.warning(
                "load_conversation_index: 인덱스 다운로드 실패 file=%s err=%s",
                att.file_name, exc,
            )
            missing_files.append(att.file_name)
            continue

        chunks = chunker_mod.chunks_from_jsonl(chunks_bytes)
        file_index = deserialize_index(index_bytes)

        # 이 파일의 모든 벡터를 추출하려면 reconstruct 를 사용
        n = file_index.ntotal
        if n != len(chunks) or n == 0:
            log.warning(
                "load_conversation_index: 정합성 불일치 file=%s ntotal=%d chunks=%d",
                att.file_name, n, len(chunks),
            )
            missing_files.append(att.file_name)
            continue
        try:
            file_vectors = np.vstack(
                [file_index.reconstruct(i) for i in range(n)]
            )
        except Exception as exc:
            log.warning(
                "load_conversation_index: 벡터 reconstruct 실패 file=%s err=%s",
                att.file_name, exc,
            )
            missing_files.append(att.file_name)
            continue

        vectors.append(file_vectors)
        for c in chunks:
            flat_chunks.append(
                {
                    "file_no": att.file_no,
                    "file_name": att.file_name,
                    "seq": c.seq,
                    "text": c.text,
                }
            )

    # 통합 인덱스 구축
    if vectors:
        all_vectors = np.vstack(vectors).astype(np.float32)
        # build_index 는 normalize_L2 + IndexFlatIP
        index = build_index(all_vectors)
    else:
        index = faiss.IndexFlatIP(_embedding_dimension())

    return ConversationIndex(
        smry_id=smry_id,
        index=index,
        chunks=flat_chunks,
        missing_files=missing_files,
    )


def get_or_load(smry_id: str) -> ConversationIndex:
    """캐시 우선 → 없으면 S3 에서 로드.

    missing_files 가 있는 캐시는 재로드하여 처리 완료된 파일을 반영한다.
    """
    cached = _cache.get(smry_id)
    if cached is not None and not cached.missing_files:
        return cached
    conv_index = load_conversation_index(smry_id)
    _cache.set(smry_id, conv_index)
    return conv_index


def _normalize_name(s: str) -> str:
    """파일명 fuzzy 매칭용 정규화 - lower + 공백/언더스코어/하이픈 제거."""
    return (
        s.lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def _strip_ext(s: str) -> str:
    """확장자 제거 (정규화된 문자열에 적용)."""
    dot = s.rfind(".")
    return s[:dot] if dot > 0 else s


def _matches_file_names(chunk_name: str, allowed_norm: list[str]) -> bool:
    """정규화 substring 양방향 + 확장자 제거 substring 폴백 매칭."""
    cn = _normalize_name(chunk_name)
    cn_no_ext = _strip_ext(cn)
    for a in allowed_norm:
        if not a:
            continue
        if a in cn or cn in a:
            return True
        a_no_ext = _strip_ext(a)
        if a_no_ext and (a_no_ext in cn_no_ext or cn_no_ext in a_no_ext):
            return True
    return False


def search_conversation(
    smry_id: str,
    query: str,
    top_k: int = 5,
    file_ids: Iterable[int] | None = None,
    file_names: Iterable[str] | None = None,
) -> dict:
    """대화 인덱스에서 유사도 top-k 검색.

    매칭 우선순위:
      1. file_ids 지정 → chunk["file_no"] 정확 매칭
      2. file_names 지정 → 정규화 substring 양방향 + 확장자 제거 폴백
      3. 위 1·2 모두 0건 → 필터 무시하고 전체 검색 (fallback note 부착)

    Returns:
        {
            "results": [{file_no, file_name, seq, text, score}, ...],
            "fallback": str | None,         # 필터가 적용/무시된 사유
            "missing_files": list[str],     # 인덱스 누락 파일명
        }
    """
    conv_index = get_or_load(smry_id)
    missing = list(conv_index.missing_files)
    empty_response: dict = {
        "results": [],
        "fallback": None,
        "missing_files": missing,
    }
    if conv_index.index is None or conv_index.index.ntotal == 0:
        return empty_response

    query_vec = embed_text(query)

    has_filter = bool(file_ids) or bool(file_names)
    fetch_k = top_k * 3 if has_filter else top_k
    scores, ids = search_index(conv_index.index, query_vec, fetch_k)

    allowed_ids: set[int] | None = None
    if file_ids:
        allowed_ids = {int(i) for i in file_ids}
    allowed_names_norm: list[str] | None = None
    if file_names:
        allowed_names_norm = [
            _normalize_name(n) for n in file_names if n and n.strip()
        ] or None

    def _collect(
        require_id: bool,
        require_name: bool,
    ) -> list[dict]:
        out: list[dict] = []
        for score, idx in zip(scores[0].tolist(), ids[0].tolist()):
            if idx < 0 or idx >= len(conv_index.chunks):
                continue
            chunk = conv_index.chunks[idx]
            if require_id and allowed_ids is not None:
                if chunk["file_no"] not in allowed_ids:
                    continue
            if require_name and allowed_names_norm is not None:
                if not _matches_file_names(chunk["file_name"], allowed_names_norm):
                    continue
            out.append({**chunk, "score": float(score)})
            if len(out) >= top_k:
                break
        return out

    fallback_note: str | None = None
    results: list[dict] = []

    if allowed_ids is not None:
        results = _collect(require_id=True, require_name=False)
        if not results and allowed_names_norm is not None:
            results = _collect(require_id=False, require_name=True)
            if results:
                fallback_note = "file_ids 매칭 실패 - file_names 폴백 적용"
    elif allowed_names_norm is not None:
        results = _collect(require_id=False, require_name=True)

    # 필터가 있었는데 0건이면 전체 검색 폴백
    if has_filter and not results:
        # 필터 무시 전체 검색은 원래 fetch 결과를 그대로 사용
        results = _collect(require_id=False, require_name=False)
        if results:
            fallback_note = (
                "지정 필터 매칭 실패 - 전체 첨부에서 검색"
            )
    elif not has_filter:
        results = _collect(require_id=False, require_name=False)

    return {
        "results": results,
        "fallback": fallback_note,
        "missing_files": missing,
    }
