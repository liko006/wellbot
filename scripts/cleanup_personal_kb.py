"""
cleanup_personal_kb.py

퇴사자 등 사용자 1명의 개인 KB 관련 리소스를 일괄 정리하는 관리자 스크립트.

삭제 대상 (AWS 의존성 역순):
    1. Bedrock Data Source
    2. Bedrock Knowledge Base
    3. S3 Vectors Index
    4. S3 main bucket 의 users/{emp_no}/ prefix
    5. S3 intermediate bucket 의 users/{emp_no}/processed/ prefix
    6. DB 의 AGNT_MMRY_USE_N (PERSONAL) 행

특징:
    - 멱등: DB 행이 이미 없어도 다른 리소스가 남아 있으면 자동 감지해서 정리
    - --dry-run: 어떤 리소스가 삭제될지 미리보기만 (실제 삭제 없음)
    - --yes: 확인 프롬프트 건너뛰기 (자동화 호출용)
    - 실패 시 즉시 중단 (재실행하면 이어서 정리 가능)

사용 예:
    # 미리보기
    python scripts/cleanup_personal_kb.py --emp-no 12345678 --dry-run

    # 실제 삭제 (y/N 프롬프트)
    python scripts/cleanup_personal_kb.py --emp-no 12345678

    # 프롬프트 건너뛰기
    python scripts/cleanup_personal_kb.py --emp-no 12345678 --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# 프로젝트 루트를 sys.path 에 추가 (scripts/ 에서 직접 실행하기 위한 목적)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wellbot.env import init_env  # noqa: E402
init_env()  # KB 모듈의 모듈레벨 os.getenv 보장 (다른 wellbot import 전에 호출)

from wellbot.models import AgntMmryUseN  # noqa: E402
from wellbot.services.knowledgebase.config import get_kb_config  # noqa: E402
from wellbot.services.core.database import get_session  # noqa: E402


# ──────────────────────────────────────────────
# 상수 (personal_kb_manager 와 동일하게 맞춤)
# ──────────────────────────────────────────────
AGNT_ID_KB = "Agnt_KnowBase"
TYPE_PERSONAL = "PERSONAL"
SEQ_PERSONAL = 1
KB_INFO_SEP = "||"


def _kb_name(emp_no: str) -> str:
    return f"aiinno-bedrock-kb-personal-{emp_no}"


def _vector_index_name(emp_no: str) -> str:
    # kb_utils.create_vector_index 와 동일: emp_no.lower()
    return f"aiinno-bedrock-kb-personal-vector-index-{emp_no.lower()}"


def _raw_prefix_root(emp_no: str) -> str:
    """users/{emp_no}/ 전체 (raw/ + originals/ 포함)"""
    return f"users/{emp_no}/"


def _processed_prefix(emp_no: str) -> str:
    return f"users/{emp_no}/processed/"


# ──────────────────────────────────────────────
# DB 조회 / 삭제
# ──────────────────────────────────────────────
def fetch_kb_info_from_db(emp_no: str) -> Optional[dict]:
    """DB 의 AGNT_MMRY_USE_N 에서 개인 KB 정보 조회. 미등록이면 None"""
    with get_session() as session:
        row = (
            session.query(AgntMmryUseN)
            .filter(
                AgntMmryUseN.agnt_id == AGNT_ID_KB,
                AgntMmryUseN.emp_no == emp_no,
                AgntMmryUseN.agnt_seq == SEQ_PERSONAL,
                AgntMmryUseN.agnt_type_dscr_cntt == TYPE_PERSONAL,
            )
            .first()
        )
        if not row or not row.agnt_mmry_path_addr:
            return None
        parts = row.agnt_mmry_path_addr.split(KB_INFO_SEP, 1)
        if len(parts) != 2:
            return None
        return {"kb_id": parts[0], "data_source_id": parts[1]}


def delete_db_row(emp_no: str) -> int:
    """AGNT_MMRY_USE_N 의 PERSONAL 행 삭제. 반환: 삭제된 행 수"""
    with get_session() as session:
        deleted = (
            session.query(AgntMmryUseN)
            .filter(
                AgntMmryUseN.agnt_id == AGNT_ID_KB,
                AgntMmryUseN.emp_no == emp_no,
                AgntMmryUseN.agnt_seq == SEQ_PERSONAL,
                AgntMmryUseN.agnt_type_dscr_cntt == TYPE_PERSONAL,
            )
            .delete()
        )
    return int(deleted)


# ──────────────────────────────────────────────
# Bedrock 조회 / 삭제
# ──────────────────────────────────────────────
def find_kb_by_name(bedrock_agent, kb_name: str) -> Optional[dict]:
    """Bedrock 에서 이름으로 KB 검색 (DB 정보 없을 때 fallback)"""
    try:
        paginator = bedrock_agent.get_paginator("list_knowledge_bases")
        for page in paginator.paginate():
            for kb in page.get("knowledgeBaseSummaries", []):
                if kb["name"] == kb_name:
                    kb_id = kb["knowledgeBaseId"]
                    ds_resp = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
                    ds_summaries = ds_resp.get("dataSourceSummaries", [])
                    ds_id = ds_summaries[0]["dataSourceId"] if ds_summaries else None
                    return {"kb_id": kb_id, "data_source_id": ds_id}
    except ClientError:
        pass
    return None


def check_ingestion_in_progress(bedrock_agent, kb_id: str, data_source_id: str) -> bool:
    """진행 중인 ingestion job 이 있는지 확인"""
    try:
        resp = bedrock_agent.list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
            maxResults=1,
        )
        jobs = resp.get("ingestionJobSummaries", [])
        if jobs and jobs[0]["status"] in ("STARTING", "IN_PROGRESS"):
            return True
    except ClientError:
        pass
    return False


def delete_data_source(bedrock_agent, kb_id: str, data_source_id: str) -> bool:
    """Data Source 삭제. 반환: 실제로 삭제했으면 True, 이미 없었으면 False"""
    try:
        bedrock_agent.delete_data_source(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
        )
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "ValidationException"):
            return False
        raise


def delete_knowledge_base(bedrock_agent, kb_id: str) -> bool:
    """KB 삭제. 반환: 실제 삭제했으면 True"""
    try:
        bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "ValidationException"):
            return False
        raise


# ──────────────────────────────────────────────
# S3 Vectors 삭제
# ──────────────────────────────────────────────
def delete_vector_index(s3vectors, vector_bucket: str, index_name: str) -> bool:
    """S3 Vectors index 삭제. 반환: 실제 삭제했으면 True"""
    try:
        s3vectors.delete_index(
            vectorBucketName=vector_bucket,
            indexName=index_name,
        )
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = str(e).lower()
        if code in ("NotFoundException", "ResourceNotFoundException") or "not found" in msg:
            return False
        raise


# ──────────────────────────────────────────────
# S3 일반 객체 조회 / 삭제
# ──────────────────────────────────────────────
def list_s3_keys(s3, bucket: str, prefix: str) -> list[str]:
    """prefix 하위 모든 객체 키 반환"""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
    return keys


def delete_s3_keys(s3, bucket: str, keys: list[str]) -> int:
    """S3 객체 일괄 삭제 (1000개 배치). 반환: 삭제된 객체 수"""
    if not keys:
        return 0
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch]},
        )
        deleted += len(batch)
    return deleted


# ──────────────────────────────────────────────
# 메인 흐름
# ──────────────────────────────────────────────
def gather_resources(emp_no: str, kb_cfg: dict, clients: dict) -> dict:
    """삭제 대상 리소스 정보 수집 (DB + AWS 조회)"""
    s3 = clients["s3"]
    bedrock_agent = clients["bedrock_agent"]

    # DB 조회
    db_record = fetch_kb_info_from_db(emp_no)

    # DB 정보 없으면 Bedrock 에서 이름으로 fallback 검색
    kb_record = db_record
    if kb_record is None:
        kb_record = find_kb_by_name(bedrock_agent, _kb_name(emp_no))

    # S3 객체 수 집계
    main_bucket = kb_cfg["s3_bucket"]
    int_bucket = kb_cfg["s3_intermediate_bucket"]
    main_keys = list_s3_keys(s3, main_bucket, _raw_prefix_root(emp_no))
    processed_keys = list_s3_keys(s3, int_bucket, _processed_prefix(emp_no))

    return {
        "db_record": db_record,
        "kb_record": kb_record,
        "main_bucket": main_bucket,
        "main_keys": main_keys,
        "int_bucket": int_bucket,
        "processed_keys": processed_keys,
        "vector_bucket": kb_cfg["s3_vector_bucket"],
        "vector_index_name": _vector_index_name(emp_no),
    }


def print_preview(emp_no: str, info: dict) -> None:
    """삭제될 리소스 미리보기 출력"""
    print(f"\n📋 다음 리소스가 삭제됩니다 (emp_no={emp_no}):")

    if info["db_record"]:
        print("   - DB 행: AGNT_MMRY_USE_N (Agnt_KnowBase / PERSONAL)")
    else:
        print("   - DB 행: (없음 - 스킵)")

    if info["kb_record"]:
        kb_id = info["kb_record"]["kb_id"]
        ds_id = info["kb_record"].get("data_source_id") or "(없음)"
        print(f"   - Bedrock KB: kb_id={kb_id}")
        print(f"   - Data Source: ds_id={ds_id}")
    else:
        print("   - Bedrock KB / Data Source: (없음 - 스킵)")

    print(f"   - S3 Vectors Index: {info['vector_index_name']}")
    print(
        f"   - S3 main: s3://{info['main_bucket']}/users/{emp_no}/ "
        f"({len(info['main_keys'])}개 객체)"
    )
    print(
        f"   - S3 intermediate: s3://{info['int_bucket']}/{_processed_prefix(emp_no)} "
        f"({len(info['processed_keys'])}개 객체)"
    )


def execute_cleanup(emp_no: str, info: dict, clients: dict) -> None:
    """실제 삭제 수행. 의존성 역순으로 진행"""
    s3 = clients["s3"]
    bedrock_agent = clients["bedrock_agent"]
    s3vectors = clients["s3vectors"]

    kb_record = info["kb_record"]

    # [1/7] Ingestion 진행 중 체크
    print("[1/7] Ingestion 진행 중 체크...", end=" ", flush=True)
    if kb_record and kb_record.get("data_source_id"):
        if check_ingestion_in_progress(
            bedrock_agent, kb_record["kb_id"], kb_record["data_source_id"]
        ):
            print("✗")
            print("   → 진행 중인 ingestion 이 있습니다. 완료 후 다시 실행해주세요.")
            sys.exit(1)
    print("✓")

    # [2/7] Data Source 삭제
    print("[2/7] Data Source 삭제...", end=" ", flush=True)
    if kb_record and kb_record.get("data_source_id"):
        ok = delete_data_source(
            bedrock_agent, kb_record["kb_id"], kb_record["data_source_id"]
        )
        print("✓" if ok else "✓ (이미 없음)")
    else:
        print("✓ (스킵)")

    # [3/7] Bedrock KB 삭제
    print("[3/7] Bedrock KB 삭제...", end=" ", flush=True)
    if kb_record:
        ok = delete_knowledge_base(bedrock_agent, kb_record["kb_id"])
        print("✓" if ok else "✓ (이미 없음)")
    else:
        print("✓ (스킵)")

    # [4/7] S3 Vectors Index 삭제
    print("[4/7] S3 Vectors Index 삭제...", end=" ", flush=True)
    ok = delete_vector_index(s3vectors, info["vector_bucket"], info["vector_index_name"])
    print("✓" if ok else "✓ (이미 없음)")

    # [5/7] S3 main bucket prefix 삭제
    print(f"[5/7] S3 main bucket 삭제 ({len(info['main_keys'])}개)...", end=" ", flush=True)
    if info["main_keys"]:
        delete_s3_keys(s3, info["main_bucket"], info["main_keys"])
        print("✓")
    else:
        print("✓ (이미 없음)")

    # [6/7] S3 intermediate processed 삭제
    print(
        f"[6/7] S3 intermediate 삭제 ({len(info['processed_keys'])}개)...",
        end=" ",
        flush=True,
    )
    if info["processed_keys"]:
        delete_s3_keys(s3, info["int_bucket"], info["processed_keys"])
        print("✓")
    else:
        print("✓ (이미 없음)")

    # [7/7] DB 행 삭제
    print("[7/7] DB 행 삭제...", end=" ", flush=True)
    if info["db_record"]:
        n = delete_db_row(emp_no)
        print(f"✓ ({n}건)" if n else "✓ (이미 없음)")
    else:
        print("✓ (스킵)")


def main() -> None:
    parser = argparse.ArgumentParser(description="개인 KB 클린업 스크립트 (퇴사자 등)")
    parser.add_argument("--emp-no", required=True, help="대상 사용자의 emp_no")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 삭제 없이 미리보기만 출력",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="확인 프롬프트 건너뛰기 (자동화 호출용)",
    )
    args = parser.parse_args()

    emp_no = args.emp_no.strip()
    if not emp_no:
        print("✗ --emp-no 값이 비어 있습니다.")
        sys.exit(1)

    # Config + AWS clients
    try:
        kb_cfg = get_kb_config()["personal_kb"]
    except Exception as e:
        print(f"✗ knowBase.yaml 설정 로드 실패: {e}")
        sys.exit(1)

    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    clients = {
        "s3": boto3.client("s3"),
        "bedrock_agent": boto3.client("bedrock-agent", region_name=region),
        "s3vectors": boto3.client("s3vectors", region_name=region),
    }

    # 리소스 정보 수집
    info = gather_resources(emp_no, kb_cfg, clients)

    # 미리보기 출력
    print_preview(emp_no, info)

    # 정리할 리소스가 하나도 없으면 조기 종료
    nothing_to_delete = (
        info["db_record"] is None
        and info["kb_record"] is None
        and not info["main_keys"]
        and not info["processed_keys"]
    )
    if nothing_to_delete:
        # S3 Vectors index 존재 여부는 API 호출 없이는 확신 불가
        # 그래도 위 4개가 모두 비었으면 사실상 정리할 게 없다고 보고 알림
        print("\nℹ 정리할 리소스가 없는 것 같습니다. (S3 Vectors index 는 별도로 시도해주세요)")

    if args.dry_run:
        print("\n🔍 --dry-run 모드: 실제 삭제 없이 종료합니다.")
        return

    # 확인 프롬프트
    print("\n⚠ 이 작업은 되돌릴 수 없습니다.")
    if not args.yes:
        try:
            ans = input("정말 삭제하시겠습니까? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "y":
            print("취소되었습니다.")
            return

    print()
    execute_cleanup(emp_no, info, clients)
    print(f"\n✅ 정리 완료: emp_no={emp_no}")


if __name__ == "__main__":
    main()
