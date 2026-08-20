"""ChatState 가 사용하는 프론트엔드 데이터 모델 및 모듈 헬퍼.

ChatState 본체에서 분리해 컴포넌트가 State 모듈을 import 하지 않고도
타입 사용 가능. 데이터 클래스는 모두 pydantic.BaseModel 기반이며
Reflex State var 의 타입으로 사용.
"""

from __future__ import annotations

import time
import uuid

from pydantic import BaseModel

from wellbot.constants import DEFAULT_CONVERSATION_TITLE


class ModelInfo(BaseModel):
    """프론트엔드 표시용 모델 정보"""

    name: str
    description: str
    supports_thinking: bool


class PromptInfo(BaseModel):
    """프론트엔드 표시용 프롬프트 템플릿 정보"""

    name: str
    content: str
    description: str = ""


class AttachmentInfo(BaseModel):
    """프론트엔드 표시용 첨부파일 정보"""

    file_no: int
    name: str
    mime: str = ""
    size_bytes: int = 0
    token_count: int = 0
    status: str = "processing"  # "processing" | "ready" | "failed"


class TurnInfo(BaseModel):
    """턴 네비게이터(우측 레일) 항목 — 질문 1개 = 틱 1개.

    index 는 **로드된 질문 중 몇 번째인가**(0-based)이며, DOM 에서
    `.chat-msg[data-role="user"]` 를 순서대로 세었을 때의 위치와 일치한다.
    JS 는 이 값으로 틱 ↔ 메시지를 연결한다.
    """

    index: int
    text: str  # 질문 원문(길이 상한 적용). 시각적 말줄임은 CSS 가 담당


class Message(BaseModel):
    """개별 메시지 모델"""

    role: str  # "user" | "assistant"
    content: str
    timestamp: float
    model_name: str = ""
    seq: int = 0
    attachments: list[AttachmentInfo] = []
    # KB 검색 출처 문서 (assistant 메시지에만). 항목: {title, source_uri, source, ext, ranks, score}
    source_docs: list[dict] = []


class Conversation(BaseModel):
    """대화 세션 모델"""

    id: str
    title: str
    messages: list[Message]
    created_at: float
    model_name: str = ""
    is_loaded: bool = False      # 메시지가 DB 에서 로드되었는지
    is_persisted: bool = False   # DB 에 저장된 대화인지
    has_more_older: bool = False # 로드되지 않은 더 오래된 메시지가 DB 에 남아있는지


# ── KB (Knowledge Base) 표시용 모델 ──
class PendingFile(BaseModel):
    """KB 업로드 대기 파일 정보."""

    name: str
    size: int
    size_display: str


class KbTreeRow(BaseModel):
    """회사(공용) KB 문서 목록 트리의 **평탄화된 한 행** (폴더 또는 파일). N단계 지원.

    Reflex `rx.foreach` 는 임의 깊이 재귀 불가 → 트리를 depth 붙은 평탄 행 리스트로 만들어
    단일 foreach + depth 들여쓰기로 렌더한다(깊이 무관). 가시성은 조상 폴더가 모두 펼쳐졌는지로
    ChatState.visible_shared_rows 가 계산.

    - depth: 조상 폴더 수 (0=최상위 대분류). indent(=padding_left)는 depth 로 미리 계산.
    - path: 대분류부터의 전체 논리 경로 (예: "사규", "사규/인사", "사규/인사/취업규칙.pdf").
            폴더 펼침 키(expanded_kb_folders)이자 조상 판정 기준.
    - name: 마지막 세그먼트(표시명). is_folder: 폴더 여부.
    - uploaded_at/expires_at: 파일 행에서만 유효.
    """

    depth: int = 0
    path: str = ""
    name: str = ""
    is_folder: bool = True
    indent: str = "0em"
    uploaded_at: str = ""
    expires_at: str = ""


_MIME_LABELS: dict[str, str] = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint",
    "application/x-hwp": "한글",
    "application/x-hwpx": "한글",
    "text/plain": "텍스트",
    "text/markdown": "Markdown",
    "image/png": "PNG 이미지",
    "image/jpeg": "JPEG 이미지",
    "image/webp": "WebP 이미지",
    "image/gif": "GIF 이미지",
}


def mime_to_label(mime: str) -> str:
    """MIME 타입을 한국어 라벨로 변환"""
    return _MIME_LABELS.get(mime, mime or "파일")


def new_conversation() -> Conversation:
    """빈 대화 생성"""
    now = time.time()
    return Conversation(
        id=str(uuid.uuid4()),
        title=DEFAULT_CONVERSATION_TITLE,
        messages=[],
        created_at=now,
        is_loaded=True,
        is_persisted=False,
    )


def _from_db_row(row: dict, existing: Conversation | None) -> Conversation:
    """DB 목록 행을 Conversation 으로. 이미 있던 대화면 로드된 본문을 살린다."""
    if existing is None:
        return Conversation(
            id=row["id"],
            title=row["title"],
            messages=[],
            created_at=row["created_at"],
            model_name=row.get("model_name", ""),
            is_loaded=False,
            is_persisted=True,
        )
    return Conversation(
        id=existing.id,
        title=row["title"],                                    # 제목은 DB 가 최신
        messages=existing.messages,                            # 이미 받아온 본문 보존
        created_at=existing.created_at,
        model_name=row.get("model_name", "") or existing.model_name,
        is_loaded=existing.is_loaded,
        is_persisted=True,
        has_more_older=existing.has_more_older,
    )


def merge_conversations(
    existing: list[Conversation],
    rows: list[dict],
    current_id: str,
) -> tuple[list[Conversation], str]:
    """DB 대화 목록과 현재 화면 상태를 병합. 반환: (새 목록, 현재 대화 id).

    사이드바는 페이지에 들어올 때마다 DB 를 다시 읽는다. 그냥 덮어쓰면 세 가지가
    깨지므로 병합한다.

    - **이미 불러온 본문**: 같은 id 는 기존 객체를 재사용한다. 매번 새로 만들면
      대화를 열어둔 채 페이지를 옮길 때마다 메시지를 다시 받아야 한다.
    - **아직 저장되지 않은 대화**: 작성 중인 새 대화는 DB 에 없으므로 목록 앞에 남긴다.
    - **현재 보고 있는 대화**: 목록에 남아 있으면 그대로 둔다. 무조건 새 대화로
      옮기면 페이지를 이동할 때마다 보던 대화에서 튕긴다.

    현재 대화가 목록에서 사라졌을 때만(삭제됐거나 사용자가 바뀐 경우) 빈 대화로
    옮기고, 쓸 만한 빈 대화가 없으면 새로 만든다.
    """
    by_id = {c.id: c for c in existing}
    merged = [c for c in existing if not c.is_persisted]
    merged += [_from_db_row(row, by_id.get(row["id"])) for row in rows]

    if any(c.id == current_id for c in merged):
        return merged, current_id

    empty = next((c for c in merged if not c.is_persisted and not c.messages), None)
    if empty is None:
        empty = new_conversation()
        merged = [empty, *merged]
    return merged, empty.id
