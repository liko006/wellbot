"""Knowledge Base teardown 서비스.

두 가지 비가역 삭제를 담당한다. 운용(업로드·문서 삭제)과 성격이 달라 별도 모듈로 둔다.

1. **개인/팀 KB 전체 삭제** (`gather_resources` / `execute_cleanup`) — 퇴사자 정리,
   팀 해체 등. 한 소유자의 리소스를 의존성 역순으로 전부 지운다.
2. **공용 KB 폴더(대분류=Data Source) 삭제** (`gather_folder_resources` /
   `execute_folder_cleanup`) — 벡터 인덱스가 공유 자산이라 접근이 정반대다.
   파일 하단 섹션 주석 참고.

**호출자는 둘이다** — CLI(``scripts/cleanup_kb.py``)와 admin UI. 그래서
admin 계층이 아니라 도메인 계층에 둔다.

scope (``kind``):
    - ``personal``: owner=emp_no  → users{env}/{emp_no}/
    - ``team``:     owner=dept_cd → teams{env}/{dept_cd}/

삭제 순서 (역순인 이유):
    Data Source → Knowledge Base → S3 Vectors 인덱스 → S3 원본 → S3 중간산출물 → DB 행.
    벡터 스토어 자체는 KB/DS 를 지워도 남으므로 별도로 지운다.

**DS/KB 삭제는 비동기다** — DeleteDataSource·DeleteKnowledgeBase 는 202(접수)만
돌려주고 실제 정리는 백그라운드에서 진행된다. 접수 응답을 성공으로 보고 바로
다음 단계로 넘어가면, 아직 도는 정리 작업이 쓰려던 벡터 인덱스를 우리가 먼저
지워버려 ``DELETE_UNSUCCESSFUL`` 로 끝난다(로그상으로는 전부 성공으로 보였다).
그래서 두 가지를 한다.

1. DS 삭제 **전에 dataDeletionPolicy 를 RETAIN 으로** 바꾼다. teardown 은 인덱스를
   통째로 지우므로 DS 가 벡터를 한 건씩 정리하는 건 낭비이자 유일한 실패 지점이다.
   (공용 KB 폴더 삭제는 인덱스가 공유라 정반대 — 거긴 벡터를 실제로 빼야 한다.)
2. 다음 단계로 넘어가기 전에 **리소스가 실제로 사라질 때까지 기다린다**.
   ``DELETE_UNSUCCESSFUL`` 이면 failureReasons 를 그대로 실패 사유로 올린다.

멱등:
    DB 행이 이미 없어도 이름으로 Bedrock/S3 를 조회해 남은 리소스를 정리한다.
    각 삭제는 NotFound 를 "이미 없음"으로 흡수하고, 어느 단계에서 실패하면 그
    지점에서 멈춘다 — 같은 대상으로 다시 실행하면 남은 것부터 이어서 정리된다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
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
    shared_base,
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

# 비동기 삭제(202) 완료 대기. RETAIN 으로 바꿔 벡터 정리를 건너뛰므로 보통 수 초에 끝난다.
DELETE_POLL_INTERVAL_SEC = 3
DELETE_POLL_TIMEOUT_SEC = 300


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


def _set_retain_policy(kb_id: str, data_source_id: str) -> bool:
    """DS 의 dataDeletionPolicy 를 RETAIN 으로 변경. 반환: 실제로 바꿨으면 True.

    teardown 은 벡터 인덱스를 통째로 지우므로 DS 가 벡터를 개별 정리할 필요가 없다.
    기본값 ``Delete`` 로 두면 그 백그라운드 정리가 우리가 방금 지운 인덱스를 찾다가
    DELETE_UNSUCCESSFUL 로 끝난다.

    현재 설정을 읽어 정책만 바꿔 되쓴다(설정을 재구성하지 않아야 DS 를 망가뜨리지
    않는다). 실패해도 삭제 자체는 시도하므로 best-effort 로 경고만 남긴다.
    """
    client = get_bedrock_agent()
    try:
        current = client.get_data_source(
            knowledgeBaseId=kb_id, dataSourceId=data_source_id,
        )["dataSource"]
        if current.get("dataDeletionPolicy") == "RETAIN":
            return False
        payload = {
            "knowledgeBaseId": kb_id,
            "dataSourceId": data_source_id,
            "name": current["name"],
            "dataSourceConfiguration": current["dataSourceConfiguration"],
            "dataDeletionPolicy": "RETAIN",
        }
        for optional in ("description", "vectorIngestionConfiguration"):
            if current.get(optional):
                payload[optional] = current[optional]
        client.update_data_source(**payload)
        return True
    except ClientError:
        log.warning(
            "dataDeletionPolicy RETAIN 전환 실패 (삭제는 계속 시도): kb_id=%s ds_id=%s",
            kb_id, data_source_id, exc_info=True,
        )
        return False


def _wait_until_gone(describe, label: str) -> None:
    """리소스가 사라질 때까지 폴링. DELETE_UNSUCCESSFUL 이면 사유와 함께 예외.

    describe() 는 리소스 dict 를 반환하거나 ResourceNotFoundException 을 던진다.
    DS/KB 삭제는 202(접수)만 돌려주므로, 이 대기 없이 다음 단계로 가면 아직 도는
    정리 작업의 대상(벡터 인덱스·S3)을 우리가 먼저 지워 실패시킨다.
    """
    deadline = time.monotonic() + DELETE_POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        try:
            resource = describe()
        except ClientError as e:
            if _absorb_not_found(e, ("ResourceNotFoundException",)):
                return
            raise
        status = resource.get("status", "")
        if status == "DELETE_UNSUCCESSFUL":
            reasons = "; ".join(resource.get("failureReasons", []) or [])
            raise RuntimeError(f"{label} 삭제 실패(DELETE_UNSUCCESSFUL): {reasons or '원인 미상'}")
        time.sleep(DELETE_POLL_INTERVAL_SEC)
    raise TimeoutError(f"{label} 삭제 완료 대기 타임아웃 ({DELETE_POLL_TIMEOUT_SEC}초)")


def _delete_data_source(
    kb_id: str, data_source_id: str, *, retain_vectors: bool,
) -> bool:
    """Data Source 삭제 후 실제로 사라질 때까지 대기. 반환: 이미 없었으면 False.

    retain_vectors 는 호출자가 **반드시 명시**한다(키워드 전용, 기본값 없음) —
    두 경로의 정답이 정반대이고, 잘못 고르면 조용히 나쁜 결과가 남는다.

    - True (개인/팀 teardown): 인덱스를 통째로 지우므로 DS 의 개별 벡터 정리는
      낭비이자 유일한 실패 지점이다.
    - False (공용 폴더 삭제): 인덱스를 여러 폴더가 공유한다. RETAIN 으로 두면 앞
      단계의 동기화가 미처 못 지운 벡터가 인덱스에 영구히 남아 **다른 폴더 질의의
      검색 결과를 오염시킨다**. 기본 정책(Delete)이 마지막 안전망이다.
    """
    client = get_bedrock_agent()
    if retain_vectors:
        _set_retain_policy(kb_id, data_source_id)
    try:
        client.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=data_source_id)
    except ClientError as e:
        if _absorb_not_found(e, ("ResourceNotFoundException", "ValidationException")):
            return False
        raise
    _wait_until_gone(
        lambda: client.get_data_source(
            knowledgeBaseId=kb_id, dataSourceId=data_source_id,
        )["dataSource"],
        "Data Source",
    )
    return True


def _delete_knowledge_base(kb_id: str) -> bool:
    """Knowledge Base 삭제 후 실제로 사라질 때까지 대기. 반환: 이미 없었으면 False."""
    client = get_bedrock_agent()
    try:
        client.delete_knowledge_base(knowledgeBaseId=kb_id)
    except ClientError as e:
        if _absorb_not_found(e, ("ResourceNotFoundException", "ValidationException")):
            return False
        raise
    _wait_until_gone(
        lambda: client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"],
        "Knowledge Base",
    )
    return True


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
def execute_cleanup(plan: CleanupPlan) -> Iterator[CleanupStep]:
    """의존성 역순으로 삭제. **단계가 끝날 때마다 결과를 하나씩 내보낸다.**

    제너레이터인 이유: DS/KB 삭제 완료 대기(§모듈 docstring)로 한 단계가 수십 초를
    쓸 수 있어, 호출자가 진행 상황을 그때그때 보여줄 수 있어야 한다. CLI 는 받는 즉시
    찍고, Reflex 이벤트는 사이사이 화면을 갱신한다.

    한 단계가 실패하면 그 단계를 마지막으로 내보내고 **멈춘다**(뒤 단계는 실행하지
    않는다 — 아직 도는 삭제 작업의 대상을 우리가 먼저 지우면 안 된다). 각 단계는
    멱등이라 같은 대상으로 다시 실행하면 남은 것부터 이어서 정리된다.

    전부 모아 받으려면 ``steps = list(execute_cleanup(plan))``.
    """
    def check_ingestion():
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "KB/DS 없음"
        if is_ingestion_in_progress(plan.kb_id, plan.data_source_id):
            raise RuntimeError("진행 중인 ingestion 이 있습니다. 완료 후 다시 시도해주세요.")
        return STEP_DONE, "진행 중 없음"

    def delete_ds():
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "DS 없음"
        ok = _delete_data_source(
            plan.kb_id, plan.data_source_id, retain_vectors=True,
        )
        if not ok:
            return STEP_DONE, "이미 없음"
        return STEP_DONE, f"{plan.data_source_id} (벡터는 인덱스 삭제로 일괄 제거)"

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
        try:
            status, detail = action()
        except Exception as exc:  # noqa: BLE001 - 단계별로 실패를 보고해야 한다
            log.exception("KB teardown 단계 실패: kind=%s owner=%s step=%s",
                          plan.kind, plan.owner, name)
            yield CleanupStep(name, STEP_FAILED, str(exc))
            return
        yield CleanupStep(name, status, detail)
    log.info("KB teardown 완료: kind=%s owner=%s", plan.kind, plan.owner)


TOTAL_STEPS = 7   # execute_cleanup 의 단계 수 (CLI/UI 진행 표시용)


# ──────────────────────────────────────────────
# 공용(shared) KB 폴더 삭제 — 대분류 = Data Source 하나
# ──────────────────────────────────────────────
# 개인/팀 teardown 과 결정적으로 다른 점: **벡터 인덱스가 여러 폴더의 공유 자산**이라
# 인덱스를 지울 수 없다. 그래서 지우는 대상이 KB 가 아니라 "이 폴더의 벡터"이고,
# 그걸 빼내는 유일한 수단이 S3 를 비운 뒤 **재색인(증분 동기화)** 이다.
#
# 순서가 곧 정확성이다 — 앞 단계가 끝나기 전에 뒤 단계를 시작하면 안 된다.
#   1. S3 객체를 지워야 재색인이 "없어진 문서"로 인식한다.
#   2. 재색인이 **완료**돼야 벡터가 실제로 빠진다.
#   3. 그 다음에야 DS 를 지운다. 순서를 뒤집으면 DS 가 없어 재색인을 돌릴 수 없고,
#      벡터는 공유 인덱스에 영구히 남는다.
#   4. yaml 레지스트리는 마지막 — 먼저 지우면 ds_id 를 잃어 재시도가 불가능해진다.
# 각 단계 함수는 효과가 확정된 뒤에만 반환하고(동기 삭제·폴링 대기), 한 단계라도
# 실패하면 제너레이터가 그 지점에서 멈춘다.

# 폴더 삭제용 재색인 대기 상한. 이 시점의 대상 prefix 는 비어 있어 금방 끝나야 하므로,
# 설정값(공용 KB 대용량 가정 30분)보다 짧게 잡는다 — 오래 걸리면 정상이 아니다.
FOLDER_SYNC_TIMEOUT_SEC = 900

FOLDER_TOTAL_STEPS = 6


@dataclass(frozen=True)
class FolderCleanupPlan:
    """공용 KB 대분류 삭제 대상 스냅샷 (미리보기 겸 실행 입력)."""

    top: str
    kb_id: str = ""
    data_source_id: str = ""
    bucket: str = ""
    prefix: str = ""
    keys: list[str] = field(default_factory=list)
    intermediate_bucket: str = ""
    intermediate_prefix: str = ""
    intermediate_keys: list[str] = field(default_factory=list)
    doc_keys: list[str] = field(default_factory=list)

    @property
    def has_nothing_to_delete(self) -> bool:
        return not (
            self.data_source_id or self.keys or self.intermediate_keys or self.doc_keys
        )


def gather_folder_resources(top: str) -> FolderCleanupPlan:
    """공용 KB 대분류의 삭제 대상을 수집. 읽기만 하므로 미리보기에 그대로 쓴다."""
    from wellbot.services.knowledgebase import shared_kb_docs, shared_kb_service

    name = (top or "").strip().strip("/")
    if not name:
        raise ValueError("대분류 이름이 비어 있습니다.")
    if "/" in name:
        raise ValueError("삭제는 대분류 단위입니다. 소분류는 문서 삭제로 정리하세요.")

    cfg = get_kb_config()["shared_kb"]
    bucket = cfg.get("s3_bucket", "")
    intermediate_bucket = cfg.get("s3_intermediate_bucket", "")
    prefix = f"{shared_base()}/{name}/"
    intermediate_prefix = shared_kb_service.processed_prefix(name)

    return FolderCleanupPlan(
        top=name,
        kb_id=cfg.get("kb_id", ""),
        data_source_id=shared_kb_service.list_folders().get(name, ""),
        bucket=bucket,
        prefix=prefix,
        keys=_list_keys(bucket, prefix),
        intermediate_bucket=intermediate_bucket,
        intermediate_prefix=intermediate_prefix,
        intermediate_keys=_list_keys(intermediate_bucket, intermediate_prefix),
        doc_keys=sorted(
            key for key in shared_kb_docs.list_doc_attrs() if key.startswith(f"{name}/")
        ),
    )


def execute_folder_cleanup(plan: FolderCleanupPlan) -> Iterator[CleanupStep]:
    """공용 KB 대분류를 삭제. 단계가 끝날 때마다 결과를 하나씩 내보낸다.

    단계 순서와 그 이유는 위 섹션 주석 참고. 한 단계가 실패하면 그 단계를 마지막으로
    내보내고 멈춘다 — 특히 재색인이 완료되지 않은 상태로 DS 를 지우면 공유 인덱스에
    고아 벡터가 남아 되돌릴 방법이 없다.
    """
    from wellbot.services.knowledgebase import shared_kb_docs, shared_kb_service

    def check_ingestion():
        """남의 job 이 돌고 있으면 시작하지 않는다 — start_ingestion 이 거부되고,
        그 job 의 결과를 우리 동기화로 오해할 수도 있다."""
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "Data Source 없음"
        if is_ingestion_in_progress(plan.kb_id, plan.data_source_id):
            raise RuntimeError("진행 중인 ingestion 이 있습니다. 완료 후 다시 시도해주세요.")
        return STEP_DONE, "진행 중 없음"

    def delete_objects():
        """S3 delete_objects 는 동기 — 반환 시점에 삭제가 확정된다."""
        if not plan.keys:
            return STEP_SKIPPED, "객체 없음"
        count = _delete_keys(plan.bucket, plan.keys)
        return STEP_DONE, f"{count}개 삭제"

    def sync_vectors():
        """재색인으로 벡터 제거. **완료까지 기다리고, COMPLETE 가 아니면 실패로 멈춘다.**

        객체가 없어도 DS 가 있으면 돌린다 — 인덱스에 남은 벡터가 있는지는 조회로
        확인할 수 없으므로, 건너뛰면 고아를 남길 수 있다(빈 prefix 동기화는 저렴하다).
        """
        if not plan.data_source_id:
            return STEP_SKIPPED, "Data Source 없음"
        job_id = shared_kb_service.start_ingestion(plan.top)
        status = shared_kb_service.poll_ingestion_status(
            plan.top, job_id, poll_timeout=FOLDER_SYNC_TIMEOUT_SEC,
        )
        if status != "COMPLETE":
            raise RuntimeError(
                f"벡터 정리 동기화가 완료되지 않았습니다(status={status}). "
                "Data Source 를 남겨 두었으니 원인 확인 후 다시 실행하세요."
            )
        return STEP_DONE, f"job={job_id}"

    def delete_ds():
        """벡터가 빠진 뒤에 DS 삭제. RETAIN 을 쓰지 않는 이유는 _delete_data_source 참고."""
        if not (plan.kb_id and plan.data_source_id):
            return STEP_SKIPPED, "Data Source 없음"
        ok = _delete_data_source(plan.kb_id, plan.data_source_id, retain_vectors=False)
        return STEP_DONE, plan.data_source_id if ok else "이미 없음"

    def delete_intermediate():
        """Lambda 변환 중간산출물. DS 가 사라진 뒤라 아무도 쓰지 않는다."""
        if not plan.intermediate_keys:
            return STEP_SKIPPED, "객체 없음"
        count = _delete_keys(plan.intermediate_bucket, plan.intermediate_keys)
        return STEP_DONE, f"{count}개 삭제"

    def clear_registry():
        removed = shared_kb_docs.remove_docs_under(plan.top)
        unregistered = shared_kb_service.unregister_folder(plan.top)
        if not (removed or unregistered):
            return STEP_SKIPPED, "등록 정보 없음"
        folder_part = "폴더 등록 해제" if unregistered else "폴더 등록 없음"
        return STEP_DONE, f"{folder_part} · 문서 속성 {removed}건 제거"

    ordered = [
        ("Ingestion 진행 확인", check_ingestion),
        ("S3 문서 삭제", delete_objects),
        ("재색인으로 벡터 정리", sync_vectors),
        ("Data Source 삭제", delete_ds),
        ("S3 중간 산출물 삭제", delete_intermediate),
        ("설정 레지스트리 정리", clear_registry),
    ]

    log.info(
        "공용 KB 폴더 삭제 시작: top=%s ds_id=%s objects=%d docs=%d",
        plan.top, plan.data_source_id or "-", len(plan.keys), len(plan.doc_keys),
    )
    for name, action in ordered:
        try:
            status, detail = action()
        except Exception as exc:  # noqa: BLE001 - 단계별로 실패를 보고해야 한다
            log.exception("공용 KB 폴더 삭제 단계 실패: top=%s step=%s", plan.top, name)
            yield CleanupStep(name, STEP_FAILED, str(exc))
            return
        yield CleanupStep(name, status, detail)
    log.info("공용 KB 폴더 삭제 완료: top=%s", plan.top)
