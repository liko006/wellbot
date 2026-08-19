"""개인/팀 Knowledge Base teardown 서비스.

퇴사자 정리, 팀 해체 등으로 한 소유자의 KB 관련 리소스를 **의존성 역순으로 전부**
삭제한다. 운용(업로드·문서 삭제)과 달리 비가역·전면 삭제라 별도 모듈로 둔다.

**호출자는 둘이다** — CLI(``scripts/cleanup_personal_kb.py``)와 admin UI. 그래서
admin 계층이 아니라 도메인 계층에 둔다.

scope (``kind``):
    - ``personal``: owner=emp_no  → users{env}/{emp_no}/
    - ``team``:     owner=dept_cd → teams{env}/{dept_cd}/

삭제 순서 (역순인 이유):
    Data Source → Knowledge Base → S3 Vectors 인덱스 → S3 원본 → S3 중간산출물 → DB 행.
    DS 삭제가 곧 인덱스에서 그 DS 의 벡터를 지우는 동작(dataDeletionPolicy 기본값
    ``Delete``)이므로 인덱스를 먼저 지우면 그 정리가 향할 곳이 없어진다. 벡터 스토어
    자체는 KB/DS 를 지워도 남으므로 별도로 지운다.

멱등:
    DB 행이 이미 없어도 이름으로 Bedrock/S3 를 조회해 남은 리소스를 정리한다.
    각 삭제는 NotFound 를 "이미 없음"으로 흡수하고, 어느 단계에서 실패하면 그
    지점에서 멈춘다 — 같은 대상으로 다시 실행하면 남은 것부터 이어서 정리된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from wellbot.models import AgntMmryUseN
from wellbot.services.core.database import get_session
from wellbot.services.files import storage_service
from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_utils import (
    AGNT_ID_KB,
    KB_INFO_SEP,
    get_bedrock_agent,
    get_s3vectors,
    is_ingestion_in_progress,
    kb_resource_name,
    kb_root_prefix,
    processed_prefix,
    vector_index_name,
)
from wellbot.services.knowledgebase.personal_kb_manager import (
    SEQ_PERSONAL,
    TYPE_PERSONAL,
    get_user_kb,
)
from wellbot.services.knowledgebase.team_kb_manager import (
    SEQ_TEAM,
    TYPE_TEAM,
    find_team_kb_by_dept,
)

log = logging.getLogger(__name__)

KIND_PERSONAL = "personal"
KIND_TEAM = "team"

# 단계 상태
STEP_DONE = "done"
STEP_SKIPPED = "skipped"
STEP_FAILED = "failed"


@dataclass(frozen=True)
class CleanupStep:
    """teardown 한 단계의 결과."""

    name: str
    status: str           # STEP_DONE | STEP_SKIPPED | STEP_FAILED
    detail: str = ""

    @property
    def is_failed(self) -> bool:
        return self.status == STEP_FAILED


@dataclass(frozen=True)
class CleanupPlan:
    """삭제 대상 스냅샷 (미리보기 = CLI --dry-run, UI preview).

    execute_cleanup 은 이 스냅샷을 그대로 지운다. 수집과 삭제 사이에 새 객체가
    올라오면 그건 남는다 — teardown 대상은 이미 사용이 끝난 소유자라 수용 가능하며,
    남으면 재실행으로 정리된다.
    """

    kind: str
    owner: str
    kb_id: str = ""
    data_source_id: str = ""
    db_row_count: int = 0
    main_bucket: str = ""
    main_prefix: str = ""
    main_keys: list[str] = field(default_factory=list)
    intermediate_bucket: str = ""
    intermediate_prefix: str = ""
    intermediate_keys: list[str] = field(default_factory=list)
    vector_bucket: str = ""
    vector_index: str = ""

    @property
    def has_nothing_to_delete(self) -> bool:
        """DB·KB·S3 어디에도 지울 것이 없는 상태.

        S3 Vectors 인덱스 존재 여부는 조회 API 없이 확신할 수 없으므로 판단에서 제외한다.
        """
        return not (
            self.db_row_count or self.kb_id or self.main_keys or self.intermediate_keys
        )


# ──────────────────────────────────────────────
# DB 조회 / 삭제
# ──────────────────────────────────────────────
def _personal_rows_query(session, emp_no: str):
    return session.query(AgntMmryUseN).filter(
        AgntMmryUseN.agnt_id == AGNT_ID_KB,
        AgntMmryUseN.emp_no == emp_no,
        AgntMmryUseN.agnt_seq == SEQ_PERSONAL,
        AgntMmryUseN.agnt_type_dscr_cntt == TYPE_PERSONAL,
    )


def _team_rows_query(session, kb_id: str):
    """대상 KB 를 가리키는 **모든** TEAM 행.

    부서 조인이 아니라 kb_id 매칭인 이유: 같은 KB 를 실제로 공유하는 전원을 잡아야
    한다. 부서로 조인하면 전출·퇴사로 소속이 바뀐 과거 멤버의 행을 놓쳐 고아가 남는다.
    """
    return session.query(AgntMmryUseN).filter(
        AgntMmryUseN.agnt_id == AGNT_ID_KB,
        AgntMmryUseN.agnt_seq == SEQ_TEAM,
        AgntMmryUseN.agnt_type_dscr_cntt == TYPE_TEAM,
        AgntMmryUseN.agnt_mmry_path_addr.like(f"{kb_id}{KB_INFO_SEP}%"),
    )


def _count_db_rows(kind: str, owner: str, kb_id: str) -> int:
    """삭제 대상 DB 행 수. 팀은 kb_id 를 모르면 셀 수 없으므로 0."""
    with get_session() as session:
        if kind == KIND_PERSONAL:
            return int(_personal_rows_query(session, owner).count())
        if not kb_id:
            return 0
        return int(_team_rows_query(session, kb_id).count())


def _delete_db_rows(kind: str, owner: str, kb_id: str) -> int:
    """DB 행 삭제. 반환: 삭제된 행 수.

    삭제 직후 세션을 닫으므로 ORM 객체 동기화가 필요 없다(synchronize_session=False).
    """
    with get_session() as session:
        if kind == KIND_PERSONAL:
            query = _personal_rows_query(session, owner)
        elif kb_id:
            query = _team_rows_query(session, kb_id)
        else:
            return 0
        return int(query.delete(synchronize_session=False))


def _kb_record_from_db(kind: str, owner: str) -> dict | None:
    """DB 에 등록된 KB 정보 (없으면 None)."""
    if kind == KIND_PERSONAL:
        return get_user_kb(owner)
    return find_team_kb_by_dept(owner)


# ──────────────────────────────────────────────
# Bedrock 조회 / 삭제
# ──────────────────────────────────────────────
def _find_kb_by_name(kind: str, owner: str) -> dict | None:
    """이름으로 Bedrock KB 검색 (DB 행이 없을 때의 폴백).

    kb_utils.find_existing_kb 를 쓰지 않는 이유 — 그쪽은 **정상 사용 경로**용이라
    ``status == ACTIVE`` 이고 Data Source 가 있는 KB 만 돌려준다. teardown 은 오히려
    그 반대 상황(생성 실패로 FAILED 상태, 이전 정리가 DS 만 지우고 멈춘 상태)을
    정리해야 하므로 상태를 가리지 않고 DS 가 없어도 KB 를 잡아야 한다.

    반환: {"kb_id", "data_source_id"(없으면 "")} 또는 None.
    """
    kb_name = kb_resource_name(kind, owner)
    client = get_bedrock_agent()
    try:
        paginator = client.get_paginator("list_knowledge_bases")
        for page in paginator.paginate():
            for kb in page.get("knowledgeBaseSummaries", []) or []:
                if kb["name"] != kb_name:
                    continue
                kb_id = kb["knowledgeBaseId"]
                ds_resp = client.list_data_sources(knowledgeBaseId=kb_id)
                ds_list = ds_resp.get("dataSourceSummaries", []) or []
                return {
                    "kb_id": kb_id,
                    "data_source_id": ds_list[0]["dataSourceId"] if ds_list else "",
                }
    except ClientError:
        log.debug("KB 이름 조회 실패 (무시): kb_name=%s", kb_name, exc_info=True)
    return None


def _absorb_not_found(error: ClientError, codes: tuple[str, ...]) -> bool:
    """'이미 없음'에 해당하는 오류면 True, 아니면 예외를 다시 던지도록 False."""
    code = error.response.get("Error", {}).get("Code", "")
    return code in codes


def _delete_data_source(kb_id: str, data_source_id: str) -> bool:
    """Data Source 삭제. 반환: 실제로 지웠으면 True, 이미 없었으면 False."""
    try:
        get_bedrock_agent().delete_data_source(
            knowledgeBaseId=kb_id, dataSourceId=data_source_id,
        )
        return True
    except ClientError as e:
        if _absorb_not_found(e, ("ResourceNotFoundException", "ValidationException")):
            return False
        raise


def _delete_knowledge_base(kb_id: str) -> bool:
    """Knowledge Base 삭제. 반환: 실제로 지웠으면 True."""
    try:
        get_bedrock_agent().delete_knowledge_base(knowledgeBaseId=kb_id)
        return True
    except ClientError as e:
        if _absorb_not_found(e, ("ResourceNotFoundException", "ValidationException")):
            return False
        raise


def _delete_vector_index(vector_bucket: str, index_name: str) -> bool:
    """S3 Vectors 인덱스 삭제. 반환: 실제로 지웠으면 True."""
    try:
        get_s3vectors().delete_index(vectorBucketName=vector_bucket, indexName=index_name)
        return True
    except ClientError as e:
        if _absorb_not_found(e, ("NotFoundException", "ResourceNotFoundException")):
            return False
        if "not found" in str(e).lower():
            return False
        raise


# ──────────────────────────────────────────────
# S3 조회 / 삭제
# ──────────────────────────────────────────────
def _list_keys(bucket: str, prefix: str) -> list[str]:
    """prefix 하위 전 객체 키."""
    if not bucket or not prefix:
        return []
    keys: list[str] = []
    paginator = storage_service.get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
    return keys


def _delete_keys(bucket: str, keys: list[str]) -> int:
    """S3 객체 일괄 삭제 (1000개 배치). 반환: 삭제 요청한 객체 수."""
    if not keys:
        return 0
    client = storage_service.get_client()
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]},
        )
        deleted += len(batch)
    return deleted


# ──────────────────────────────────────────────
# 수집 (미리보기)
# ──────────────────────────────────────────────
def gather_resources(kind: str, owner: str) -> CleanupPlan:
    """삭제 대상을 DB + AWS 에서 수집. 읽기만 하므로 미리보기에 그대로 쓴다."""
    if kind not in (KIND_PERSONAL, KIND_TEAM):
        raise ValueError(f"알 수 없는 kind: {kind!r}")
    if not owner:
        raise ValueError("owner(사번 또는 부서코드)가 비어 있습니다.")

    record = _kb_record_from_db(kind, owner) or _find_kb_by_name(kind, owner) or {}
    kb_id = record.get("kb_id", "")
    data_source_id = record.get("data_source_id", "")

    cfg = get_kb_config()["personal_kb"]   # 인프라 키는 personal/shared 동일
    main_bucket = cfg.get("s3_bucket", "")
    intermediate_bucket = cfg.get("s3_intermediate_bucket", "")
    main_prefix = kb_root_prefix(kind, owner)
    intermediate = processed_prefix(kind, owner)

    return CleanupPlan(
        kind=kind,
        owner=owner,
        kb_id=kb_id,
        data_source_id=data_source_id,
        db_row_count=_count_db_rows(kind, owner, kb_id),
        main_bucket=main_bucket,
        main_prefix=main_prefix,
        main_keys=_list_keys(main_bucket, main_prefix),
        intermediate_bucket=intermediate_bucket,
        intermediate_prefix=intermediate,
        intermediate_keys=_list_keys(intermediate_bucket, intermediate),
        vector_bucket=cfg.get("s3_vector_bucket", ""),
        vector_index=vector_index_name(kind, owner),
    )


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────
def execute_cleanup(plan: CleanupPlan) -> list[CleanupStep]:
    """의존성 역순으로 삭제. 반환: 단계별 결과.

    한 단계가 실패하면 **거기서 멈추고** 그때까지의 결과를 돌려준다(뒤 단계는 목록에
    없다). 각 단계는 멱등이라 같은 대상으로 다시 실행하면 남은 것부터 이어서 정리된다.
    호출자는 ``any(s.is_failed for s in steps)`` 로 성공 여부를 판정한다.
    """
    steps: list[CleanupStep] = []

    def run(name: str, action) -> bool:
        """한 단계 실행. 실패면 steps 에 기록하고 False."""
        try:
            status, detail = action()
        except Exception as exc:  # noqa: BLE001 - 단계별로 실패를 보고해야 한다
            log.exception("KB teardown 단계 실패: kind=%s owner=%s step=%s",
                          plan.kind, plan.owner, name)
            steps.append(CleanupStep(name, STEP_FAILED, str(exc)))
            return False
        steps.append(CleanupStep(name, status, detail))
        return True

    def check_ingestion():
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "KB/DS 없음"
        if is_ingestion_in_progress(plan.kb_id, plan.data_source_id):
            raise RuntimeError("진행 중인 ingestion 이 있습니다. 완료 후 다시 시도해주세요.")
        return STEP_DONE, "진행 중 없음"

    def delete_ds():
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "DS 없음"
        ok = _delete_data_source(plan.kb_id, plan.data_source_id)
        return STEP_DONE, plan.data_source_id if ok else "이미 없음"

    def delete_kb():
        if not plan.kb_id:
            return STEP_SKIPPED, "KB 없음"
        ok = _delete_knowledge_base(plan.kb_id)
        return STEP_DONE, plan.kb_id if ok else "이미 없음"

    def delete_index():
        if not (plan.vector_bucket and plan.vector_index):
            return STEP_SKIPPED, "벡터 버킷 설정 없음"
        ok = _delete_vector_index(plan.vector_bucket, plan.vector_index)
        return STEP_DONE, plan.vector_index if ok else "이미 없음"

    def delete_main():
        if not plan.main_keys:
            return STEP_SKIPPED, "객체 없음"
        n = _delete_keys(plan.main_bucket, plan.main_keys)
        return STEP_DONE, f"{n}개 삭제"

    def delete_intermediate():
        if not plan.intermediate_keys:
            return STEP_SKIPPED, "객체 없음"
        n = _delete_keys(plan.intermediate_bucket, plan.intermediate_keys)
        return STEP_DONE, f"{n}개 삭제"

    def delete_rows():
        if not plan.db_row_count:
            return STEP_SKIPPED, "행 없음"
        n = _delete_db_rows(plan.kind, plan.owner, plan.kb_id)
        return STEP_DONE, f"{n}건 삭제"

    ordered = [
        ("Ingestion 진행 확인", check_ingestion),
        ("Data Source 삭제", delete_ds),
        ("Knowledge Base 삭제", delete_kb),
        ("S3 Vectors 인덱스 삭제", delete_index),
        ("S3 원본/변환본 삭제", delete_main),
        ("S3 중간 산출물 삭제", delete_intermediate),
        ("DB 행 삭제", delete_rows),
    ]

    log.info("KB teardown 시작: kind=%s owner=%s kb_id=%s rows=%d objects=%d",
             plan.kind, plan.owner, plan.kb_id or "-",
             plan.db_row_count, len(plan.main_keys) + len(plan.intermediate_keys))
    for name, action in ordered:
        if not run(name, action):
            return steps
    log.info("KB teardown 완료: kind=%s owner=%s", plan.kind, plan.owner)
    return steps


TOTAL_STEPS = 7   # execute_cleanup 의 단계 수 (CLI/UI 진행 표시용)
