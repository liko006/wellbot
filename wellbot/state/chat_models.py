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


class ChatModeInfo(BaseModel):
    """프론트엔드 표시용 채팅 모드 정보"""

    id: str
    name: str
    description: str = ""
    icon: str = "message-circle"


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


# ── KB (Knowledge Base) 표시용 모델 ──
class PendingFile(BaseModel):
    """KB 업로드 대기 파일 정보."""

    name: str
    size: int
    size_display: str


class KbSharedFile(BaseModel):
    """회사 KB 문서 목록 표시용 파일 정보 (폴더 안에 들어감)."""

    file_name: str
    uploaded_at: str
    expires_at: str


class KbSharedSubfolder(BaseModel):
    """회사 KB 대분류 아래 소분류 단위 그룹.

    sub_name 이 빈 문자열이면 대분류 raw/ 바로 밑(소분류 없는) 파일 묶음.
    """

    sub_name: str = ""
    files: list[KbSharedFile] = []


class KbSharedFolder(BaseModel):
    """회사 KB 대분류 단위 그룹. 소분류(subfolders)로 2단계 트리 구성."""

    folder_type: str
    subfolders: list[KbSharedSubfolder] = []


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
