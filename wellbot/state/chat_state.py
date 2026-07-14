"""대화 상태 관리 - ChatState.

메시지 전송, 대화 생성/전환/삭제, Bedrock 스트리밍 응답 처리 담당.
DB 연동으로 대화 이력 영속화 보장.
"""

import asyncio
import json
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
    KB_NOT_FOUND_PATTERNS,
    LLM_CONTEXT_MAX_TOKENS,
    LOCAL_SUPPORTED_EXTS,
    MESSAGE_PAGE_SIZE,
    STREAM_FLUSH_INTERVAL_SEC,
    TITLE_MAX_LENGTH,
    TOOL_USE_MAX_ITERATIONS,
    UPSTAGE_SUPPORTED_EXTS,
)
from wellbot.services.ai.bedrock import (
    astream_chat,
    astream_chat_with_tools,
    generate_title,
    read_budget_for,
)
from wellbot.services.chat import chat_service, response_filter, tool_executor
from wellbot.services.core.executor import ensure_io_executor
from wellbot.services.core.settings import ModelConfig, get_config, get_greetings
from wellbot.services.files import attachment_service, file_parser
from wellbot.state.chat_helpers.attachments import (
    collect_image_blocks,
    fetch_pending_attachments,
    rows_to_attachment_infos,
)
from wellbot.state.chat_helpers.context_window import select_context_window
from wellbot.state.chat_helpers.model_params import (
    EFFORT_LABELS,
    EFFORT_PRESETS,
    MAX_TOKENS_PRESETS,
    THINKING_BUDGET_PRESETS,
    apply_overrides,
    nearest_index,
    parse_overrides,
)
from wellbot.state.chat_helpers.download_script import (
    build_download_script,
    build_kb_download_script,
)
from wellbot.state.chat_helpers.system_prompt import (
    augment_system_with_attachments,
    augment_system_with_datetime,
    augment_system_with_kb,
)
from wellbot.state.chat_helpers.upload_script import build_upload_script
from wellbot.state.chat_models import (
    AttachmentInfo,
    Conversation,
    KbSharedFile,
    KbSharedFolder,
    KbSharedSubfolder,
    Message,
    ModelInfo,
    PendingFile,
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
    show_model_settings_panel: bool = False
    # 모델별 파라미터 오버라이드. 브라우저 LocalStorage 에 JSON 으로 영구 저장
    # ({model_name: {param: value}}). 서버 state 로 동기화되어 send_message 에서 읽힘.
    model_settings_raw: str = rx.LocalStorage("{}", name="wellbot_model_settings")
    greeting_text: str = ""

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

    # ── KB (Knowledge Base) ──
    kb_modes: list[str] = []                    # 활성 KB 검색 범위: shared/team/personal
    upload_target: str = "personal"             # 업로드 대상: personal | team
    dept_cd: str = ""                           # 사용자 소속 부서코드 (팀 KB)
    personal_kb_exists: bool = False
    team_kb_exists: bool = False
    kb_scope_inline_expanded: bool = False      # flyout 의 'KB 검색 범위' inline expand 상태
    kb_flyout_open: bool = False                # 지식베이스 hover_card flyout 열림 여부 (controlled)
    ingestion_status: str = "idle"
    ingestion_error: str = ""
    pending_files: list[PendingFile] = []
    _pending_file_data: dict = {}
    show_plus_menu: bool = False
    active_panel: str = ""                       # 입력창 위 패널: "" | "kb_docs" | "kb_upload"
    kb_doc_list: list[dict] = []
    kb_doc_list_tab: str = "personal"            # personal | team | shared
    kb_doc_list_loading: bool = False
    selected_kb_docs: list[str] = []             # 다중 선택된 파일명 (개인/팀 KB 삭제용)
    kb_delete_status: str = "idle"               # idle | processing | ready | error
    kb_delete_error: str = ""
    kb_folder_list: list[KbSharedFolder] = []    # 회사(공용) KB 탭용 그룹 뷰
    expanded_kb_folders: list[str] = []          # 회사 KB 탭에서 펼쳐진 folder_type 목록
    # KB 검색 결과 출처 (스트리밍 중 누적 → 메시지에 첨부)
    _streaming_kb_sources: list[dict] = []

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
            has_more_older=kwargs.get("has_more_older", conv.has_more_older),  # type: ignore[arg-type]
        )
        self.conversations = [
            updated if c.id == updated.id else c for c in self.conversations
        ]

    def _load_messages_for(self, idx: int) -> None:
        """DB 에서 대화 최근 메시지(MESSAGE_PAGE_SIZE) 로드. 이미 로드된 경우 첨부만 갱신.

        이전 메시지는 load_older_messages 로 커서 기반 추가 로드.
        """
        conv = self.conversations[idx]
        if conv.is_loaded:
            self._load_conversation_attachments(conv.id)
            return
        msgs, has_more = chat_service.get_conversation_messages(
            conv.id, self._emp_no, limit=MESSAGE_PAGE_SIZE
        )
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
        self._update_conversation(
            idx, messages=loaded, is_loaded=True, has_more_older=has_more
        )
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
    def can_load_older(self) -> bool:
        """현재 대화에 로드되지 않은 이전 메시지가 DB 에 남아있는지 여부"""
        idx = self._get_current_index()
        if idx is None:
            return False
        return self.conversations[idx].has_more_older

    @rx.var
    def current_title(self) -> str:
        """현재 대화의 제목"""
        idx = self._get_current_index()
        if idx is None:
            return ""
        return self.conversations[idx].title

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
    def has_tabular_pending(self) -> bool:
        """KB 업로드 대기 목록에 표 형식(.xlsx/.csv) 파일이 있는지 여부.

        있을 때만 업로드 패널에 엑셀 형식 안내(강조박스)를 노출한다.
        """
        return any(
            f.name.lower().endswith((".xlsx", ".csv")) for f in self.pending_files
        )

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
    def on_chat_page(self) -> bool:
        """현재 채팅 페이지(홈, '/')에 있는지 여부.

        /ai-services 등 다른 페이지에서는 사이드바 대화 항목을
        하이라이트하지 않기 위해 사용.
        """
        return self.router.url.path == "/"

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

    # ── 모델 설정(파라미터 오버라이드) ──

    def _selected_model_config(self) -> ModelConfig | None:
        """현재 선택된 모델의 base 설정 (없으면 None)."""
        try:
            return get_config().get_model(self.selected_model)
        except Exception:
            return None

    @rx.var
    def model_is_adaptive(self) -> bool:
        """선택 모델이 adaptive thinking(effort 제어) 모델인지 여부"""
        m = self._selected_model_config()
        return bool(m and m.thinking_mode == "adaptive")

    @rx.var
    def model_has_top_p(self) -> bool:
        """선택 모델이 top_p 파라미터를 쓰는지 여부"""
        m = self._selected_model_config()
        return bool(m and m.top_p is not None and m.supports_temperature)

    @rx.var
    def model_supports_temperature(self) -> bool:
        """선택 모델이 temperature 등 sampling 파라미터를 지원하는지 여부.

        Opus 4.7/4.8 등은 sampling 폐기 모델이라 False → 설정 패널에서 숨긴다.
        """
        m = self._selected_model_config()
        return m.supports_temperature if m else True

    @rx.var
    def current_temperature_num(self) -> float:
        """선택 모델의 유효 temperature (슬라이더 값)"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        base = m.temperature if m else 0.5
        try:
            return float(ov.get("temperature", base))
        except (ValueError, TypeError):
            return base

    @rx.var
    def current_temperature_display(self) -> str:
        return str(self.current_temperature_num)

    @rx.var
    def current_top_p_num(self) -> float:
        """선택 모델의 유효 top_p (슬라이더 값)"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        base = m.top_p if (m and m.top_p is not None) else 0.9
        try:
            return float(ov.get("top_p", base))
        except (ValueError, TypeError):
            return base

    @rx.var
    def current_top_p_display(self) -> str:
        return str(self.current_top_p_num)

    @rx.var
    def current_max_tokens_index(self) -> int:
        """max_tokens 슬라이더 인덱스 (프리셋 위치)"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        val = ov.get("max_tokens", m.max_tokens if m else 8192)
        return nearest_index(MAX_TOKENS_PRESETS, val)

    @rx.var
    def current_max_tokens_display(self) -> str:
        return MAX_TOKENS_PRESETS[self.current_max_tokens_index]

    @rx.var
    def current_thinking_budget_index(self) -> int:
        """thinking_budget 슬라이더 인덱스 (프리셋 위치)"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        val = ov.get("thinking_budget", m.thinking_budget if m else 4096)
        return nearest_index(THINKING_BUDGET_PRESETS, val)

    @rx.var
    def current_thinking_budget_display(self) -> str:
        return THINKING_BUDGET_PRESETS[self.current_thinking_budget_index]

    @rx.var
    def current_effort_index(self) -> int:
        """유효 effort 의 슬라이더 인덱스 (0=low ~ 3=xhigh)"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        eff = str(ov.get("effort", m.effort if m else "high"))
        try:
            return EFFORT_PRESETS.index(eff)
        except ValueError:
            return EFFORT_PRESETS.index("high")

    @rx.var
    def current_effort_display(self) -> str:
        """effort 값 표시: 'High' / 'Extra high' 등"""
        ov = parse_overrides(self.model_settings_raw).get(self.selected_model, {})
        m = self._selected_model_config()
        eff = str(ov.get("effort", m.effort if m else "high"))
        return EFFORT_LABELS.get(eff, eff)

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

    # ── KB computed vars ──

    @rx.var
    def use_kb(self) -> bool:
        """KB 검색 사용 여부"""
        return len(self.kb_modes) > 0

    @rx.var
    def kb_mode_display(self) -> str:
        """현재 KB 모드 표시 문자열.

        선택 순서와 무관하게 항상 '회사 - 팀 - 개인' 순서로 정렬해 표시.
        """
        if not self.kb_modes:
            return "KB OFF"
        labels = {"shared": "회사", "team": "팀", "personal": "개인"}
        order = ["shared", "team", "personal"]
        parts = [labels[m] for m in order if m in self.kb_modes]
        return "지식베이스 : " + " + ".join(parts)

    @rx.var
    def kb_docs_empty(self) -> bool:
        """현재 탭에서 문서가 비어 있는지 여부 (UI 의 '업로드된 문서가 없습니다' 분기용)"""
        if self.kb_doc_list_tab == "shared":
            return len(self.kb_folder_list) == 0
        return len(self.kb_doc_list) == 0

    @rx.var
    def kb_delete_button_label(self) -> str:
        """'선택 삭제 (N)' 버튼 레이블"""
        return f"선택 삭제 ({len(self.selected_kb_docs)})"

    # ── Event handlers ──

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
        # 블로킹 I/O 처리용 기본 스레드풀 확대 (멱등, 첫 로드 시 1회 설치).
        # lifespan 에서도 설치하지만 Reflex 이벤트 루프 컨텍스트를 보장하기 위해 재호출.
        ensure_io_executor()

        # AuthState 에서 emp_no 취득
        from wellbot.state.auth_state import AuthState
        auth = await self.get_state(AuthState)
        self._emp_no = auth.current_emp_no

        # KB 초기화: 개인/팀 KB 존재 여부 확인 (검색 범위 활성화 제어용)
        self.kb_modes = []
        if self._emp_no:
            try:
                from wellbot.services.knowledgebase.personal_kb_manager import (
                    get_user_kb as _get_user_kb,
                )
                self.personal_kb_exists = (
                    await asyncio.to_thread(_get_user_kb, self._emp_no) is not None
                )
            except Exception:
                self.personal_kb_exists = False
            try:
                self.dept_cd = auth.current_dept_cd or ""
                if self.dept_cd:
                    from wellbot.services.knowledgebase.team_kb_manager import (
                        ensure_team_kb_membership as _ensure_team_kb_membership,
                    )
                    # 같은 팀의 다른 팀원이 이미 만든 팀 KB 가 있으면 본인 행 자동 등록
                    self.team_kb_exists = (
                        await asyncio.to_thread(
                            _ensure_team_kb_membership, self._emp_no, self.dept_cd
                        )
                        is not None
                    )
            except Exception:
                self.dept_cd = ""
                self.team_kb_exists = False

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
            convs = await asyncio.to_thread(chat_service.list_conversations, self._emp_no)
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
        if self.show_style_panel:
            self.show_model_settings_panel = False

    def toggle_model_settings_panel(self) -> None:
        """모델 설정 패널 표시/숨김 토글"""
        self.show_model_settings_panel = not self.show_model_settings_panel
        if self.show_model_settings_panel:
            self.show_style_panel = False

    def _set_model_param(self, param: str, value: str) -> None:
        """선택 모델의 파라미터 오버라이드를 LocalStorage(JSON)에 기록."""
        data = parse_overrides(self.model_settings_raw)
        entry = dict(data.get(self.selected_model, {}))
        entry[param] = value
        data[self.selected_model] = entry
        self.model_settings_raw = json.dumps(data)

    def _slider_first(self, value) -> float | None:
        """rx.slider on_change 가 넘기는 list 에서 첫 값 추출."""
        try:
            return float(value[0])
        except (ValueError, TypeError, IndexError):
            return None

    def set_model_temperature_slider(self, value: list) -> None:
        v = self._slider_first(value)
        if v is not None:
            self._set_model_param("temperature", str(round(v, 2)))

    def set_model_top_p_slider(self, value: list) -> None:
        v = self._slider_first(value)
        if v is not None:
            self._set_model_param("top_p", str(round(v, 2)))

    def set_model_effort_index(self, value: list) -> None:
        """effort 슬라이더(0~3) → effort 레벨 문자열로 저장."""
        v = self._slider_first(value)
        if v is None:
            return
        idx = max(0, min(len(EFFORT_PRESETS) - 1, int(v)))
        self._set_model_param("effort", EFFORT_PRESETS[idx])

    def set_model_max_tokens_index(self, value: list) -> None:
        """max_tokens 슬라이더 인덱스 → 프리셋 값 저장."""
        v = self._slider_first(value)
        if v is None:
            return
        idx = max(0, min(len(MAX_TOKENS_PRESETS) - 1, int(v)))
        self._set_model_param("max_tokens", MAX_TOKENS_PRESETS[idx])

    def set_model_thinking_budget_index(self, value: list) -> None:
        """thinking_budget 슬라이더 인덱스 → 프리셋 값 저장."""
        v = self._slider_first(value)
        if v is None:
            return
        idx = max(0, min(len(THINKING_BUDGET_PRESETS) - 1, int(v)))
        self._set_model_param("thinking_budget", THINKING_BUDGET_PRESETS[idx])

    def reset_model_settings(self) -> None:
        """선택 모델의 오버라이드 제거 → 기본값(models.yaml)으로 복귀."""
        data = parse_overrides(self.model_settings_raw)
        if self.selected_model in data:
            del data[self.selected_model]
            self.model_settings_raw = json.dumps(data)

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

    def _reset_kb_panels(self) -> None:
        """대화 전환/생성 시 KB UI 임시 상태 정리.

        검색 범위(kb_modes)는 세션 설정이라 유지하고, 열려있던 패널·폴더 펼침과
        종료 상태(ready/error)의 ingest/삭제 메시지만 초기화.
        진행 중인 'processing' 상태는 알림 끊김 방지를 위해 유지.
        """
        self.active_panel = ""
        self.show_style_panel = False
        self.expanded_kb_folders = []
        if self.ingestion_status in ("ready", "error"):
            self.ingestion_status = "idle"
            self.ingestion_error = ""
        if self.kb_delete_status in ("ready", "error"):
            self.kb_delete_status = "idle"
            self.kb_delete_error = ""

    def create_new_conversation(self):
        """새 대화 생성. 현재 대화가 비어있으면 무시.

        채팅 페이지가 아닌 곳(예: /ai-services)에서 호출되면 홈으로 이동한다.
        """
        leaving_other_page = self.router.url.path != "/"
        idx = self._get_current_index()
        if idx is not None and not self.conversations[idx].messages:
            self._refresh_greeting()
            return rx.redirect("/") if leaving_other_page else None
        conv = new_conversation()
        self.conversations = [conv, *self.conversations]
        self.current_conversation_id = conv.id
        self.current_input = ""
        self.conversation_attachments = []
        self._reset_kb_panels()
        self._refresh_greeting()
        return rx.redirect("/") if leaving_other_page else None

    def switch_conversation(self, conv_id: str):
        """대화 전환. 메시지 미로드 시 DB 에서 로드.

        채팅 페이지가 아닌 곳(예: /ai-services)에서 호출되면 홈으로 이동한다.
        """
        leaving_other_page = self.router.url.path != "/"
        self.current_conversation_id = conv_id
        self.current_input = ""
        self.search_query = ""
        self._reset_kb_panels()
        idx = self._get_current_index()
        if idx is not None:
            self._load_messages_for(idx)
        if leaving_other_page:
            return rx.redirect("/")
        return rx.call_script(
            "if (window.__resetAutoScroll) { window.__resetAutoScroll(); }"
        )

    async def load_older_messages(self) -> None:
        """현재 대화의 이전(더 오래된) 메시지 페이지를 커서 기반으로 추가 로드.

        DB 조회는 to_thread 로 오프로드해 이벤트 루프를 막지 않는다.
        """
        idx = self._get_current_index()
        if idx is None:
            return
        conv = self.conversations[idx]
        if not conv.messages or not conv.has_more_older:
            return

        oldest_seq = conv.messages[0].seq
        older, has_more = await asyncio.to_thread(
            chat_service.get_conversation_messages,
            conv.id,
            self._emp_no,
            limit=MESSAGE_PAGE_SIZE,
            before_seq=oldest_seq,
        )
        older_msgs = [
            Message(
                role=m["role"],
                content=m["content"],
                timestamp=m["timestamp"],
                model_name=m.get("model_name", ""),
                seq=m.get("seq", 0),
            )
            for m in older
        ]

        # 로드 사이 대화가 전환되지 않았는지 재확인 후 prepend
        idx = self._get_current_index()
        if idx is None or self.conversations[idx].id != conv.id:
            return
        current = self.conversations[idx]
        self._update_conversation(
            idx,
            messages=[*older_msgs, *current.messages],
            has_more_older=has_more if older_msgs else False,
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

    # ── KB event handlers ──

    def set_upload_target(self, value: str) -> None:
        """KB 업로드 대상(personal/team) 설정"""
        self.upload_target = value

    def set_kb_doc_list_tab(self, tab: str) -> None:
        """KB 문서 목록 탭(personal/team) 전환 후 목록 재로드.

        탭 전환 시 선택 상태와 삭제 상태를 초기화.
        """
        self.kb_doc_list_tab = tab
        self.selected_kb_docs = []
        self.kb_delete_status = "idle"
        self.kb_delete_error = ""
        return ChatState.load_kb_docs  # type: ignore

    def toggle_kb_doc_selection(self, filename: str) -> None:
        """KB 문서 다중 선택 토글"""
        if filename in self.selected_kb_docs:
            self.selected_kb_docs = [f for f in self.selected_kb_docs if f != filename]
        else:
            self.selected_kb_docs = self.selected_kb_docs + [filename]

    def toggle_kb_folder(self, folder_type: str) -> None:
        """회사 KB 탭의 문서종류 폴더 펼침/접힘 토글"""
        if folder_type in self.expanded_kb_folders:
            self.expanded_kb_folders = [
                f for f in self.expanded_kb_folders if f != folder_type
            ]
        else:
            self.expanded_kb_folders = self.expanded_kb_folders + [folder_type]

    @rx.event(background=True)
    async def confirm_kb_delete(self):
        """선택된 파일들을 S3 에서 삭제한 뒤 ingestion job 으로 벡터 인덱스 정리.

        background 이벤트 — 인덱스 정리 ingestion poll 이 길어도 이벤트 채널을 점유하지
        않아 처리 중에도 토글/패널이 응답한다(긴 침묵으로 인한 websocket 끊김도 회피).
        상태 변경(self.xxx)은 반드시 `async with self:` 안에서 수행하고, 무거운 작업
        (run_in_executor)은 락 밖에서 await 한다.
        현재 활성 탭(personal/team)에 따라 KB manager 분기. team KB 는 같은 팀원 누구나
        삭제 가능(현재 정책).
        """
        async with self:
            if not self.selected_kb_docs:
                return
            filenames = list(self.selected_kb_docs)
            emp_no = self._emp_no
            tab = self.kb_doc_list_tab
            if not emp_no:
                self.kb_delete_status = "error"
                self.kb_delete_error = "로그인 정보를 확인할 수 없습니다."
                return
            self.kb_delete_status = "processing"
            self.kb_delete_error = ""

        try:
            import asyncio as _asyncio
            from wellbot.services.knowledgebase.kb_utils import poll_ingestion_status as _poll

            loop = _asyncio.get_running_loop()

            if tab == "team":
                from wellbot.services.knowledgebase.kb_utils import (
                    is_ingestion_in_progress as _team_in_progress,
                )
                from wellbot.services.knowledgebase.team_kb_manager import (
                    delete_files_from_team_kb,
                    get_dept_cd as _get_dept_cd,
                    get_user_team_kb as _get_team_kb,
                    start_ingestion as _team_start_ingestion,
                )

                # 0. 부서코드는 클라이언트 상태(self.dept_cd)가 아니라 emp_no 로
                #    서버에서 도출 — 위·변조/stale dept_cd 로 다른 팀 prefix 의
                #    파일을 삭제하거나, ingest 대상 KB 와 어긋난 prefix 를 지우는 것을 방지.
                dept_cd = await loop.run_in_executor(None, lambda: _get_dept_cd(emp_no))
                if not dept_cd:
                    raise RuntimeError("소속 팀 정보가 없습니다.")

                # 1. KB 정보 조회
                kb_info = await loop.run_in_executor(None, lambda: _get_team_kb(emp_no))
                if not kb_info:
                    raise RuntimeError("팀 KB 정보를 찾을 수 없습니다.")

                # 2. 진행 중인 ingestion 체크 (다른 팀원이 작업 중인지)
                in_progress = await loop.run_in_executor(
                    None,
                    lambda: _team_in_progress(kb_info["kb_id"], kb_info["data_source_id"]),
                )
                if in_progress:
                    raise RuntimeError(
                        "현재 다른 팀원이 문서를 처리 중입니다. 잠시 후 다시 시도해주세요."
                    )

                # 3. S3 파일 삭제 (서버 도출 dept_cd 기준)
                await loop.run_in_executor(
                    None, lambda: delete_files_from_team_kb(dept_cd, filenames)
                )

                # 4. Ingestion job 실행 → 벡터 인덱스에서 삭제분 정리
                job_id = await loop.run_in_executor(
                    None,
                    lambda: _team_start_ingestion(
                        kb_info["kb_id"], kb_info["data_source_id"]
                    ),
                )
            else:
                from wellbot.services.knowledgebase.personal_kb_manager import (
                    delete_files_from_personal_kb,
                    get_user_kb as _get_user_kb,
                    start_ingestion as _personal_start_ingestion,
                )

                # 1. KB 정보 조회 — S3 삭제 전에 먼저 확인. KB 레코드가 없는데
                #    원본만 지우면 벡터 인덱스는 그대로 남아(검색 가능) desync 가
                #    발생하므로, 없으면 아무것도 삭제하지 않고 즉시 실패.
                kb_info = await loop.run_in_executor(None, lambda: _get_user_kb(emp_no))
                if not kb_info:
                    raise RuntimeError("개인 KB 정보를 찾을 수 없습니다.")

                # 2. S3 파일 삭제
                await loop.run_in_executor(
                    None, lambda: delete_files_from_personal_kb(emp_no, filenames)
                )

                # 3. Ingestion job 실행 → 벡터 인덱스에서 삭제분 정리
                job_id = await loop.run_in_executor(
                    None,
                    lambda: _personal_start_ingestion(
                        kb_info["kb_id"], kb_info["data_source_id"]
                    ),
                )

            status = await loop.run_in_executor(
                None,
                lambda: _poll(kb_info["kb_id"], kb_info["data_source_id"], job_id),
            )

            ok = status == "COMPLETE" or status.startswith("COMPLETE_WITH_ERRORS")
            async with self:
                if ok:
                    self.kb_delete_status = "ready"
                    self.selected_kb_docs = []
                else:
                    self.kb_delete_status = "error"
                    self.kb_delete_error = f"인덱스 정리 실패: {status}"
            if ok:
                yield ChatState.load_kb_docs  # 목록 새로고침
        except Exception as e:
            async with self:
                self.kb_delete_status = "error"
                self.kb_delete_error = str(e)
        finally:
            # processing 고착 방지(M3): 위 경로에서 종료 상태를 못 찍고 빠져나온 경우에만
            # error 로 떨어뜨려 UI 가 영구 잠기지 않게 한다(정상 종료 시엔 no-op).
            async with self:
                if self.kb_delete_status == "processing":
                    self.kb_delete_status = "error"
                    self.kb_delete_error = "처리가 완료되지 않았습니다. 다시 시도해 주세요."

    async def load_kb_docs(self) -> None:
        """현재 탭(personal/team)에 해당하는 S3 파일 목록 로드.

        raw/ 와 originals/ 두 prefix 를 합쳐서 표시.
        (pptx 등 변환 대상 원본은 originals/ 에 별도 저장)
        """
        import asyncio as _asyncio
        from datetime import timezone as _tz, timedelta as _td
        from wellbot.services.files import storage_service
        from wellbot.services.knowledgebase.kb_utils import is_tabular_part

        self.kb_doc_list_loading = True
        self.kb_doc_list = []
        self.kb_folder_list = []
        yield

        emp_no = self._emp_no
        dept_cd = self.dept_cd
        tab = self.kb_doc_list_tab

        loop = _asyncio.get_running_loop()
        try:
            if tab == "shared":
                # 회사 KB: shared{env}/{문서종류}/raw/{파일} 구조 → 문서종류 단위 그룹 뷰.
                # 공용 prefix base 는 dev/prd 분기(shared / shared-dev) — kb_utils 단일 출처.
                from wellbot.services.knowledgebase.config import get_kb_config
                from wellbot.services.knowledgebase.kb_utils import shared_base
                shared_cfg = get_kb_config().get("shared_kb", {})
                shared_bucket = shared_cfg.get("s3_bucket", "")
                if not shared_bucket:
                    self.kb_folder_list = []
                    return

                base = shared_base()
                items = await loop.run_in_executor(
                    None,
                    lambda: storage_service.list_objects_with_meta(f"{base}/", shared_bucket),
                )
                # 대분류 → 소분류 → 파일목록 으로 그룹핑.
                # raw/ = 인덱싱 대상(원본 + 변환본). originals/ = 인덱싱 제외 원본
                # (xlsx→Upstage 변환 시 원본 xlsx 보관 위치). 변환본(_xlsx.md 등)은
                # list_objects_with_meta 가 이미 제외하므로 raw/+originals/ 를 합치면
                # 원본이 정확히 1번 노출된다. 같은 (대분류,소분류,파일)은 중복 제거.
                folder_map: dict[str, dict[str, list[dict]]] = {}
                seen_shared: set[tuple[str, str, str]] = set()
                for obj in items:
                    key = obj["key"]
                    parts = key.split("/")
                    # shared{env}/{대분류}/{raw|originals}/{...} 형태만 채택
                    if (
                        len(parts) < 4
                        or parts[0] != base
                        or parts[2] not in ("raw", "originals")
                    ):
                        continue
                    top = parts[1]
                    rest = parts[3:]
                    # rest 가 1개면 소분류 없음, 2개 이상이면 첫 segment 가 소분류
                    if len(rest) >= 2:
                        sub = rest[0]
                        filename = "/".join(rest[1:])
                    else:
                        sub = ""
                        filename = rest[0]
                    if (top, sub, filename) in seen_shared:
                        continue
                    seen_shared.add((top, sub, filename))
                    lm = obj["last_modified"]
                    if lm.tzinfo is None:
                        lm = lm.replace(tzinfo=_tz.utc)
                    folder_map.setdefault(top, {}).setdefault(sub, []).append({
                        "file_name": filename,
                        "uploaded_at": lm.strftime("%Y-%m-%d"),
                        "expires_at": "-",
                    })

                # 대분류 가나다순, 소분류 가나다순("" 은 맨 앞 = 대분류 직속 파일).
                # 파일은 업로드일 내림차순 + 파일명 오름차순 (stable sort 2단계).
                self.kb_folder_list = [
                    KbSharedFolder(
                        folder_type=top,
                        subfolders=[
                            KbSharedSubfolder(
                                sub_name=sub,
                                files=[
                                    KbSharedFile(**f)
                                    for f in sorted(
                                        sorted(files, key=lambda d: d["file_name"]),
                                        key=lambda d: d["uploaded_at"],
                                        reverse=True,
                                    )
                                ],
                            )
                            for sub, files in sorted(subs.items())
                        ],
                    )
                    for top, subs in sorted(folder_map.items())
                ]
                return

            # personal / team: 본인(또는 팀) prefix 의 raw/ + originals/ 병합
            from wellbot.services.knowledgebase.kb_utils import (
                get_originals_prefix,
                raw_prefix,
            )
            if tab == "personal":
                raw_pfx = raw_prefix("personal", emp_no)
            else:  # team
                raw_pfx = raw_prefix("team", dept_cd)
            orig_pfx = get_originals_prefix(raw_pfx)

            raw_items = await loop.run_in_executor(
                None, storage_service.list_objects_with_meta, raw_pfx
            )
            originals_items = await loop.run_in_executor(
                None, storage_service.list_objects_with_meta, orig_pfx
            )
            # 동일 파일명이 양쪽에 있을 경우 originals/ 의 원본을 우선.
            # 분할본(_partN)은 원본(originals/)으로 대표되므로 목록에서 제외 →
            # 사용자에게 '파일 1개'로 표시되고, 선택 삭제도 논리 파일 단위로 동작.
            seen: set[str] = set()
            combined: list[dict] = []
            for obj in originals_items + raw_items:
                if is_tabular_part(obj["file_name"]):
                    continue
                if obj["file_name"] in seen:
                    continue
                seen.add(obj["file_name"])
                combined.append(obj)

            expiry_days = 365
            docs = []
            for obj in combined:
                lm = obj["last_modified"]
                if lm.tzinfo is None:
                    lm = lm.replace(tzinfo=_tz.utc)
                expiry = lm + _td(days=expiry_days)
                docs.append({
                    "file_name": obj["file_name"],
                    "uploaded_at": lm.strftime("%Y-%m-%d"),
                    "expires_at": expiry.strftime("%Y-%m-%d"),
                })
            # 업로드일 내림차순 정렬
            docs.sort(key=lambda d: d["uploaded_at"], reverse=True)
            self.kb_doc_list = docs
        except Exception:
            log.exception("KB 문서 목록 로드 실패")
            self.kb_doc_list = []
        finally:
            self.kb_doc_list_loading = False

    def toggle_kb_scope_inline(self) -> None:
        """지식베이스 flyout 의 'KB 검색 범위' inline expand 토글"""
        self.kb_scope_inline_expanded = not self.kb_scope_inline_expanded

    def toggle_kb_mode(self, mode: str) -> rx.event.EventSpec | None:
        """체크박스 토글: 해당 KB 모드를 추가/제거. KB 미존재 시 토스트 안내"""
        if mode == "personal" and not self.personal_kb_exists:
            return rx.toast.warning(
                "개인 KB가 없습니다.",
                description="파일 업로드에서 파일을 먼저 업로드해 KB를 생성하세요.",
                duration=4000,
            )
        if mode == "team" and not self.team_kb_exists:
            return rx.toast.warning(
                "팀 KB가 없습니다.",
                description="파일 업로드에서 파일을 먼저 업로드해 KB를 생성하세요.",
                duration=4000,
            )
        if mode in self.kb_modes:
            self.kb_modes = [m for m in self.kb_modes if m != mode]
        else:
            self.kb_modes = self.kb_modes + [mode]

    def on_plus_menu_open_change(self, is_open: bool) -> None:
        """+ 메뉴 팝오버 열림/닫힘 상태 동기화.

        팝오버가 닫히면 지식베이스 flyout 도 함께 닫기 (외부 클릭으로 전체 dismiss).
        """
        self.show_plus_menu = is_open
        if not is_open:
            self.kb_flyout_open = False
            self.kb_scope_inline_expanded = False

    def on_kb_flyout_open_change(self, is_open: bool) -> None:
        """hover_card 의 open 변화 처리.

        Radix 의 자동 close (mouseleave timer) 는 무시.
        True 로 변하는 경우만 반영해서, 한 번 열리면 명시적 사용자 행동으로만 닫힘.
        """
        if is_open:
            self.kb_flyout_open = True

    def close_kb_flyout(self) -> None:
        """지식베이스 flyout 닫기 (다른 메뉴 항목 hover/flyout 내 클릭 시).

        inline expand 상태도 함께 리셋해서 다음 열림 때 깔끔한 상태로 시작.
        """
        self.kb_flyout_open = False
        self.kb_scope_inline_expanded = False

    def download_kb_source(self, s3_uri: str, filename: str):
        """KB 출처 문서를 백엔드 프록시(/api/download_kb)로 다운로드.

        S3 presigned URL 직접 사용은 내부망 환경에서 차단될 수 있어
        백엔드를 통한 스트리밍 다운로드로 변경.
        """
        return rx.call_script(build_kb_download_script(s3_uri, filename))

    def open_panel(self, panel: str) -> None:
        """2차 메뉴에서 기능 선택 → 메뉴 닫고 입력창 위 패널 열기"""
        self.show_plus_menu = False
        self.show_style_panel = False
        self.active_panel = panel

    def close_panel(self) -> None:
        """입력창 위 모든 패널 닫기. 선택값은 유지.

        KB 패널(검색 범위/문서 목록/업로드)의 X 버튼과 외부 영역 클릭 모두 사용.
        스타일 패널도 같이 닫기 (KB 패널과 동시에 열릴 일은 없으므로 대부분 no-op).
        ingestion/delete 의 'ready'·'error' 종료 메시지도 함께 정리.
        진행 중인 'processing' 상태는 유지하여 알림 끊김을 방지.
        회사 KB 폴더 펼침 상태는 패널 닫힐 때 초기화 (다음 열림에서 깔끔한 상태로).

        주의: 이 핸들러는 메시지 영역 전체(message_area)의 on_click 으로도 걸려 있어,
        답변 텍스트를 드래그 선택하고 마우스를 놓는 click 에서도 호출된다. 닫을 패널이
        없는데도 state 를 재할당하면 빈 delta 가 전송돼 마크다운이 re-render 되고
        브라우저의 텍스트 선택이 풀린다. 변경할 게 없으면 early-return 하여 막는다.
        """
        if not (
            self.active_panel
            or self.show_style_panel
            or self.show_model_settings_panel
            or self.expanded_kb_folders
            or self.ingestion_status in ("ready", "error")
            or self.kb_delete_status in ("ready", "error")
        ):
            return
        self.active_panel = ""
        self.show_style_panel = False
        self.show_model_settings_panel = False
        self.expanded_kb_folders = []
        if self.ingestion_status in ("ready", "error"):
            self.ingestion_status = "idle"
            self.ingestion_error = ""
        if self.kb_delete_status in ("ready", "error"):
            self.kb_delete_status = "idle"
            self.kb_delete_error = ""

    def open_file_picker(self) -> rx.event.EventSpec:
        """KB 파일 선택 다이얼로그를 열고 파일 메타데이터 수집.

        사용자가 다이얼로그를 취소한 경우 'cancel' 이벤트(브라우저 native)로
        즉시 빈 배열로 resolve. 이렇게 하지 않으면 Promise 가 30초 timeout
        까지 pending 상태로 남아 다른 이벤트들이 큐에 쌓이고 UI 가 멈춘 것처럼 보이는 문제.
        """
        return rx.call_script(
            "(function() {"
            "  var existing = window._kbPendingMeta || [];"
            "  if (existing.length > 0) {"
            "    window._kbPendingMeta = [];"
            "    return Promise.resolve(existing);"
            "  }"
            "  openKbFilePicker();"
            "  return new Promise(function(resolve) {"
            "    var check = setInterval(function() {"
            "      var meta = window._kbPendingMeta || [];"
            "      if (meta.length > 0) {"
            "        clearInterval(check);"
            "        window._kbPendingMeta = [];"
            "        resolve(meta);"
            "      } else if (window._kbPickerCanceled) {"
            "        clearInterval(check);"
            "        window._kbPickerCanceled = false;"
            "        resolve([]);"
            "      }"
            "    }, 200);"
            "    setTimeout(function() { clearInterval(check); resolve([]); }, 30000);"
            "  });"
            "})()",
            callback=ChatState.add_pending_files_from_js,
        )

    def add_pending_files_from_js(self, files_meta: list[dict]) -> None:
        """JS openKbFilePicker() 완료 후 콜백. 파일 메타데이터만 수신"""
        for meta in files_meta:
            name = meta.get("name", "")
            size = meta.get("size", 0)
            if any(f.name == name for f in self.pending_files):
                continue
            self.pending_files = self.pending_files + [
                PendingFile(
                    name=name,
                    size=size,
                    size_display=self._format_size(size),
                )
            ]

    def remove_pending_file(self, filename: str) -> rx.event.EventSpec:
        """선택 목록에서 파일 제거.

        JS 측 누적 선택 배열(_kbSelectedFiles)에서도 함께 제거해야 같은 파일을
        다시 고를 수 있다(안 그러면 change 핸들러 dedup 에 걸려 재선택이 유실됨).
        """
        self.pending_files = [f for f in self.pending_files if f.name != filename]
        self._pending_file_data.pop(filename, None)
        import json as _json
        return rx.call_script(f"removeKbSelectedFile({_json.dumps(filename)})")

    def clear_pending_files(self) -> rx.event.EventSpec:
        """선택 목록 전체 초기화 (JS 누적 선택 배열도 함께 비움)"""
        self.pending_files = []
        self._pending_file_data = {}
        return rx.call_script("clearKbSelectedFiles()")

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _user_friendly_error(self, error: str) -> str:
        """기술적 에러 메시지를 사용자 친화적 메시지로 변환"""
        e = error.lower()
        if "지원하지 않는 파일 형식" in error:
            return error
        if "파일 크기 초과" in error:
            return error
        if "최대 5개" in error:
            return error
        if "소속 팀 정보" in error:
            return error
        if "timeout" in e or "타임아웃" in error:
            return "처리 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
        if "다른 팀원이 문서를 처리 중" in error:
            return error
        if "No files selected" in error:
            # 패널(pending_files)과 브라우저 선택(_kbSelectedFiles)이 어긋난 경우.
            # 관리자 문의가 아니라 재선택을 안내.
            return "선택된 파일을 찾을 수 없습니다. 파일을 다시 선택해 주세요."
        log.warning("KB 처리 오류 (사용자에게는 일반 메시지 표시): %s", error)
        return "문서 처리 중 오류가 발생했습니다. 관리자에게 문의해주세요."

    async def confirm_upload_via_api(self):
        """확정 버튼 클릭 → JS 로 S3 업로드 실행 → on_upload_complete 콜백"""
        if not self.pending_files:
            return
        self.ingestion_status = "uploading"
        self.ingestion_error = ""
        yield
        # 패널에 남아있는 파일명을 allowedNames 로 함께 전달 → JS 가 그 파일들만 업로드
        # (여러 번 선택해 누적된 _kbSelectedFiles 중, 패널에서 제거하지 않은 것만).
        import json as _json
        args = _json.dumps([
            self._emp_no,
            self.upload_target,
            self.dept_cd,
            [f.name for f in self.pending_files],
        ])
        yield rx.call_script(
            f"uploadKbFilesToApi.apply(null, {args})",
            callback=ChatState.on_upload_complete,
        )

    @rx.event(background=True)
    async def on_upload_complete(self, result):
        """JS uploadKbFilesToApi() 완료 후 콜백.

        background 이벤트 — 변환+ingest 가 길어도 이벤트 채널을 점유하지 않아
        처리 중에도 토글/패널이 응답하고, 긴 침묵으로 인한 websocket 유휴 끊김을
        피한다. 상태 변경(self.xxx)은 반드시 `async with self:` 안에서 수행하고,
        무거운 작업(run_in_executor)은 락 밖에서 await 한다.
        """
        if isinstance(result, str):
            import json as _json
            try:
                result = _json.loads(result)
            except Exception:
                async with self:
                    self.ingestion_status = "error"
                    self.ingestion_error = f"응답 파싱 실패: {result}"
                    self.pending_files = []
                return

        if result is None or (isinstance(result, dict) and result.get("error")):
            # 업로드 자체 실패: S3 는 엔드포인트(stage_raw_files)가 부분 적재분을 롤백.
            # 패널 대기 목록도 비워 stale 방지(JS _kbSelectedFiles 도 함께 비워져 재선택 가능).
            error_msg = result.get("error", "업로드 실패") if result else "업로드 응답 없음"
            async with self:
                self.ingestion_status = "error"
                self.ingestion_error = self._user_friendly_error(error_msg)
                self.pending_files = []
            return

        import asyncio as _asyncio

        async with self:
            # 이번 turn 에 S3 에 올라간 파일명(원본). 색인 실패 시 고아 정리용.
            uploaded_names = [f.name for f in self.pending_files]
            emp_no = self._emp_no
            upload_target = self.upload_target
            self.ingestion_status = "processing"

        try:
            # 축2(변환+KB확보+ingest+poll+롤백)는 kb_ingest_service 가 담당. blocking 이라
            # run_in_executor 로 한 번에 실행 — async with self 밖이라 처리 중 락을 잡지 않는다.
            from wellbot.services.knowledgebase.kb_ingest_service import ingest_staged

            loop = _asyncio.get_running_loop()
            outcome = await loop.run_in_executor(
                None, lambda: ingest_staged(upload_target, emp_no, uploaded_names)
            )

            async with self:
                if outcome.busy:
                    self.ingestion_status = "error"
                    self.ingestion_error = (
                        "현재 다른 팀원이 문서를 처리 중입니다. 잠시 후 다시 시도해주세요."
                    )
                else:
                    status = outcome.status
                    if status == "COMPLETE":
                        self.ingestion_status = "ready"
                        self.ingestion_error = ""
                        self._mark_kb_exists(upload_target)
                    elif status.startswith("COMPLETE_WITH_ERRORS"):
                        self.ingestion_status = "ready"
                        self.ingestion_error = "일부 문서 처리에 실패했습니다. 관리자에게 문의해주세요."
                        log.warning("KB ingestion 부분 실패: %s", status)
                        self._mark_kb_exists(upload_target)
                    else:
                        self.ingestion_status = "error"
                        self.ingestion_error = "문서 처리에 실패했습니다. 관리자에게 문의해주세요."

        except TimeoutError:
            async with self:
                self.ingestion_status = "error"
                self.ingestion_error = "처리 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
            log.warning("KB ingestion 타임아웃")
        except Exception as e:
            async with self:
                self.ingestion_status = "error"
                self.ingestion_error = self._user_friendly_error(str(e))
            log.exception("KB ingestion 예외")
        finally:
            async with self:
                self.pending_files = []
                self._pending_file_data = {}

    def _mark_kb_exists(self, upload_target: str) -> None:
        """ingestion 성공 후 해당 scope 의 KB 존재 플래그 + kb_modes 갱신."""
        if upload_target == "team":
            self.team_kb_exists = True
            if "team" not in self.kb_modes:
                self.kb_modes = self.kb_modes + ["team"]
        else:
            self.personal_kb_exists = True
            if "personal" not in self.kb_modes:
                self.kb_modes = self.kb_modes + ["personal"]

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
        start = time.time()
        deadline = start + 120.0
        # 트리거 시점의 기존 첨부 수. 이보다 늘어나야(=새 업로드가 DB 안착) 조기 종료 허용.
        # (붙여넣은 이미지가 이미 ready 인 상태에서 파일추가 시, 새 파일이 아직 DB 에
        #  없을 때 'all ready' 로 조기 종료돼 새 파일이 UI 에 안 뜨던 버그 방지)
        start_count = len(self.pending_attachments)
        settle_grace = 8.0  # 새 업로드가 안 올라오면(취소 등) 이 시간 후 종료 허용
        # 업로드 직후 칩이 빨리 뜨도록 처음엔 촘촘히 폴링하고 점차 백오프.
        # (이미지는 파싱을 건너뛰어 거의 즉시 ready 가 되므로 초기 응답성이 중요)
        interval = 0.3
        while time.time() < deadline:
            async with self:
                self._sync_attachments_from_db()
                # 새 업로드 안착(개수 증가) 또는 grace 경과 또는 기존 첨부 없음(빈 시작)일 때만
                # 조기 종료 허용 — 인플라이트 업로드를 놓치지 않도록.
                can_exit = (
                    start_count == 0
                    or len(self.pending_attachments) > start_count
                    or (time.time() - start) >= settle_grace
                )
                # 모든 pending 파일이 종료 상태(ready 또는 failed)면 폴링 종료.
                # (실패도 종료 상태로 취급해야 실패 시 120초 데드라인까지 헛돌지 않음)
                if can_exit and self.pending_attachments and all(
                    a.status != "processing" for a in self.pending_attachments
                ):
                    break
                # pending 이 비었으면(전송 완료 등) 종료
                if not self.pending_attachments and not self._pending_msg_id:
                    break
            await asyncio.sleep(interval)
            interval = min(3.0, interval + 0.3)

    def _prepare_attachment_upload(self) -> tuple[str, str] | None:
        """첨부 업로드 공통 준비: 대화 영속화 + 한도 체크 + msg_id 발급.

        Returns:
            (conv_id, msg_id): 준비 완료
            None: 실패 (attachment_error 설정됨)
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
        return conv_id, self._pending_msg_id

    def trigger_upload(self) -> rx.event.EventSpec | None:
        """파일 선택 다이얼로그를 열고 업로드를 트리거.

        흐름:
            1. 대화가 미저장 상태라면 DB 에 먼저 저장
            2. JS 가 파일 선택 + fetch POST /api/upload 실행
            3. poll_attachments 백그라운드 이벤트가 DB 를 폴링해 UI 갱신
        """
        prep = self._prepare_attachment_upload()
        if prep is None:
            return None
        conv_id, msg_id = prep

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

    def handle_paste_upload(self) -> rx.event.EventSpec | list | None:
        """클립보드 이미지 붙여넣기 업로드 (JS paste 리스너가 트리거).

        JS 가 붙여넣은 이미지를 window._pastedFiles 에 담고 숨김 버튼을 눌러
        호출한다. 여기서 conv_id/msg_id 를 발급한 뒤, JS(wellbotUploadPasted)가
        그 파일들을 /api/upload 로 전송하도록 call_script 를 돌려준다.
        """
        # vision 미지원 모델에서는 이미지 첨부 의미가 없으므로 차단 (파일 선택 picker 와 동일)
        if not self.model_supports_vision:
            self.attachment_error = "현재 모델은 이미지 입력을 지원하지 않습니다."
            return rx.call_script("window._pastedFiles = [];")

        prep = self._prepare_attachment_upload()
        if prep is None:
            # 한도 초과 등으로 중단 시 적재된 클립보드 파일을 비워 다음 붙여넣기와 섞이지 않게 함
            return rx.call_script("window._pastedFiles = [];")
        conv_id, msg_id = prep

        script = (
            f"window.wellbotUploadPasted("
            f"'{conv_id}', '{msg_id}', {FILE_MAX_SIZE_MB}, "
            f"{FILE_MAX_PER_MESSAGE}, {len(self.pending_attachments)})"
        )
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
                    fresh = await asyncio.to_thread(
                        attachment_service.get_attachments_by_msg_id,
                        self._pending_msg_id,
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
            use_kb = self.use_kb
            kb_modes = list(self.kb_modes)
            model_overrides_raw = self.model_settings_raw

            # 첨부파일이 있으면 미리 생성한 msg_id 재사용, 없으면 빈 문자열
            pending_msg_id = self._pending_msg_id or ""
            self._pending_msg_id = ""  # 소비 후 초기화

            # API 호출용 메시지 — 텍스트만 포함 (이미지는 image_blocks 로 별도 전달해 중복 방지).
            # 히스토리는 토큰 예산(LLM_CONTEXT_MAX_TOKENS)으로 제한해 긴 대화의
            # 입력 토큰 폭증을 방지 (최근 우선 윈도우, 문서 회상은 툴이 담당).
            context_messages = select_context_window(
                updated_messages, LLM_CONTEXT_MAX_TOKENS
            )
            api_messages = [
                {"role": m.role, "content": m.content}
                for m in context_messages
            ]

        # DB 저장: 대화·시스템 프롬프트·사용자 메시지 (State 락 밖).
        # 동기 pymysql 호출을 to_thread 로 오프로드해 단일 이벤트 루프 블로킹 방지.
        if emp_no:
            if not is_persisted:
                await asyncio.to_thread(
                    chat_service.save_conversation, emp_no, conv_id, title, model_name
                )
                # 시스템 프롬프트를 첫 번째 메시지로 저장
                try:
                    cfg = get_config()
                    prompt = cfg.get_prompt(prompt_name)
                    sys_content = prompt.content if prompt else cfg.system_prompt
                    await asyncio.to_thread(
                        chat_service.append_message,
                        smry_id=conv_id,
                        role="system", content=sys_content,
                        emp_no=emp_no, model_name=prompt_name,
                    )
                except Exception:
                    log.warning("첫 turn system 메시지 저장 실패", exc_info=True)
            elif is_first_msg:
                await asyncio.to_thread(
                    chat_service.update_conversation_title, conv_id, title, emp_no
                )
            user_msg_id = await asyncio.to_thread(
                chat_service.append_message,
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
            # 사용자 파라미터 오버라이드(모델 설정 패널) 적용
            model = apply_overrides(
                model, parse_overrides(model_overrides_raw).get(model_name, {})
            )
            provider = model.provider
            prompt = cfg.get_prompt(prompt_name)
            base_system = prompt.content if prompt else cfg.system_prompt

            # 대화 전체 첨부파일 메타를 system prompt 에 추가
            # 현재 시각(KST) 주입 → 상대 날짜 표현 해석. 매 턴 최신값으로 갱신.
            system_prompt = augment_system_with_datetime(base_system)
            system_prompt = augment_system_with_attachments(system_prompt, conv_id)
            # KB 활성화 시 검색 지침 + 인용 표기 규칙 추가
            if use_kb and kb_modes:
                system_prompt = augment_system_with_kb(system_prompt, kb_modes)

            # 이번 turn 의 이미지 첨부를 content block 으로 변환 (마지막 user 메시지에만 적용)
            image_blocks = collect_image_blocks(turn_attachments, model)
            if image_blocks and api_messages:
                api_messages[-1] = {**api_messages[-1], "image_blocks": image_blocks}

            # 검색 가능한(텍스트) 첨부가 있을 때만 search_attachment 활성화.
            # token_count>0 = 텍스트 추출·청킹 완료. 이미지(0)·처리중(None)은 검색 대상이
            # 아니므로 제외 — 노출하면 LLM 이 빈 검색을 반복(폴백까지 소진)한다.
            has_searchable_attachments = False
            try:
                conv_attachments = await asyncio.to_thread(
                    attachment_service.get_conversation_attachments, conv_id
                )
                has_searchable_attachments = any(
                    (getattr(a, "token_count", None) or 0) > 0 for a in conv_attachments
                )
            except Exception:
                log.warning("첨부 보유 여부 조회 실패 conv_id=%s", conv_id, exc_info=True)

            tool_config = None
            if has_searchable_attachments or use_kb:
                tool_config = tool_executor.build_tool_config(
                    include_attachment=has_searchable_attachments,
                    include_kb=bool(use_kb),
                )

            if tool_config:
                # read_attachment 결과를 모델 윈도우에 맞춰 한 번에 싣도록 예산 산정.
                # 이렇게 하면 offset 이어읽기 누적으로 인한 컨텍스트 초과가 원천 차단된다.
                read_budget = read_budget_for(model.context_window, model.max_tokens)

                def _tool_exec(name: str, tool_input: dict) -> dict:
                    # 검색 범위는 사용자의 UI 선택(kb_modes)으로 결정. kb_scope 는 LLM 에
                    # 노출하지 않고 여기서 주입 (툴 스키마에도 부재).
                    if name == "kb_search":
                        tool_input = {**tool_input, "kb_scope": kb_modes}
                    return tool_executor.execute_tool(
                        name, tool_input, conv_id, emp_no, max_read_tokens=read_budget
                    )

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

            # 토큰 배치 스트리밍: streaming_content 갱신(state 락 + WebSocket push)을
            # STREAM_FLUSH_INTERVAL_SEC 간격으로 묶어 토큰당 락·네트워크 폭주를 방지.
            # content 누적은 락 없이 수행하고, 취소 확인도 flush/경계 시점에만 한다.
            last_flush = time.monotonic()
            pending_text = False  # content 에 반영됐지만 아직 push 안 된 텍스트 존재

            async for event_type, chunk in stream:
                if event_type == "text":
                    content += chunk
                    pending_text = True
                    if time.monotonic() - last_flush >= STREAM_FLUSH_INTERVAL_SEC:
                        async with self:
                            if self._cancel_requested:
                                stream_interrupted = True
                                break
                            self.is_thinking = False
                            self.streaming_content = content
                        pending_text = False
                        last_flush = time.monotonic()
                elif event_type == "thinking":
                    async with self:
                        if pending_text:  # 경계 전 대기 텍스트 먼저 반영(순서 보존)
                            self.streaming_content = content
                            pending_text = False
                        if self._cancel_requested:
                            stream_interrupted = True
                            break
                        self.is_thinking = True
                    last_flush = time.monotonic()
                elif event_type == "tool_use":
                    async with self:
                        if pending_text:
                            self.streaming_content = content
                            pending_text = False
                        if self._cancel_requested:
                            stream_interrupted = True
                            break
                        self.is_thinking = True  # tool 실행 중 스피너 표시
                    last_flush = time.monotonic()
                elif event_type == "tool_result":
                    # kb_search 결과 출처 누적 (같은 source_uri 는 ranks 만 병합 — 인용 마커 매칭 보존).
                    # search_attachment 결과는 LLM 이 다음 turn 에서 활용 → UI 에 직접 표시 제외
                    if chunk.get("name") == "kb_search":
                        new_docs = chunk.get("source_docs") or []
                        if new_docs:
                            async with self:
                                if pending_text:  # 경계 전 대기 텍스트 먼저 반영
                                    self.streaming_content = content
                                    pending_text = False
                                by_uri = {
                                    d.get("source_uri"): d
                                    for d in self._streaming_kb_sources
                                }
                                merged = list(self._streaming_kb_sources)
                                for doc in new_docs:
                                    uri = doc.get("source_uri")
                                    new_ranks = doc.get("ranks") or []
                                    if uri in by_uri:
                                        existing_ranks = by_uri[uri].get("ranks") or []
                                        for r in new_ranks:
                                            if r not in existing_ranks:
                                                existing_ranks.append(r)
                                        by_uri[uri]["ranks"] = existing_ranks
                                        # rank → page 매핑 병합 (인용된 페이지만 추려 표시).
                                        existing_rank_pages = by_uri[uri].get("rank_pages") or {}
                                        existing_rank_pages.update(doc.get("rank_pages") or {})
                                        by_uri[uri]["rank_pages"] = existing_rank_pages
                                    else:
                                        merged.append(doc)
                                        by_uri[uri] = doc
                                self._streaming_kb_sources = merged
                elif event_type == "usage":
                    input_tokens += int(chunk.get("inputTokens", 0) or 0)
                    output_tokens += int(chunk.get("outputTokens", 0) or 0)

        except Exception as exc:
            log.exception("chat streaming 실패 model=%s conv_id=%s", model_name, conv_id)
            content = response_filter.classify_stream_error(exc) or "오류가 발생했습니다."

        finally:
            # Nova 등 확장 사고 미지원 모델이 <thinking> 블록을 출력하는 경우 제거
            content = response_filter.strip_thinking(content)

            # 중단 시 접미사 추가
            if stream_interrupted and content:
                content += "\n\n*[생성이 중단되었습니다]*"

            # 빈 응답 방지: 텍스트 없이 정상 종료(콘텐츠 필터 차단 등)면 침묵 대신 안내.
            # content_filtered 는 스트림에서 별도 신호를 주지 않으므로 '비었고 & 중단 아님'
            # 으로 포괄 감지한다. (오류 케이스는 위 except 에서 이미 content 설정)
            used_empty_fallback = False
            if not content.strip() and not stream_interrupted:
                used_empty_fallback = True
                content = (
                    "요청은 처리했지만 표시할 답변을 받지 못했어요. "
                    "안전 정책에 의해 응답이 차단되었거나 일시적인 문제일 수 있어요. "
                    "질문 표현을 조금 바꿔 다시 시도해 주세요."
                )
                log.warning(
                    "빈 응답 대체 메시지 적용 conv_id=%s model=%s", conv_id, model_name
                )

            # 출처 필터링: LLM 이 본문에 [N] 인용 마커를 표기한 청크만 유지
            # 1) 본문에서 [1], [1, 3], [1][3] 등 인용 마커의 번호 추출
            # 2) 마커가 있으면 → 인용된 ranks 만 유지
            #    마커가 없으면 → '정보 없음' 패턴 검사 (LLM 이 못 찾았다고 답한 경우 제거)
            #    그것도 아니면 → LLM 이 마커를 잊은 경우로 보고 전체 유지 (fallback)
            # 3) 본문에서 [N] 마커 자체는 제거하여 사용자에게는 깔끔하게 표시
            import re as _re

            all_sources = list(self._streaming_kb_sources)
            cited_ranks: set[int] = set()
            for m in _re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", content):
                for num in m.group(1).split(","):
                    try:
                        cited_ranks.add(int(num.strip()))
                    except ValueError:
                        pass

            if used_empty_fallback:
                # 답변이 아닌 안내 메시지엔 출처를 붙이지 않음
                final_sources = []
            elif cited_ranks:
                final_sources = [
                    s for s in all_sources
                    if any(r in cited_ranks for r in (s.get("ranks") or []))
                ]
            elif all_sources and any(p in content for p in KB_NOT_FOUND_PATTERNS):
                final_sources = []
            else:
                final_sources = all_sources

            # 출처 칩에 표시할 PDF 페이지 문자열을 확정 (PDF + 페이지 있을 때만 비어있지 않음).
            # UI 는 truthiness 로만 분기하므로 과거 메시지(키 없음)도 안전하게 미표시된다.
            # 주의: final_sources 원소는 state 변수(_streaming_kb_sources)의 dict 라
            # background task 의 컨텍스트 밖에서 직접 변형하면 ImmutableStateError 가 난다.
            # 읽기는 허용되므로, 변형 대신 새 plain dict 로 복사하며 키를 추가한다.
            def _with_pages_display(s: dict) -> dict:
                # 인용 마커가 있으면 그 문서에서 '실제 인용된 청크'의 페이지만,
                # 없으면(fallback) 검색된 전체 페이지. (페이지 집합은 rank_pages 에서 파생)
                rank_pages = s.get("rank_pages") or {}
                ranks = (cited_ranks & rank_pages.keys()) if cited_ranks else rank_pages.keys()
                pages = sorted({rank_pages[r] for r in ranks if rank_pages[r] is not None})
                display = "p." + ", ".join(str(p) for p in pages) if (s.get("ext") == "pdf" and pages) else ""
                return {**s, "pages_display": display}

            final_sources = [_with_pages_display(s) for s in final_sources]

            # KB 근거(grounding) 관측: 검색·누적된 출처 대비 답변에 실제 인용된 출처 수.
            # cited=0 인데 retrieved>0 이면 미근거(환각 가능) 신호.
            if use_kb:
                log.info(
                    "kb grounding: retrieved=%d cited=%d",
                    len(all_sources), len(final_sources),
                )

            # 본문에서 인용 마커 제거 ([1], [1, 3] 등)
            content = _re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", content)

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
                        source_docs=final_sources,
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
                self._streaming_kb_sources = []

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
            await asyncio.to_thread(
                chat_service.append_message,
                smry_id=conv_id,
                role="assistant", content=content,
                emp_no=emp_no, model_name=model_name,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reply_time=elapsed,
            )

        # 4. 첫 메시지 교환 후 LLM 으로 대화 제목 자동 생성.
        # generate_title 은 동기 Bedrock 호출 → to_thread 로 루프 블로킹 방지.
        if is_first_msg and content and emp_no:
            try:
                generated = await asyncio.to_thread(generate_title, text, content)
                if generated:
                    await asyncio.to_thread(
                        chat_service.update_conversation_title,
                        conv_id, generated, emp_no,
                    )
                    async with self:
                        idx = self._get_current_index()
                        if idx is not None:
                            self._update_conversation(idx, title=generated)
            except Exception:
                log.warning("대화 제목 자동 생성 실패", exc_info=True)
