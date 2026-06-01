"""대화 상태 관리 - ChatState.

메시지 전송, 대화 생성/전환/삭제, Bedrock 스트리밍 응답 처리 담당.
DB 연동으로 대화 이력 영속화 보장.
"""

import asyncio
import logging
import random
import time
import uuid

import reflex as rx

from wellbot.logger import log_context

from wellbot.constants import (
    DEFAULT_CONVERSATION_TITLE,
    FILE_MAX_PER_MESSAGE,
    FILE_MAX_SIZE_MB,
    FILE_PARSER_MODE,
    LOCAL_SUPPORTED_EXTS,
    TITLE_MAX_LENGTH,
    TOOL_USE_MAX_ITERATIONS,
    UPSTAGE_SUPPORTED_EXTS,
)
from wellbot.services.ai.bedrock import (
    astream_chat,
    astream_chat_with_tools,
    generate_title,
)
from wellbot.services.chat import chat_service, response_filter, tool_executor
from wellbot.services.core.settings import get_config, get_greetings
from wellbot.services.files import attachment_service, file_parser
from wellbot.state.chat_helpers.attachments import (
    collect_image_blocks,
    fetch_pending_attachments,
    rows_to_attachment_infos,
)
from wellbot.state.chat_helpers.download_script import build_download_script
from wellbot.state.chat_helpers.system_prompt import augment_system_with_attachments
from wellbot.state.chat_helpers.upload_script import build_upload_script
from wellbot.state.chat_models import (
    ChatModeInfo,
    AttachmentInfo,
    Conversation,
    Message,
    ModelInfo,
    PromptInfo,
    new_conversation,
)

log = logging.getLogger(__name__)


class ChatState(rx.State):
    """채팅 관련 상태 관리"""

    conversations: list[Conversation] = []
    current_conversation_id: str = ""
    current_input: str = ""
    is_loading: bool = False
    is_thinking: bool = False
    streaming_content: str = ""
    selected_model: str = ""
    thinking_enabled: bool = False
    selected_prompt: str = "default"
    show_style_panel: bool = False
    greeting_text: str = ""

    # ── 채팅 모드 ──
    selected_chat_mode: str = "chat"

    # ── 대화 검색 ──
    search_query: str = ""

    # ── 첨부파일 ──
    pending_attachments: list[AttachmentInfo] = []
    attachment_error: str = ""
    conversation_attachments: list[AttachmentInfo] = []

    # ── 생성 중지 ──
    _cancel_requested: bool = False

    # on_load 시점에 캐시된 인증 사용자 사번
    _emp_no: str = ""

    # 첨부파일-메시지 매핑용 msg_id. trigger_upload 에서 생성 후 send_message 에서 재사용
    _pending_msg_id: str = ""

    def _refresh_greeting(self) -> None:
        """환영 메시지를 랜덤으로 갱신"""
        self.greeting_text = random.choice(get_greetings())

    def _ensure_conversation(self) -> None:
        """대화가 없으면 새로 생성"""
        if not self.conversations:
            conv = new_conversation()
            self.conversations = [conv]
            self.current_conversation_id = conv.id

    def _get_current_index(self) -> int | None:
        """현재 대화의 인덱스 반환. 없으면 자동 복구 시도"""
        for i, conv in enumerate(self.conversations):
            if conv.id == self.current_conversation_id:
                return i
        # current_id 가 conversations 에 없는 경우 자동 복구
        if self.conversations:
            self.current_conversation_id = self.conversations[0].id
            return 0
        self._ensure_conversation()
        return 0 if self.conversations else None

    def _update_conversation(self, idx: int, **kwargs: object) -> None:
        """지정 인덱스 대화를 불변 방식으로 갱신"""
        conv = self.conversations[idx]
        updated = Conversation(
            id=conv.id,
            title=kwargs.get("title", conv.title),  # type: ignore[arg-type]
            messages=kwargs.get("messages", conv.messages),  # type: ignore[arg-type]
            created_at=conv.created_at,
            model_name=kwargs.get("model_name", conv.model_name),  # type: ignore[arg-type]
            is_loaded=kwargs.get("is_loaded", conv.is_loaded),  # type: ignore[arg-type]
            is_persisted=kwargs.get("is_persisted", conv.is_persisted),  # type: ignore[arg-type]
        )
        self.conversations = [
            updated if c.id == updated.id else c for c in self.conversations
        ]

    def _load_messages_for(self, idx: int) -> None:
        """DB 에서 대화 메시지 로드. 이미 로드된 경우 첨부파일 목록만 갱신"""
        conv = self.conversations[idx]
        if conv.is_loaded:
            self._load_conversation_attachments(conv.id)
            return
        msgs = chat_service.get_conversation_messages(conv.id, self._emp_no)
        loaded = [
            Message(
                role=m["role"],
                content=m["content"],
                timestamp=m["timestamp"],
                model_name=m.get("model_name", ""),
                seq=m.get("seq", 0),
            )
            for m in msgs
        ]
        self._update_conversation(idx, messages=loaded, is_loaded=True)
        self._load_conversation_attachments(conv.id)

    def _load_conversation_attachments(self, conv_id: str) -> None:
        """DB 에서 대화 첨부파일 목록 로드"""
        try:
            rows = attachment_service.get_conversation_attachments(conv_id)
            self.conversation_attachments = rows_to_attachment_infos(rows)
        except Exception:
            self.conversation_attachments = []

    # ── Computed vars ──

    @rx.var
    def current_messages(self) -> list[Message]:
        """현재 대화의 메시지 목록"""
        idx = self._get_current_index()
        if idx is None:
            return []
        return self.conversations[idx].messages

    @rx.var
    def has_messages(self) -> bool:
        """현재 대화에 메시지가 하나 이상 존재하는지 여부"""
        return len(self.current_messages) > 0

    @rx.var
    def current_title(self) -> str:
        """현재 대화의 제목"""
        idx = self._get_current_index()
        if idx is None:
            return ""
        return self.conversations[idx].title

    @rx.var
    def chat_mode_list(self) -> list[ChatModeInfo]:
        """설정에서 읽은 사용 가능한 채팅 모드 목록"""
        try:
            cfg = get_config()
            return [
                ChatModeInfo(
                    id=a.id, name=a.name,
                    description=a.description, icon=a.icon,
                )
                for a in cfg.chat_modes
            ]
        except Exception:
            log.warning("채팅 모드 목록 로드 실패", exc_info=True)
            return []

    @rx.var
    def current_chat_mode_name(self) -> str:
        """현재 선택된 채팅 모드의 표시 이름"""
        try:
            cfg = get_config()
            mode = cfg.get_chat_mode(self.selected_chat_mode)
            return mode.name if mode else "기본 대화"
        except Exception:
            return "기본 대화"

    @rx.var
    def current_chat_mode_icon(self) -> str:
        """현재 선택된 채팅 모드의 아이콘 식별자"""
        try:
            cfg = get_config()
            mode = cfg.get_chat_mode(self.selected_chat_mode)
            return mode.icon if mode else "message-circle"
        except Exception:
            return "message-circle"

    @rx.var
    def has_conversation_attachments(self) -> bool:
        """현재 대화에 전송 완료된 첨부파일이 하나 이상 존재하는지 여부"""
        return len(self.conversation_attachments) > 0

    @rx.var
    def conversation_attachment_count(self) -> int:
        """현재 대화의 전송 완료 첨부파일 수"""
        return len(self.conversation_attachments)

    @rx.var
    def has_processing_attachments(self) -> bool:
        """pending 목록 중 아직 처리 중인 첨부파일이 있는지 여부"""
        return any(a.status == "processing" for a in self.pending_attachments)

    @rx.var
    def can_send(self) -> bool:
        """현재 입력 상태가 전송 가능한지 여부.

        처리 중인 첨부파일이 있거나 로딩 중이면 전송 차단.
        """
        if self._get_current_index() is None:
            return False
        if self.is_loading:
            return False
        if self.has_processing_attachments:
            return False
        return self.current_input.strip() != ""

    @rx.var
    def sorted_conversations(self) -> list[Conversation]:
        """시간 역순으로 정렬된 대화 목록.

        빈 미저장 대화는 숨기되, 현재 활성 대화는 항상 표시.
        검색어가 있으면 제목 부분 일치(대소문자 구분 없음)로 필터링.
        """
        current_id = self.current_conversation_id
        visible = [
            c for c in self.conversations
            if c.is_persisted or c.messages or c.id == current_id
        ]
        query = self.search_query.strip().lower()
        if query:
            visible = [c for c in visible if query in (c.title or "").lower()]
        return sorted(visible, key=lambda c: c.created_at, reverse=True)

    @rx.var
    def is_searching(self) -> bool:
        """검색어가 입력된 상태인지 여부"""
        return self.search_query.strip() != ""

    @rx.var
    def has_search_results(self) -> bool:
        """검색 결과가 하나 이상 존재하는지 여부"""
        return len(self.sorted_conversations) > 0

    @rx.var
    def model_names(self) -> list[str]:
        """설정에서 읽은 사용 가능한 모델 이름 목록"""
        try:
            return get_config().model_names
        except Exception:
            log.warning("모델 이름 목록 로드 실패", exc_info=True)
            return []

    @rx.var
    def model_list(self) -> list[ModelInfo]:
        """팝오버 표시용 모델 목록"""
        try:
            cfg = get_config()
            return [
                ModelInfo(
                    name=m.name,
                    description=m.description,
                    supports_thinking=m.thinking,
                )
                for m in cfg.models
            ]
        except Exception:
            log.warning("모델 목록 로드 실패", exc_info=True)
            return []

    @rx.var
    def trigger_label(self) -> str:
        """모델 선택 트리거 버튼 라벨"""
        label = self.selected_model
        if self.thinking_enabled and self.model_supports_thinking:
            label += " 확장"
        return label

    @rx.var
    def has_streaming(self) -> bool:
        """응답 스트리밍이 진행 중이며 표시할 텍스트가 있는지 여부"""
        return self.is_loading and bool(self.streaming_content) and not self.is_thinking

    @rx.var
    def model_supports_thinking(self) -> bool:
        """현재 선택된 모델이 extended thinking 을 지원하는지 여부"""
        try:
            cfg = get_config()
            model = cfg.get_model(self.selected_model)
            return model.thinking if model else False
        except Exception:
            return False

    @rx.var
    def prompt_list(self) -> list[PromptInfo]:
        """설정에서 읽은 사용 가능한 프롬프트 템플릿 목록"""
        try:
            cfg = get_config()
            return [
                PromptInfo(name=p.name, content=p.content, description=p.description)
                for p in cfg.prompts
            ]
        except Exception as exc:
            log.exception("프롬프트 로드 실패: %s", exc)
            return []

    @rx.var
    def current_system_prompt(self) -> str:
        """현재 선택된 시스템 프롬프트 내용"""
        try:
            cfg = get_config()
            p = cfg.get_prompt(self.selected_prompt)
            return p.content if p else cfg.system_prompt
        except Exception:
            return ""

    # ── Event handlers ──

    def set_chat_mode(self, mode_id: str) -> None:
        """채팅 모드 변경"""
        self.selected_chat_mode = mode_id

    def stop_generation(self) -> None:
        """생성 중지 요청. 로딩 중이 아니면 무시"""
        if not self.is_loading:
            return
        self._cancel_requested = True

    async def on_load(self) -> None:
        """페이지 로드 시 초기화.

        AuthState 에서 사번 취득 후 모델·프롬프트 기본값 설정,
        DB 에서 대화 목록 로드. 이미 로드된 경우 재조회 생략.
        """
        # AuthState 에서 emp_no 취득
        from wellbot.state.auth_state import AuthState
        auth = await self.get_state(AuthState)
        self._emp_no = auth.current_emp_no


        # 모델/프롬프트 초기화
        try:
            cfg = get_config()
            if not self.selected_model:
                self.selected_model = cfg.default_model.name
            if self.selected_prompt == "default":
                for p in cfg.prompts:
                    if p.content == cfg.system_prompt:
                        self.selected_prompt = p.name
                        break
        except Exception:
            log.debug("기본 모델/프롬프트 선택값 초기화 실패", exc_info=True)

        # 환영 메시지 초기화
        self._refresh_greeting()

        # 이미 DB 대화를 로드한 상태면 재조회 생략
        has_db_conversations = any(c.is_persisted for c in self.conversations)
        if has_db_conversations:
            return

        # DB 에서 대화 목록 로드
        if self._emp_no:
            convs = chat_service.list_conversations(self._emp_no)
            db_conversations = [
                Conversation(
                    id=c["id"],
                    title=c["title"],
                    messages=[],
                    created_at=c["created_at"],
                    model_name=c.get("model_name", ""),
                    is_loaded=False,
                    is_persisted=True,
                )
                for c in convs
            ]
            if db_conversations:
                # 기존 빈 미저장 대화가 있으면 재사용, 없으면 새로 생성
                existing_new = next(
                    (c for c in self.conversations if not c.is_persisted and not c.messages),
                    None,
                )
                new_conv = existing_new or new_conversation()
                self.conversations = [new_conv, *db_conversations]
                self.current_conversation_id = new_conv.id
            else:
                self._ensure_conversation()
        else:
            self._ensure_conversation()

    def set_model(self, name: str) -> None:
        """사용 모델 변경. thinking 은 모델 변경 시 비활성화"""
        self.selected_model = name
        self.thinking_enabled = False

    def toggle_thinking(self, checked: bool) -> None:
        """extended thinking 활성화/비활성화 토글"""
        self.thinking_enabled = checked

    def toggle_style_panel(self) -> None:
        """스타일 패널 표시/숨김 토글"""
        self.show_style_panel = not self.show_style_panel

    def select_prompt(self, name: str) -> None:
        """시스템 프롬프트 템플릿 선택 후 패널 닫기.

        프롬프트가 실제로 변경됐고 대화가 DB 에 저장된 경우 system 메시지 기록.
        """
        prev_prompt = self.selected_prompt
        self.selected_prompt = name
        self.show_style_panel = False

        # 프롬프트가 변경됐고 대화가 저장된 상태면 system 메시지 기록
        if name != prev_prompt and self._emp_no:
            idx = self._get_current_index()
            if idx is not None and self.conversations[idx].is_persisted:
                try:
                    cfg = get_config()
                    prompt = cfg.get_prompt(name)
                    sys_content = prompt.content if prompt else ""
                    if sys_content:
                        conv_id = self.conversations[idx].id
                        chat_service.append_message(
                            smry_id=conv_id,
                            role="system", content=sys_content,
                            emp_no=self._emp_no, model_name=name,
                        )
                except Exception:
                    log.warning("프롬프트 변경 system 메시지 저장 실패", exc_info=True)

    def create_new_conversation(self) -> None:
        """새 대화 생성. 현재 대화가 비어있으면 무시"""
        idx = self._get_current_index()
        if idx is not None and not self.conversations[idx].messages:
            self._refresh_greeting()
            return
        conv = new_conversation()
        self.conversations = [conv, *self.conversations]
        self.current_conversation_id = conv.id
        self.current_input = ""
        self.conversation_attachments = []
        self._refresh_greeting()

    def switch_conversation(self, conv_id: str) -> None:
        """대화 전환. 메시지 미로드 시 DB 에서 로드"""
        self.current_conversation_id = conv_id
        self.current_input = ""
        self.search_query = ""
        idx = self._get_current_index()
        if idx is not None:
            self._load_messages_for(idx)
        return rx.call_script(  # type: ignore[return-value]
            "if (window.__resetAutoScroll) { window.__resetAutoScroll(); }"
        )

    def delete_conversation(self, conv_id: str) -> None:
        """대화 삭제. DB 에 저장된 경우 DB 에서도 제거"""
        conv = next((c for c in self.conversations if c.id == conv_id), None)
        if conv and conv.is_persisted:
            try:
                chat_service.delete_conversation(conv_id, self._emp_no)
            except Exception:
                log.warning("대화 삭제 실패 conv_id=%s", conv_id, exc_info=True)
        self.conversations = [c for c in self.conversations if c.id != conv_id]
        if conv_id == self.current_conversation_id:
            # 빈 미저장 대화가 있으면 그쪽으로 이동, 없으면 새로 생성
            empty = next(
                (c for c in self.conversations if not c.messages and not c.is_persisted),
                None,
            )
            if empty:
                self.current_conversation_id = empty.id
            else:
                new_conv = new_conversation()
                self.conversations = [new_conv, *self.conversations]
                self.current_conversation_id = new_conv.id

    def set_input(self, value: str) -> None:
        """입력 필드 값 설정"""
        self.current_input = value

    def set_search_query(self, value: str) -> None:
        """대화 검색어 설정"""
        self.search_query = value

    def clear_search_query(self) -> None:
        """검색어 초기화"""
        self.search_query = ""

    # ── 첨부파일 ──

    @rx.var
    def accepted_file_extensions(self) -> str:
        """파일 선택 대화상자용 허용 확장자 CSV.

        FILE_PARSER_MODE 와 현재 모델의 vision 지원 여부를 조합해 결정.
        """
        mode = (FILE_PARSER_MODE or "local").lower()
        if mode == "local":
            allowed = set(LOCAL_SUPPORTED_EXTS | file_parser.IMAGE_EXTS)
        elif mode == "upstage":
            allowed = set(UPSTAGE_SUPPORTED_EXTS)
        else:
            allowed = set(LOCAL_SUPPORTED_EXTS | UPSTAGE_SUPPORTED_EXTS)

        # vision 미지원 모델이면 이미지 확장자 제외
        if not self.model_supports_vision:
            allowed -= set(file_parser.IMAGE_EXTS)

        return ",".join(sorted(allowed))

    @rx.var
    def model_supports_vision(self) -> bool:
        """현재 선택된 모델이 이미지 입력(vision)을 지원하는지 여부"""
        try:
            cfg = get_config()
            model = cfg.get_model(self.selected_model)
            return bool(model and getattr(model, "supports_vision", False))
        except Exception:
            return False

    @rx.var
    def has_pending_attachments(self) -> bool:
        """전송 전 대기 중인 첨부파일이 있는지 여부 (첨부 칩 영역 표시 제어)"""
        return len(self.pending_attachments) > 0

    def _ensure_conversation_persisted(self) -> str:
        """파일 업로드 전 대화를 DB 에 저장하고 대화 ID 반환.

        이미 저장된 경우 저장 생략. 대화 생성 실패 시 빈 문자열 반환.
        """
        self._ensure_conversation()
        idx = self._get_current_index()
        if idx is None:
            return ""
        conv = self.conversations[idx]
        if not conv.is_persisted and self._emp_no:
            try:
                chat_service.save_conversation(
                    self._emp_no,
                    conv.id,
                    conv.title or DEFAULT_CONVERSATION_TITLE,
                    self.selected_model,
                )
                self._update_conversation(idx, is_persisted=True)
            except Exception:
                log.warning("대화 영속화 실패 conv_id=%s", conv.id, exc_info=True)
        return conv.id

    def set_attachment_error(self, message: str) -> None:
        """첨부 관련 오류 메시지 설정"""
        self.attachment_error = message

    def remove_pending_attachment(self, file_no: int) -> None:
        """pending 목록에서 첨부파일 제거. DB 에서도 삭제"""
        if self._emp_no:
            try:
                attachment_service.delete_attachment(file_no, self._emp_no)
            except Exception:
                log.warning("첨부 삭제 실패 file_no=%s", file_no, exc_info=True)
        self.pending_attachments = [
            a for a in self.pending_attachments if a.file_no != file_no
        ]

    def download_attachment(self, file_no: int) -> rx.event.EventSpec | None:
        """첨부파일 다운로드 스크립트 실행.

        백엔드 프록시 경유 fetch POST 방식으로 프론트엔드 라우터 간섭 회피.
        소유권 검증 실패 시 None 반환.
        """
        if not self._emp_no:
            return None
        if not attachment_service.verify_ownership(file_no, self._emp_no):
            return None
        return rx.call_script(build_download_script(file_no))

    def _sync_attachments_from_db(self) -> None:
        """DB 를 폴링해 pending 첨부 상태 갱신.

        conversation_attachments 에 이미 있는 파일(전송 완료)은 제외하고,
        아직 메시지로 전송하지 않은 파일만 pending 에 표시.
        """
        sent: set[int] = {a.file_no for a in self.conversation_attachments}
        pending = fetch_pending_attachments(
            emp_no=self._emp_no,
            conv_id=self.current_conversation_id,
            pending_msg_id=self._pending_msg_id,
            already_sent=sent,
        )
        if pending is not None:
            self.pending_attachments = pending

    @rx.event(background=True)
    async def poll_attachments(self) -> None:
        """업로드 트리거 후 DB 를 폴링해 UI 갱신.

        모든 pending 파일이 ready 상태가 되면 조기 종료.
        대용량 파일 처리를 고려해 최대 120초까지 폴링.
        """
        deadline = time.time() + 120.0
        interval = 1.0
        while time.time() < deadline:
            async with self:
                self._sync_attachments_from_db()
                # 모든 pending 파일이 ready 면 폴링 종료
                if self.pending_attachments and all(
                    a.status == "ready" for a in self.pending_attachments
                ):
                    break
                # pending 이 비었으면(전송 완료 등) 종료
                if not self.pending_attachments and not self._pending_msg_id:
                    break
            await asyncio.sleep(interval)
            interval = min(3.0, interval + 0.5)

    def trigger_upload(self) -> rx.event.EventSpec | None:
        """파일 선택 다이얼로그를 열고 업로드를 트리거.

        흐름:
            1. 대화가 미저장 상태라면 DB 에 먼저 저장
            2. JS 가 파일 선택 + fetch POST /api/upload 실행
            3. poll_attachments 백그라운드 이벤트가 DB 를 폴링해 UI 갱신
        """
        self.attachment_error = ""
        conv_id = self._ensure_conversation_persisted()
        if not conv_id:
            self.attachment_error = "대화를 먼저 생성할 수 없습니다."
            return None

        if len(self.pending_attachments) >= FILE_MAX_PER_MESSAGE:
            self.attachment_error = (
                f"메시지당 최대 {FILE_MAX_PER_MESSAGE}개까지 첨부 가능합니다."
            )
            return None

        # 첨부파일-메시지 매핑용 msg_id 를 미리 생성 후 send_message 에서 재사용
        if not self._pending_msg_id:
            self._pending_msg_id = uuid.uuid4().hex[:50]
        msg_id = self._pending_msg_id

        script = build_upload_script(
            accept=self.accepted_file_extensions,
            conv_id=conv_id,
            msg_id=msg_id,
            max_mb=FILE_MAX_SIZE_MB,
            max_per_msg=FILE_MAX_PER_MESSAGE,
            current_count=len(self.pending_attachments),
        )
        # JS 실행 + Python 폴링을 함께 반환
        return [
            rx.call_script(script),
            ChatState.poll_attachments,
        ]

    @rx.event(background=True)
    async def send_message(self, form_data: dict | None = None) -> None:
        """메시지 전송 및 Bedrock 스트리밍 응답 처리.

        흐름:
            1. 사용자 메시지 추가 및 상태 초기화
            2. Bedrock 스트리밍 호출
            3. 최종 AI 메시지 저장 및 상태 복구
            4. 첫 메시지 교환 후 LLM 으로 대화 제목 자동 생성
        """
        # turn 단위 로그 상관관계 바인딩 (이후 모든 로그에 emp/conv/req 태그)
        log_context.bind(
            emp_no=self._emp_no or None,
            conversation_id=self.current_conversation_id or None,
            request_id=log_context.new_request_id(),
        )
        # 1. 사용자 메시지 추가 및 상태 초기화
        blocked_processing = False
        async with self:
            text = self.current_input.strip()
            if not text or self.is_loading:
                return
            # 첨부파일 처리 중 Enter 키 제출 차단 — 버튼 disabled 우회 방지
            if self.has_processing_attachments:
                blocked_processing = True

        if blocked_processing:
            yield rx.toast.info(
                "첨부 파일 분석이 끝나면 전송할 수 있어요.",
                duration=2500,
                position="bottom-center",
            )
            return

        async with self:
            idx = self._get_current_index()
            if idx is None:
                return

            # pending → message 로 이동할 파일 결정
            # DB 에서 최신 상태 재조회해 처리 완료 여부 반영
            if self.pending_attachments and self._pending_msg_id:
                try:
                    fresh = attachment_service.get_attachments_by_msg_id(
                        self._pending_msg_id
                    )
                    refreshed = rows_to_attachment_infos(fresh)
                    if refreshed:
                        self.pending_attachments = refreshed
                except Exception:
                    log.warning(
                        "전송 직전 첨부 상태 갱신 실패 msg_id=%s",
                        self._pending_msg_id, exc_info=True,
                    )
            turn_attachments = list(self.pending_attachments)

            user_msg = Message(
                role="user",
                content=text,
                timestamp=time.time(),
                attachments=turn_attachments,
            )
            updated_messages = [*self.conversations[idx].messages, user_msg]

            # 첫 번째 사용자 메시지 기준으로 임시 제목 설정 (LLM 제목 생성 전 표시용)
            title = self.conversations[idx].title
            is_first_msg = not any(m.role == "user" for m in self.conversations[idx].messages)
            if is_first_msg:
                title = text[:TITLE_MAX_LENGTH]
                if len(text) > TITLE_MAX_LENGTH:
                    title += "..."

            self._update_conversation(idx, title=title, messages=updated_messages)
            self.current_input = ""
            # pending 첨부 → conversation_attachments 로 이동
            if self.pending_attachments:
                self.conversation_attachments = [
                    *self.conversation_attachments,
                    *self.pending_attachments,
                ]
            self.pending_attachments = []
            self.attachment_error = ""
            self.is_loading = True
            self.is_thinking = False
            self.streaming_content = ""
            self._cancel_requested = False

            # State 락 밖에서 DB 저장 시 사용할 로컬 변수
            conv_id = self.conversations[idx].id
            is_persisted = self.conversations[idx].is_persisted
            emp_no = self._emp_no
            model_name = self.selected_model
            use_thinking = self.thinking_enabled
            prompt_name = self.selected_prompt

            # 첨부파일이 있으면 미리 생성한 msg_id 재사용, 없으면 빈 문자열
            pending_msg_id = self._pending_msg_id or ""
            self._pending_msg_id = ""  # 소비 후 초기화

            # API 호출용 메시지 — 텍스트만 포함 (이미지는 image_blocks 로 별도 전달해 중복 방지)
            api_messages = [
                {"role": m.role, "content": m.content}
                for m in updated_messages
            ]

        # DB 저장: 대화·시스템 프롬프트·사용자 메시지 (State 락 밖)
        if emp_no:
            if not is_persisted:
                chat_service.save_conversation(emp_no, conv_id, title, model_name)
                # 시스템 프롬프트를 첫 번째 메시지로 저장
                try:
                    cfg = get_config()
                    prompt = cfg.get_prompt(prompt_name)
                    sys_content = prompt.content if prompt else cfg.system_prompt
                    chat_service.append_message(
                        smry_id=conv_id,
                        role="system", content=sys_content,
                        emp_no=emp_no, model_name=prompt_name,
                    )
                except Exception:
                    log.warning("첫 turn system 메시지 저장 실패", exc_info=True)
            elif is_first_msg:
                chat_service.update_conversation_title(conv_id, title, emp_no)
            user_msg_id = chat_service.append_message(
                smry_id=conv_id,
                role="user", content=text,
                emp_no=emp_no, model_name=model_name,
                provider=prompt_name,
                msg_id=pending_msg_id or None,
            )
            # turn 의 메시지 ID(CHTB_TLK_ID)를 로그 컨텍스트에 바인딩
            # → 이후 스트리밍·tool·응답 로그를 conv 단위가 아닌 메시지 단위로 추적 가능
            log_context.bind(message_id=user_msg_id)

        # 신규 대화 저장 완료 후 is_persisted 갱신
        async with self:
            idx = self._get_current_index()
            if idx is not None and not self.conversations[idx].is_persisted:
                self._update_conversation(idx, is_persisted=True)

        # 2. Bedrock 스트리밍 호출
        content = ""  # 누적 응답 텍스트
        start_time = time.time()
        input_tokens = 0
        output_tokens = 0
        provider = ""
        log.info(
            "chat request",
            extra={
                "model": model_name,
                "prompt": prompt_name,
                "thinking": use_thinking,
                "attachments": len(turn_attachments),
                "history_len": len(api_messages),
            },
        )
        stream_interrupted = False
        try:
            cfg = get_config()
            model = cfg.get_model(model_name) or cfg.default_model
            provider = model.provider
            prompt = cfg.get_prompt(prompt_name)
            base_system = prompt.content if prompt else cfg.system_prompt

            # 대화 전체 첨부파일 메타를 system prompt 에 추가
            system_prompt = augment_system_with_attachments(base_system, conv_id)

            # 이번 turn 의 이미지 첨부를 content block 으로 변환 (마지막 user 메시지에만 적용)
            image_blocks = collect_image_blocks(turn_attachments, model)
            if image_blocks and api_messages:
                api_messages[-1] = {**api_messages[-1], "image_blocks": image_blocks}

            # 대화에 첨부파일이 있으면 tool use(search_attachment) 활성화
            has_attachments = False
            try:
                has_attachments = bool(
                    attachment_service.get_conversation_attachments(conv_id)
                )
            except Exception:
                log.warning("첨부 보유 여부 조회 실패 conv_id=%s", conv_id, exc_info=True)
                has_attachments = False

            if has_attachments:
                tool_config = tool_executor.build_tool_config()

                def _tool_exec(name: str, tool_input: dict) -> dict:
                    return tool_executor.execute_tool(name, tool_input, conv_id)

                stream = astream_chat_with_tools(
                    api_messages,
                    model,
                    system_prompt,
                    thinking_enabled=use_thinking,
                    tool_config=tool_config,
                    tool_executor_fn=_tool_exec,
                    max_iterations=TOOL_USE_MAX_ITERATIONS,
                )
            else:
                stream = astream_chat(
                    api_messages,
                    model,
                    system_prompt,
                    thinking_enabled=use_thinking,
                )

            async for event_type, chunk in stream:
                # 취소 요청 확인
                async with self:
                    if self._cancel_requested:
                        stream_interrupted = True
                        break

                if event_type == "thinking":
                    async with self:
                        self.is_thinking = True
                elif event_type == "text":
                    content += chunk
                    async with self:
                        self.is_thinking = False
                        self.streaming_content = content
                elif event_type == "tool_use":
                    async with self:
                        self.is_thinking = True  # tool 실행 중 스피너 표시
                elif event_type == "tool_result":
                    # 검색 결과는 LLM 이 다음 turn 에서 활용 → UI 에 직접 표시하지 않음
                    pass
                elif event_type == "usage":
                    input_tokens += int(chunk.get("inputTokens", 0) or 0)
                    output_tokens += int(chunk.get("outputTokens", 0) or 0)

        except Exception:
            log.exception("chat streaming 실패 model=%s conv_id=%s", model_name, conv_id)
            content = "오류가 발생했습니다."

        finally:
            # Nova 등 확장 사고 미지원 모델이 <thinking> 블록을 출력하는 경우 제거
            content = response_filter.strip_thinking(content)

            # 중단 시 접미사 추가
            if stream_interrupted and content:
                content += "\n\n*[생성이 중단되었습니다]*"

            # 3. 최종 AI 메시지를 대화에 저장 + 상태 복구
            async with self:
                self._cancel_requested = False
                idx = self._get_current_index()

                if idx is not None and content:
                    ai_msg = Message(
                        role="assistant",
                        content=content,
                        timestamp=time.time(),
                        model_name=model_name,
                    )
                    updated = [*self.conversations[idx].messages, ai_msg]
                    self._update_conversation(idx, messages=updated)
                elif idx is not None and stream_interrupted:
                    # 텍스트 수신 전 중단된 경우
                    ai_msg = Message(
                        role="assistant",
                        content="*[생성이 시작되기 전에 중단되었습니다]*",
                        timestamp=time.time(),
                        model_name=model_name,
                    )
                    updated = [*self.conversations[idx].messages, ai_msg]
                    self._update_conversation(idx, messages=updated)

                self.is_loading = False
                self.is_thinking = False
                self.streaming_content = ""

        # 응답 완료 관측 — 중단·실패 케이스 포함 (비용·지연 추적)
        elapsed = round(time.time() - start_time, 2)
        log.info(
            "chat response",
            extra={
                "model": model_name,
                "provider": provider,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "elapsed_ms": int(elapsed * 1000),
                "interrupted": stream_interrupted,
                "chars": len(content),
            },
        )

        # DB 저장: AI 응답 메시지 (State 락 밖). 텍스트 없이 중단된 경우 저장 생략
        if emp_no and content:
            chat_service.append_message(
                smry_id=conv_id,
                role="assistant", content=content,
                emp_no=emp_no, model_name=model_name,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reply_time=elapsed,
            )

        # 4. 첫 메시지 교환 후 LLM 으로 대화 제목 자동 생성
        if is_first_msg and content and emp_no:
            try:
                generated = generate_title(text, content)
                if generated:
                    chat_service.update_conversation_title(conv_id, generated, emp_no)
                    async with self:
                        idx = self._get_current_index()
                        if idx is not None:
                            self._update_conversation(idx, title=generated)
            except Exception:
                log.warning("대화 제목 자동 생성 실패", exc_info=True)
