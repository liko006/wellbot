"""첨부파일 S3 인덱스 정합성 검증 스크립트.

DB 의 처리 완료된 첨부파일(token_count > 0) 에 대해:
  1. S3 에 chunks.jsonl + index.faiss 가 실제 존재하는지 확인
  2. load_conversation_index() 가 missing_files 없이 로드되는지 확인
  3. 인덱스 ntotal 과 chunks 수의 정합성 확인

Usage:
    uv run python scripts/verify_attachment_index.py
    uv run python scripts/verify_attachment_index.py --smry SMRY_ID  # 특정 대화만
    uv run python scripts/verify_attachment_index.py --limit 5       # 상위 N 개 대화만
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

# scripts/ 에서 직접 실행 시 패키지 임포트를 위해 프로젝트 루트를 sys.path 에 추가
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func

from wellbot.models.attachment import Attachment
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_message_attachment import ChatMessageAttachment
from wellbot.services.ai import embedding_service
from wellbot.services.core.database import get_session
from wellbot.services.files import storage_service
from wellbot.services.files.attachment_service import get_conversation_attachments

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("verify")


def find_smry_ids_with_attachments(limit: int | None = None) -> list[str]:
    """첨부파일이 있는 대화 ID 목록 (최신순)"""
    with get_session() as session:
        q = (
            session.query(
                ChatMessage.chtb_tlk_smry_id,
                func.max(Attachment.upd_dtm).label("latest"),
                func.count(Attachment.atch_file_no).label("cnt"),
            )
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.chtb_tlk_id == ChatMessage.chtb_tlk_id,
            )
            .join(
                Attachment,
                Attachment.atch_file_no == ChatMessageAttachment.atch_file_no,
            )
            .group_by(ChatMessage.chtb_tlk_smry_id)
            .order_by(func.max(Attachment.upd_dtm).desc())
        )
        if limit:
            q = q.limit(limit)
        rows = q.all()
    return [r[0] for r in rows if r[0]]


def verify_smry(smry_id: str) -> dict:
    """단일 대화 S3 인덱스 정합성 검증"""
    print(f"\n{'=' * 70}")
    print(f"smry_id={smry_id}")
    print(f"{'=' * 70}")

    atts = get_conversation_attachments(smry_id)
    print(f"  첨부 총 {len(atts)}개")

    s3_status: list[dict] = []
    for att in atts:
        chunks_key = f"{att.s3_prefix}chunks.jsonl" if att.s3_prefix else None
        index_key = f"{att.s3_prefix}index.faiss" if att.s3_prefix else None
        original_keys: list[str] = []
        if att.s3_prefix:
            ext = Path(att.file_name).suffix.lower()
            original_keys.append(f"{att.s3_prefix}original{ext}")

        chunks_exists = (
            storage_service.object_exists(chunks_key) if chunks_key else False
        )
        index_exists = (
            storage_service.object_exists(index_key) if index_key else False
        )
        original_exists = any(
            storage_service.object_exists(k) for k in original_keys
        )

        # DB 기록과 실제 S3 키 목록을 비교하기 위해 조회
        actual_keys = (
            storage_service.list_objects(att.s3_prefix) if att.s3_prefix else []
        )

        s3_status.append(
            {
                "att": att,
                "chunks_exists": chunks_exists,
                "index_exists": index_exists,
                "original_exists": original_exists,
                "actual_keys": actual_keys,
            }
        )

        print(
            f"  - file_no={att.file_no} name={att.file_name!r} "
            f"token_count={att.token_count} prefix={att.s3_prefix!r}"
        )
        print(
            f"      original={original_exists} chunks={chunks_exists} "
            f"index={index_exists}"
        )
        if att.token_count and (not chunks_exists or not index_exists):
            print("      [WARN] token_count>0 인데 파생물 누락! (DB-S3 정합성 깨짐)")
        if actual_keys:
            print(f"      실제 S3 키: {actual_keys}")

    # 캐시를 무시하고 실제 로드하여 정합성 확인
    embedding_service.get_cache().invalidate(smry_id)
    try:
        conv_index = embedding_service.load_conversation_index(smry_id)
        print(
            f"\n  load_conversation_index → ntotal={conv_index.index.ntotal} "
            f"chunks={len(conv_index.chunks)} missing={conv_index.missing_files}"
        )

        per_file: dict[int, int] = defaultdict(int)
        for c in conv_index.chunks:
            per_file[c["file_no"]] += 1
        for fno, n in per_file.items():
            print(f"    file_no={fno}: {n} chunks")

    except Exception as exc:
        print(f"  [FAIL] load_conversation_index 실패: {exc}")
        return {"smry_id": smry_id, "load_ok": False, "missing": [], "atts": s3_status}

    return {
        "smry_id": smry_id,
        "load_ok": True,
        "missing": conv_index.missing_files,
        "ntotal": conv_index.index.ntotal,
        "chunks_len": len(conv_index.chunks),
        "atts": s3_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smry", help="특정 smry_id 만 검증")
    parser.add_argument(
        "--limit", type=int, default=3, help="검증할 대화 수 (default=3)"
    )
    parser.add_argument(
        "--search",
        help="추가로 search_conversation 동작 확인 (지정한 query 로 검색)",
    )
    args = parser.parse_args()

    if args.smry:
        smry_ids = [args.smry]
    else:
        smry_ids = find_smry_ids_with_attachments(limit=args.limit)
        if not smry_ids:
            print("첨부파일이 있는 대화가 없습니다.")
            return 1
        print(f"검증 대상: {len(smry_ids)}개 대화 (최신순)")

    results = [verify_smry(s) for s in smry_ids]

    if args.search:
        print(f"\n{'=' * 70}")
        print(f"RAG 검색 테스트: query={args.search!r}")
        print(f"{'=' * 70}")
        for s in smry_ids:
            try:
                res = embedding_service.search_conversation(s, args.search, top_k=3)
                print(f"\n  smry_id={s}")
                print(
                    f"    fallback={res['fallback']} missing={res['missing_files']}"
                )
                for hit in res["results"]:
                    text_preview = hit["text"][:80].replace("\n", " ")
                    print(
                        f"    score={hit['score']:.3f} "
                        f"file={hit['file_name']!r} seq={hit['seq']} "
                        f"text={text_preview!r}..."
                    )
            except Exception as exc:
                print(f"  smry_id={s} [FAIL] {exc}")

    print(f"\n{'=' * 70}")
    print("요약")
    print(f"{'=' * 70}")
    for r in results:
        status = "[OK]  " if r["load_ok"] and not r["missing"] else "[FAIL]"
        miss = f" missing={r['missing']}" if r.get("missing") else ""
        ntotal = r.get("ntotal", "-")
        print(f"  {status} {r['smry_id']} ntotal={ntotal}{miss}")

    bad = [r for r in results if not r["load_ok"] or r["missing"]]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
