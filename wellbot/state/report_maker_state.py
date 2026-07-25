"""보고서 문구 작성 지원 — Reflex 상태머신 (백지 재작성).

legacy ChatState 의 flow_stage 흐름을 동일 UX 로 재현하되:
- 신원은 사번 입력이 아니라 AuthState 세션의 emp_no (서버 도출)
- 서비스 로직은 report_maker 서비스 계층(analysis/structure/build/style)
- 영속은 db(대화·템플릿) / memory(AgentCore 스타일) / storage(S3 파일)
- 진행 상황은 background task(@rx.event(background=True)) + async with self 경계에서
  점진 반영 (메인 챗과 동일 스트리밍 패턴; foreground 핸들러는 응답 완료까지 락을 잡아
  스트리밍 delta 가 화면에 반영되지 않으므로 background 로 처리)

흐름:
  템플릿 선택/생성 → start_session(스타일 로드) → 주제 입력
  → analyze → await_page_count → [정보 게이트] → propose_structure → await_clarify
  → 1회 되묻기 → build → 편집 루프
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

import reflex as rx
from pydantic import BaseModel

from wellbot.constants import STREAM_FLUSH_INTERVAL_SEC
from wellbot.logger import log_context
from wellbot.services.ai.bedrock.converse import adrain_generator
from wellbot.services.files import attachment_service
from wellbot.state.chat_helpers.download_script import build_download_script
from wellbot.services.report_maker import (
    analysis,
    bedrock,
    build,
    db,
    memory,
    slides,
    storage,
    structure,
    style,
)
from wellbot.services.report_maker.config import get_config
from wellbot.services.report_maker.parsing import (
    extract_questions,
    fmt_pages,
    md_linebreaks,
    parse_page_count,
    strip_question_block,
    to_safe_id,
)
from wellbot.state.auth_state import AuthState
from wellbot.state.chat_state import ChatState

log = logging.getLogger(__name__)

_SKIP_WORDS = ("진행", "그냥", "그대로", "없음", "TBD", "tbd", "진행해", "진행해줘")

# 분석 요약 점진 노출(타이핑 효과)의 flush 횟수. 분석은 블로킹 JSON 이라 스트리밍이
# 불가능하므로, 완성된 요약 문자열을 이 횟수로 잘라 초안 스트리밍과 같은 시간 배치
# (STREAM_FLUSH_INTERVAL_SEC)로 흘린다. 길이와 무관하게 총 노출 시간이 일정하도록 청크
# 크기를 길이/횟수로 잡는다(짧은 글은 굵게, 긴 글은 잘게).
_REVEAL_FLUSHES = 18

MODE_INTRO = (
    "----------------------------------------------------\n\n"
    "## 보고 유형 안내\n"
    "이 에이전트는 두 가지 보고 유형을 지원합니다.\n\n"
    " **서머리 (정기 보고)** — 팀의 여러 과제를 한눈에 정리하는 정기 보고입니다.\n\n"
    " **심층 보고** — 하나의 주제/안건을 배경→검증→기대효과→향후 계획 흐름으로 깊이 풀어냅니다.\n\n"
    " ※ 보고유형은 입력한 토픽을 기반으로 에이전트가 자동 분류합니다.\n"
)


class ReportMessage(BaseModel):
    """대화 메시지(표시용). role="user"|"assistant", is_outline=최종 아웃라인."""

    role: str = "assistant"
    content: str = ""
    is_outline: bool = False
    is_flow: bool = False
    is_loading: bool = False  # 대기 자리표시자(분석/생성 중) — UI 에서 스피너+문구로 렌더
    style_saved: bool = False
    file_name: str = ""
    file_no: int = 0        # 첨부 파일 번호(atch_file_m). 0 이면 첨부 없음
    msg_id: str = ""        # chtb_tlk_id — 첨부(ChatMessageAttachment) 매핑·영속에 사용
    model_name: str = ""    # 생성 모델 ID(스트리밍 응답에만). 영속 시 chtb_mdl_nm 로 저장
    input_tokens: int = 0   # 스트리밍 응답의 입력 토큰(metadata usage). 영속 시 기록
    output_tokens: int = 0  # 스트리밍 응답의 출력 토큰(metadata usage). 영속 시 기록


class ConvSummary(BaseModel):
    id: str = ""
    title: str = ""
    created_at: float = 0.0
    created_label: str = ""  # 작성일 표시용(YYYY.MM.DD)
    template_id: str = ""   # 대화가 속한 보고서 유형(없으면 legacy)


class ReportMakerState(rx.State):
    # ── 신원 (백엔드 전용) ──
    _emp_no: str = ""

    # ── 템플릿(보고서 유형) ──
    template_id: str = ""
    template_display: str = ""
    templates: list[dict] = []
    show_new_template: bool = False
    show_template_menu: bool = False
    # 마지막 사용 유형(브라우저 영구 저장) — 재진입 시 자동 선택해 랜딩 존 없이 바로 챗 진입
    last_template_id: str = rx.LocalStorage("", name="wellbot_rm_last_template")

    # ── 세션 ──
    session_ready: bool = False
    session_id: str = ""
    loaded_style: str = ""
    user_mode: str = ""          # "report_based" | "text_based"
    edited_style: str = ""       # 편집기: 단일 편집 스타일 정본(문서 추출+수동편집 통합)

    # ── 대화 ──
    messages: list[ReportMessage] = []
    conversation_list: list[ConvSummary] = []
    show_report_history: bool = False   # '이전 보고서' 모달 열림 여부
    report_history_query: str = ""      # 이전 보고서 검색어(제목 필터)
    # ── 이름 변경 공용 다이얼로그(유형/대화) — 컨트롤드(폼 submit-in-close 회피) ──
    rename_open: bool = False
    rename_kind: str = ""               # "template" | "conversation"
    rename_id: str = ""
    rename_value: str = ""
    # ── 슬라이드 미리보기 ──
    show_slides: bool = False           # 슬라이드 미리보기 오버레이 열림
    slides_loading: bool = False        # 렌더 중(파싱→태깅→렌더)
    slides_src: str = ""                # 렌더된 슬라이드 HTML의 data URI(iframe src)
    _slides_hash: str = ""              # 원본 아웃라인 해시(캐시 무효화)
    is_streaming: bool = False
    show_guide: bool = False   # 시작 화면의 '상세 작성 가이드' 토글(입력 항목 1~6 안내)

    # ── 생성 흐름(flow) ──
    outline: str = ""
    iteration: int = 0
    flow_stage: str = ""
    pending_topic: str = ""
    flow_analysis: str = ""
    report_type: str = ""
    report_type_name: str = ""
    report_mode: str = "deep"
    report_storyline: str = ""
    report_storyline_blocks: str = ""
    deepdive_targets: str = ""
    page_count: float = 0
    recommended_pages: float = 0
    page_options: list[dict] = []
    proposed_structure: str = ""
    pending_questions: list[str] = []
    struct_gate_total: int = 0
    gate_asked_questions: list[str] = []
    outline_reasked: bool = False
    edit_instructions: list[str] = []

    # ── 업로드 ──
    style_upload_status: str = ""
    _uploaded_topic_text: str = ""
    pending_topic_file: str = ""      # 첨부됐지만 아직 전송 안 한 주제 파일명(표시용)
    pending_topic_file_no: int = 0    # 대기 중 첨부의 atch_file_m 번호
    _pending_msg_id: str = ""         # 첨부-메시지 매핑용 사전발급 msg_id(전송 시 메시지에 부여)
    style_docs: list[dict] = []       # 추출 문서 목록 [{"name","key"}] (key=삭제 식별용 S3 key)

    # 채팅에서 넘어온 보고서 seed(대화 메시지 본문). _reset_conversation 에서 지우지 않고
    # 세션 시작 시점(_start_session)에 _uploaded_topic_text 로 적용한다.
    _pending_seed: str = ""

    # ── 영속 커서 (백엔드) ──
    _persisted_count: int = 0

    # ══════════════════════════════════════════════════════════
    # computed
    # ══════════════════════════════════════════════════════════
    @rx.var
    def is_report_based(self) -> bool:
        return self.user_mode == "report_based"

    @rx.var
    def has_templates(self) -> bool:
        return len(self.templates) > 0

    @rx.var
    def recent_chats_label(self) -> str:
        return "최근 대화" if self.conversation_list else ""

    @rx.var
    def can_save_style(self) -> bool:
        """편집기 저장 가능 여부 — 내용이 비어 있으면(초기화 직후 등) 저장 버튼 비활성화."""
        return bool(self.edited_style.strip())

    # ══════════════════════════════════════════════════════════
    # 진입 / 인증
    # ══════════════════════════════════════════════════════════
    def _bind_log(self, **extra) -> None:
        """이후 로그에 emp/conv/req 상관관계 태그 주입(메인 챗과 동일 규약).

        State 이벤트는 API 미들웨어를 안 거치므로 여기서 컨텍스트를 바인딩해야
        로그 앞머리 [emp=… conv=… …] 가 채워진다. 핸들러 진입부에서 호출한다.
        extra 로 넘긴 필드(예: conversation_id)는 기본값을 덮어쓴다.
        """
        fields = {
            "emp_no": self._emp_no or None,
            "conversation_id": self.session_id or None,
            "request_id": log_context.new_request_id(),
        }
        fields.update(extra)
        log_context.bind(**fields)

    @rx.event
    async def on_load(self):
        """페이지 진입 — 유형이 있으면 랜딩 존 없이 바로 챗으로 자동 진입.

        마지막 사용 유형(없으면 첫 유형)을 자동 선택해 세션을 시작한다. 이미 세션 중이면
        유지하고, 유형이 하나도 없을 때만 유형 생성 화면(_setup_view 빈 상태)을 보인다.
        """
        auth = await self.get_state(AuthState)
        self._emp_no = auth.current_emp_no
        if not self._emp_no:
            yield rx.redirect("/login")
            return
        self._bind_log()
        await self._load_templates()
        await self._consume_report_seed()
        # 이미 세션 진행 중이면 그대로 유지
        if self.session_ready and self.template_id:
            return
        target = self._pick_auto_template()
        if not target:
            return   # 유형 0개 → 생성 화면
        self.template_id = target
        t = await asyncio.to_thread(db.get_template, self._emp_no, target)
        self.template_display = t["display"] if t else target
        async for _ in self._start_session():
            yield

    def _pick_auto_template(self) -> str:
        """자동 진입 대상 유형 — 마지막 사용 유형 우선, 없으면 첫 유형."""
        ids = [t["id"] for t in self.templates]
        if self.last_template_id and self.last_template_id in ids:
            return self.last_template_id
        return ids[0] if ids else ""

    async def _load_templates(self):
        self.templates = await asyncio.to_thread(db.list_templates, self._emp_no)

    async def _consume_report_seed(self):
        """채팅에서 넘어온 보고서 seed(본문)를 cross-state 로 읽어 소비한다.

        본문은 URL 이 아니라 ChatState backend 필드(_report_seed_content)에 담겨 넘어온다.
        로그인 사용자 자신의 대화 본문이므로 별도 재조회/소유권 검증 없이 그대로 쓴다.
        (seq 참조 + DB 재조회 방식은 스트리밍 직후 메시지의 in-memory seq 가 0 이라
        get_message_content 가 못 찾아 seed 가 유실됐음.)
        세션이 이미 열려 있으면 즉시 적용하고, 아니면 유형 선택 시 _start_session 이 적용한다.

        '보고서 만들기'는 새 보고서 작성 의도이므로, 진행 중 세션(이력)이 남아 있으면
        기존 flow_stage/outline/messages 에 seed 를 얹지 않고 새 대화로 분리한 뒤 적용한다
        (이어쓰기 방지). seed 없는 일반 페이지 이동은 초기화하지 않아 이력이 유지된다.
        """
        chat = await self.get_state(ChatState)
        content = chat._report_seed_content
        if not content:
            return
        chat._report_seed_content = ""   # 중복 소비 방지
        self._pending_seed = content
        if self.session_ready:
            if self.messages:            # 진행 이력이 있으면 새 대화로 분리 후 적용
                self._reset_conversation()
                self.session_id = uuid.uuid4().hex[:50]
                await self._load_conversation_list()
            self._apply_pending_seed()

    def _apply_pending_seed(self):
        """보류 중인 seed 를 주제 첨부 슬롯에 적용(칩 재사용). 세션 리셋 이후에만 호출."""
        if not self._pending_seed:
            return
        self._uploaded_topic_text = self._pending_seed
        self.pending_topic_file = "대화에서 가져온 내용"
        self.pending_topic_file_no = 0
        self._pending_seed = ""

    # ══════════════════════════════════════════════════════════
    # 템플릿(보고서 유형)
    # ══════════════════════════════════════════════════════════
    @rx.event
    def toggle_new_template(self):
        self.show_new_template = not self.show_new_template

    @rx.event
    def toggle_template_menu(self):
        self.show_template_menu = not self.show_template_menu

    @rx.event
    def close_template_menu(self):
        self.show_template_menu = False

    @rx.event
    async def select_template(self, template_id: str):
        self._bind_log()
        t = await asyncio.to_thread(db.get_template, self._emp_no, template_id)
        self.template_id = template_id
        self.template_display = t["display"] if t else template_id
        self.show_template_menu = False
        async for _ in self._start_session():
            yield

    @rx.event
    async def create_template(self, form_data: dict):
        self._bind_log()
        name = (form_data.get("template_name") or "").strip()
        if not name:
            yield rx.toast.error("보고서 유형명을 입력해주세요.")
            return
        tid = to_safe_id(name)
        actor = memory.actor_id_for(self._emp_no, tid)
        ok = await asyncio.to_thread(db.save_template, self._emp_no, tid, name, actor)
        if not ok:
            yield rx.toast.error(f"보고서 유형은 최대 {get_config().max_templates}개까지 가능합니다.")
            return
        log.info("유형 생성 emp_no=%s template=%s", self._emp_no, tid)
        await self._load_templates()
        self.template_id = tid
        self.template_display = name
        self.show_new_template = False
        async for _ in self._start_session():
            yield

    # ── 공용 이름 변경 다이얼로그 (유형/대화) ──
    @rx.event
    def start_rename_template(self, template_id: str, current: str):
        self.rename_kind = "template"
        self.rename_id = template_id
        self.rename_value = current
        self.rename_open = True

    @rx.event
    def start_rename_conversation(self, session_id: str, current: str):
        self.rename_kind = "conversation"
        self.rename_id = session_id
        self.rename_value = current
        self.rename_open = True

    @rx.event
    def set_rename_value(self, v: str):
        self.rename_value = v

    @rx.event
    def set_rename_open(self, v: bool):
        self.rename_open = v

    @rx.event
    async def commit_rename(self):
        """다이얼로그 저장 — 유형(display만) 또는 대화(제목) 이름 변경.

        유형: template_id 는 to_safe_id(원래 이름)로 고정돼 S3/스타일 스코프를 결정하므로
        display 만 갱신(save_template upsert). 대화: 헤더 제목만 갱신.
        """
        self._bind_log()
        name = self.rename_value.strip()
        if not name:
            yield rx.toast.error("이름을 입력해주세요.")
            return
        kind, rid = self.rename_kind, self.rename_id
        if kind == "template":
            t = await asyncio.to_thread(db.get_template, self._emp_no, rid)
            if not t:
                yield rx.toast.error("보고서 유형을 찾을 수 없습니다.")
                return
            await asyncio.to_thread(db.save_template, self._emp_no, rid, name, t["actor"])
            log.info("유형 이름변경 emp_no=%s template=%s", self._emp_no, rid)
            await self._load_templates()
            if self.template_id == rid:
                self.template_display = name
        elif kind == "conversation":
            await asyncio.to_thread(db.update_conversation_title, rid, name, self._emp_no)
            await self._load_conversation_list()
        else:
            self.rename_open = False
            return
        self.rename_open = False
        yield rx.toast.success("이름을 변경했습니다.")

    @rx.event
    async def edit_template_style(self, template_id: str):
        """세션 시작 없이 해당 유형의 작성 스타일 편집기로 바로 이동(유형만 지정)."""
        t = await asyncio.to_thread(db.get_template, self._emp_no, template_id)
        self.template_id = template_id
        self.template_display = t["display"] if t else template_id
        return rx.redirect("/ai-services/report-generator/style")

    @rx.event
    async def delete_template(self, template_id: str):
        """보고서 유형 완전 삭제 — DB 비활성화 + S3 전체 + AgentCore 기록까지.

        template_id 는 to_safe_id(이름)로 고정돼 같은 이름 재생성 시 스코프가 동일하다.
        AgentCore 레코드를 남기면 재생성 때 load_style 폴백으로 옛 스타일이 되살아나므로,
        삭제 문구('모두 삭제')대로 AgentCore(/writing·/preference)까지 정리해 백지로 만든다.
        """
        self._bind_log()
        # AgentCore /writing·/preference + S3 스타일 파일 삭제
        deleted_records = await asyncio.to_thread(memory.clear_style, self._emp_no, template_id)
        # 나머지 S3(대화·주제 첨부 등) 프리픽스 전체 삭제
        await asyncio.to_thread(storage.delete_template_files, self._emp_no, template_id)
        await asyncio.to_thread(db.delete_template, self._emp_no, template_id)
        log.info(
            "유형 삭제 emp_no=%s template=%s (AgentCore 레코드 %d건 삭제)",
            self._emp_no, template_id, deleted_records,
        )
        await self._load_templates()
        if self.last_template_id == template_id:
            self.last_template_id = ""   # 자동 진입이 삭제된 유형을 다시 집지 않도록
        if self.template_id == template_id:
            self.session_ready = False
            self.template_id = ""
            self.template_display = ""

    # ══════════════════════════════════════════════════════════
    # 세션
    # ══════════════════════════════════════════════════════════
    async def _start_session(self):
        self.is_streaming = True
        yield
        # 세션 시작 — 정본이 있으면 LLM 없이 그대로, 없으면 legacy 이관(1회 정규화).
        self.loaded_style = await asyncio.to_thread(
            memory.load_style, self._emp_no, self.template_id
        )
        self.user_mode = "report_based" if self.loaded_style.strip() else "text_based"
        self._reset_conversation()
        self._apply_pending_seed()   # 채팅에서 넘어온 seed 는 리셋 이후에 적용
        self.session_id = uuid.uuid4().hex[:50]
        self.session_ready = True
        self.last_template_id = self.template_id   # 재진입 자동 선택용 기억
        self._bind_log()   # session_id 확정 후 재바인딩 → 이후 로그에 conv 채움
        log.info(
            "세션 시작 emp_no=%s template=%s session=%s",
            self._emp_no, self.template_id, self.session_id,
        )
        await self._load_conversation_list()
        self.is_streaming = False
        yield

    def _reset_conversation(self):
        """새 대화 초기화. 새 상태 변수는 반드시 여기에만 추가한다."""
        self.messages = []
        self.outline = ""
        self.iteration = 0
        self.flow_stage = ""
        self.pending_topic = ""
        self.flow_analysis = ""
        self.report_type = ""
        self.report_type_name = ""
        self.report_mode = "deep"
        self.report_storyline = ""
        self.report_storyline_blocks = ""
        self.deepdive_targets = ""
        self.page_count = 0
        self.recommended_pages = 0
        self.page_options = []
        self.proposed_structure = ""
        self.pending_questions = []
        self.struct_gate_total = 0
        self.gate_asked_questions = []
        self.outline_reasked = False
        self.edit_instructions = []
        self._uploaded_topic_text = ""
        self._persisted_count = 0

    @rx.event
    async def start_new_chat(self):
        self._reset_conversation()
        self.session_id = uuid.uuid4().hex[:50]
        await self._load_conversation_list()

    # ══════════════════════════════════════════════════════════
    # 대화 이력 (chtb_smry_d / chtb_msg_d, AGNT_ID 태깅)
    # ══════════════════════════════════════════════════════════
    async def _load_conversation_list(self):
        rows = await asyncio.to_thread(db.list_conversations, self._emp_no)
        self.conversation_list = [
            ConvSummary(id=r["id"], title=r["title"], created_at=r["created_at"],
                        created_label=r.get("created_label", ""),
                        template_id=r.get("template_id", ""))
            for r in rows
        ]

    @rx.event
    async def load_conversation_list(self):
        await self._load_conversation_list()

    # ── '이전 보고서' 모달 ──
    @rx.var
    def filtered_conversations(self) -> list[ConvSummary]:
        """검색어(제목 부분일치)로 필터한 이전 보고서 목록."""
        q = self.report_history_query.strip().lower()
        if not q:
            return self.conversation_list
        return [c for c in self.conversation_list if q in c.title.lower()]

    @rx.event
    def set_report_history_query(self, value: str):
        self.report_history_query = value

    @rx.event
    def open_report_history(self):
        self.report_history_query = ""
        self.show_report_history = True

    @rx.event
    def close_report_history(self):
        self.show_report_history = False
        self.report_history_query = ""

    @rx.event
    async def load_conversation_by_id(self, session_id: str):
        self._bind_log(conversation_id=session_id)
        rows = await asyncio.to_thread(
            db.get_conversation_messages, session_id, self._emp_no
        )
        self._reset_conversation()
        self.session_id = session_id
        msgs: list[ReportMessage] = []
        last_outline = ""
        for r in rows:
            is_outline = r["role"] == "outline"
            role = "assistant" if is_outline else r["role"]
            msg = ReportMessage(
                role=role, content=r["content"], is_outline=is_outline, msg_id=r.get("msg_id", ""),
            )
            # 첨부 복원 — 사용자 메시지에 매핑된 첨부(파일명·다운로드) 표시
            if role == "user" and r.get("msg_id"):
                atts = await asyncio.to_thread(
                    attachment_service.get_attachments_by_msg_id, r["msg_id"]
                )
                if atts:
                    msg.file_name = atts[0].file_name
                    msg.file_no = atts[0].file_no
            msgs.append(msg)
            if is_outline:
                last_outline = r["content"]
        self.messages = msgs
        self.outline = last_outline  # 편집 이어가기 복원 (flow_state 는 비영속)
        self._persisted_count = len(msgs)
        # 대화가 속한 보고서 유형으로 정렬(스타일 포함). 유형이 삭제/미상이면 현재 유형 유지.
        return await self._align_template_to_conversation(session_id)

    async def _align_template_to_conversation(self, session_id: str):
        """불러온 대화의 보고서 유형·스타일을 맞춘다.

        - 유형이 현재와 다르고 아직 존재하면: template_id·표시명·스타일 재설정
        - 유형이 삭제됐으면(태그는 있으나 목록에 없음): 현재 유형 유지 + 안내 토스트
        - 태그가 없으면(legacy 대화): 조용히 현재 유형 유지
        """
        conv_tid = next(
            (c.template_id for c in self.conversation_list if c.id == session_id), ""
        )
        if not conv_tid or conv_tid == self.template_id:
            return
        if conv_tid not in {t["id"] for t in self.templates}:
            # 유형이 삭제됨 → 현재 유형/스타일 유지, 사용자에게 안내
            return rx.toast.info(
                "이 대화의 보고서 유형이 삭제되어, 현재 유형·스타일로 불러왔습니다."
            )
        # 유형 존재 → 유형·스타일 정렬
        self.template_id = conv_tid
        self.last_template_id = conv_tid
        t = await asyncio.to_thread(db.get_template, self._emp_no, conv_tid)
        self.template_display = t["display"] if t else conv_tid
        self.loaded_style = await asyncio.to_thread(
            memory.load_style, self._emp_no, conv_tid
        )
        self.user_mode = "report_based" if self.loaded_style.strip() else "text_based"

    @rx.event
    async def delete_conversation_by_id(self, session_id: str):
        self._bind_log(conversation_id=session_id)
        await asyncio.to_thread(db.delete_conversation, session_id, self._emp_no)
        log.info("보고서 대화 삭제 emp_no=%s session=%s", self._emp_no, session_id)
        if session_id == self.session_id:
            await self.start_new_chat()
        else:
            await self._load_conversation_list()

    async def _persist_turn(self):
        """이번 턴에서 확정된 새 메시지를 DB 에 append (flow_state 는 저장 안 함).

        background task(send_message)에서 호출되므로 state 접근은 async with self
        안에서만 하고, blocking DB 호출은 락 밖에서 실행한다. 대화 목록 갱신은
        foreground 전용 _load_conversation_list(중첩 락 위험) 대신 여기서 직접 반영한다.
        """
        async with self:
            if not self.messages:
                return
            emp_no = self._emp_no
            session_id = self.session_id
            template_id = self.template_id
            persisted_before = self._persisted_count
            title = next(
                (m.content[:30] for m in self.messages if m.role == "user"), "새 보고서"
            )
            # 첨부가 있는 사용자 메시지는 사전발급 msg_id 로 저장 → ChatMessageAttachment 매핑 연결
            pending = [
                (
                    "outline" if m.is_outline else m.role,
                    m.content, m.model_name, m.input_tokens, m.output_tokens,
                    (m.msg_id or None),
                )
                for m in self.messages[persisted_before:]
            ]

        # 부분 실패 안전: 저장 성공한 개수만큼만 _persisted_count 를 올려 중복 append 를 막는다.
        # 헤더 저장이 실패하면 아무것도 커밋하지 않고 다음 턴에 통째로 재시도(카운트 유지).
        saved = 0
        try:
            await asyncio.to_thread(
                db.save_conversation, emp_no, session_id, title, template_id
            )
            for role, content, model_name, in_tok, out_tok, msg_id in pending:
                await asyncio.to_thread(
                    db.append_message, session_id, role, content, emp_no,
                    model_name=model_name,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    msg_id=msg_id,
                )
                saved += 1
        except Exception:
            log.exception(
                "보고서 대화 저장 실패 emp_no=%s session=%s (%d/%d 저장됨)",
                emp_no, session_id, saved, len(pending),
            )

        try:
            rows = await asyncio.to_thread(db.list_conversations, emp_no)
        except Exception:
            log.exception("대화 목록 조회 실패 emp_no=%s", emp_no)
            rows = None

        async with self:
            self._persisted_count = persisted_before + saved
            if rows is not None:
                self.conversation_list = [
                    ConvSummary(id=r["id"], title=r["title"], created_at=r["created_at"],
                                created_label=r.get("created_label", ""),
                                template_id=r.get("template_id", ""))
                    for r in rows
                ]

    # ══════════════════════════════════════════════════════════
    # 스타일 학습 (업로드 → 분석 → AgentCore/S3 저장)
    # ══════════════════════════════════════════════════════════
    @rx.event
    async def on_styles_registered(self, keys: list[str]):
        """업로드된 참고 문서를 목록에 등록만 한다(추출은 '스타일 추출' 버튼에서 별도 실행).

        등록 = S3 에 파일이 올라가 목록에 뜨는 것. 스타일 반영(추출)은 사용자가 명시적으로
        '스타일 추출'을 눌러야 일어난다(업데이트성 동작).
        """
        self._bind_log()
        valid = [k for k in (keys or []) if storage.owns_key(k, self._emp_no, self.template_id)]
        if not valid:
            log.warning("스타일 등록 key 소유권 불일치 emp_no=%s keys=%s", self._emp_no, keys)
            return rx.toast.error("잘못된 파일 참조입니다.")
        await self._load_style_docs()
        self.style_upload_status = f"{len(valid)}개 문서를 등록했습니다. '스타일 추출'을 눌러 반영하세요."
        return rx.toast.success("문서를 등록했습니다. '스타일 추출'로 스타일에 반영하세요.")

    @rx.event(background=True)
    async def extract_pending_styles(self):
        """등록된 문서 중 '미추출' 문서만 추출해 정본 스타일에 병합(사용자 클릭 업데이트).

        이미 반영된 문서는 건너뛰어 중복 병합을 막는다. 추출 완료 문서는 마커에 기록돼
        이후 '추출됨'으로 표시된다.
        """
        self._bind_log()
        async with self:
            emp_no, template = self._emp_no, self.template_id
            pending = [d["key"] for d in self.style_docs
                       if not d.get("extracted") and storage.owns_key(d["key"], emp_no, template)]
            if not pending:
                self.style_upload_status = "추출할 새 문서가 없습니다."
                return
            self.is_streaming = True

        def worker(key: str) -> None:
            path = storage.download_to_temp(key)
            try:
                doc = style.extract_doc_style(path)
                analyzed = style.analyze_style_with_claude(doc)
                desc = style.build_style_desc(doc, analyzed)
                # 단일 편집기 모델: 추출본을 정본 스타일에 병합(기존 편집 내용 위에 얹힘)
                memory.add_doc_style(emp_no, template, desc)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        total = len(pending)
        extracted_now: list[str] = []
        for i, key in enumerate(pending, 1):
            async with self:
                self.style_upload_status = f"스타일 추출 중... ({i}/{total})"
            try:
                await asyncio.to_thread(worker, key)
                extracted_now.append(os.path.basename(key))
            except Exception:
                log.exception("스타일 추출 실패 key=%s", key)

        async with self:
            # 추출 완료 문서를 마커에 누적(중복 방지)
            if extracted_now:
                prev = await asyncio.to_thread(storage.load_extracted_docs, emp_no, template) or []
                merged = list(dict.fromkeys([*prev, *extracted_now]))
                await asyncio.to_thread(storage.save_extracted_docs, emp_no, template, merged)
            # 추출 후 리로드 — 방금 병합한 정본을 그대로 읽는다(LLM 없음).
            self.loaded_style = await asyncio.to_thread(
                memory.load_style, self._emp_no, self.template_id
            )
            self.user_mode = "report_based" if self.loaded_style.strip() else "text_based"
            # 단일 편집기: 추출본이 병합된 정본을 편집 필드에 그대로 반영
            self.edited_style = self.loaded_style
            await self._load_style_docs()
            done = len(extracted_now)
            if done == total:
                self.style_upload_status = f"스타일 추출 완료 ({done}개 반영)"
            elif done > 0:
                self.style_upload_status = f"{done}/{total}개 반영 (일부 실패)"
            else:
                self.style_upload_status = "스타일 추출에 실패했습니다. 다시 시도해주세요."
            self.is_streaming = False
        log.info(
            "스타일 추출 emp_no=%s template=%s (%d/%d 반영)",
            emp_no, template, len(extracted_now), total,
        )

    @rx.event
    async def on_topic_uploaded(self, file_no: int, filename: str = ""):
        """정식 등록된 주제 첨부(file_no)의 원본을 받아 텍스트를 추출해 다음 전송에 합친다.

        파일 자체는 이미 DB(atch_file_m)에 기록되어 재조회·다운로드 가능하다.
        말풍선엔 파일명만 표시하고 추출 텍스트는 LLM 입력에만 쓴다.
        """
        if not attachment_service.verify_ownership(file_no, self._emp_no):
            log.warning("주제 첨부 소유권 불일치 emp_no=%s file_no=%s", self._emp_no, file_no)
            yield rx.toast.error("잘못된 파일 참조입니다.")
            return
        self.is_streaming = True
        yield

        def worker() -> str:
            data = attachment_service.download_original_bytes(file_no)
            if not data:
                return ""
            fd, path = tempfile.mkstemp(suffix=Path(filename).suffix.lower(), prefix="rptmk_topic_")
            os.close(fd)
            try:
                with open(path, "wb") as f:
                    f.write(data)
                if style.is_image_file(path):
                    return style.extract_text_from_image(path)
                return style.extract_plain_text(path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        try:
            text = await asyncio.to_thread(worker)
            self._uploaded_topic_text = text or ""
            self.pending_topic_file = filename or "첨부 파일"
            self.pending_topic_file_no = file_no
            self.is_streaming = False
            yield rx.toast.success(f"첨부됨: {self.pending_topic_file}")
        except Exception:
            log.exception("주제 첨부 처리 실패 file_no=%s", file_no)
            self.is_streaming = False
            yield rx.toast.error("첨부 파일 처리에 실패했습니다.")

    @rx.event
    def register_style_docs(self):
        """참고 문서(다중) 선택 → 순차 업로드(JS) → 콜백에서 목록에 '등록'(추출은 별도)."""
        if not self.template_id:
            return rx.toast.error("먼저 보고서 유형을 선택하세요.")
        return rx.call_script(
            f"reportMakerPickAndUploadMany({json.dumps(self.template_id)}, 'style')",
            callback=ReportMakerState.on_style_result,
        )

    @rx.event
    def on_style_result(self, results: list):
        """다중 업로드 결과([{key,filename,error}, ...]) → 유효 key 만 모아 목록 등록."""
        keys = [r["key"] for r in (results or []) if r and r.get("key")]
        if not keys:
            errors = [r["error"] for r in (results or []) if r and r.get("error")]
            if errors:
                return rx.toast.error(errors[0])
            return
        return ReportMakerState.on_styles_registered(keys)

    @rx.event
    def pick_and_upload_topic(self):
        """주제 첨부 선택 → 정식 등록(JS→API) → 콜백에서 추출 트리거.

        첨부-메시지 매핑을 위해 msg_id 를 미리 발급해 업로드 시 함께 보낸다.
        전송 시 이 msg_id 로 사용자 메시지를 저장하면 첨부가 연결된다.
        """
        if not self.template_id:
            return rx.toast.error("먼저 보고서 유형을 선택하세요.")
        self._pending_msg_id = uuid.uuid4().hex[:50]
        return rx.call_script(
            f"reportMakerPickAndUpload({json.dumps(self.template_id)}, 'topic', "
            f"{json.dumps(self.session_id)}, {json.dumps(self._pending_msg_id)})",
            callback=ReportMakerState.on_topic_result,
        )

    @rx.event
    def on_topic_result(self, result: dict):
        if not result or not result.get("file_no"):
            if result and result.get("error"):
                return rx.toast.error(result["error"])
            return
        return ReportMakerState.on_topic_uploaded(
            int(result["file_no"]), result.get("filename", "")
        )

    @rx.event
    async def clear_pending_topic(self):
        """첨부 취소(전송 전) — 등록된 첨부(고아)도 함께 삭제."""
        if self.pending_topic_file_no:
            await asyncio.to_thread(
                attachment_service.delete_attachment, self.pending_topic_file_no, self._emp_no
            )
        self._uploaded_topic_text = ""
        self.pending_topic_file = ""
        self.pending_topic_file_no = 0
        self._pending_msg_id = ""

    @rx.event
    def download_attachment(self, file_no: int):
        """말풍선 첨부 다운로드(공용 /api/download/{file_no} 프록시 경유)."""
        if not file_no:
            return
        return rx.call_script(build_download_script(file_no))

    # ══════════════════════════════════════════════════════════
    # 스타일 편집
    # ══════════════════════════════════════════════════════════
    @rx.event
    def set_edited_style(self, value: str):
        """세부 조정(manual) 편집기(controlled textarea) on_change 핸들러."""
        self.edited_style = value

    async def _load_style_docs(self):
        """참고 문서 목록 + 문서별 추출 상태(extracted) 로드.

        추출 마커가 아직 없으면(구데이터/최초 진입): 이미 정본 스타일이 있으면 기존 문서는
        '반영됨'으로 간주해 마커를 초기화(중복 병합 방지), 정본이 비어 있으면 모두 '미추출'.
        """
        emp_no, template = self._emp_no, self.template_id
        keys = await asyncio.to_thread(storage.list_style_docs, emp_no, template)
        extracted = await asyncio.to_thread(storage.load_extracted_docs, emp_no, template)
        if extracted is None:
            existing = await asyncio.to_thread(storage.load_combined_style, emp_no, template)
            extracted = [os.path.basename(k) for k in keys] if (existing.strip() and keys) else []
            await asyncio.to_thread(storage.save_extracted_docs, emp_no, template, extracted)
        extracted_set = set(extracted)
        self.style_docs = [
            {"name": storage.style_doc_name(k), "key": k,
             "extracted": os.path.basename(k) in extracted_set}
            for k in keys
        ]

    @rx.var
    def pending_extract_count(self) -> int:
        """아직 스타일에 반영되지 않은(미추출) 등록 문서 수."""
        return sum(1 for d in self.style_docs if not d.get("extracted"))

    @rx.event
    async def load_style_editor(self):
        """스타일 편집 페이지 on_load.

        단일 편집기 모델: 문서 추출본과 수동 편집이 하나의 정본으로 통합돼 있어, 정본
        전체를 편집 필드에 로드한다(사용자가 직접 수정·저장). 문서 목록은 참고용으로 표시.
        """
        auth = await self.get_state(AuthState)
        self._emp_no = auth.current_emp_no
        if not self._emp_no:
            return rx.redirect("/login")
        if not self.template_id:
            # 보고서 유형(세션) 없이 진입 → 메인으로 돌려보냄
            return rx.redirect("/ai-services/report-generator")
        self._bind_log()
        # 이전 방문의 등록/추출 상태 문구 초기화(재진입 시 stale 표시 방지)
        self.style_upload_status = ""
        # 정본 스타일 전체를 편집 필드로 (정본이 있으면 그대로, legacy 는 1회 정규화 이관)
        self.edited_style = await asyncio.to_thread(
            memory.load_style, self._emp_no, self.template_id
        )
        await self._load_style_docs()

    @rx.event
    async def save_edited_style(self, form_data: dict):
        """작성 스타일 저장 — 편집 텍스트로 정본 전체를 덮어쓴다. 빈 값도 허용(스타일 없음)."""
        self._bind_log()
        edited = (form_data.get("edited_style") or "").strip()
        self.is_streaming = True
        yield
        await asyncio.to_thread(memory.set_style, self._emp_no, self.template_id, edited)
        log.info(
            "작성 스타일 저장 emp_no=%s template=%s (%d자)",
            self._emp_no, self.template_id, len(edited),
        )
        # 세션의 스타일 갱신(생성에 반영)
        self.loaded_style = edited
        self.edited_style = edited
        self.user_mode = "report_based" if edited else "text_based"
        self.is_streaming = False
        yield rx.toast.success("작성 스타일 저장 완료")

    @rx.event
    async def delete_style_doc(self, key: str):
        """추출 문서 하나 삭제 — 원본 파일/목록만 정리. 정본 스타일 텍스트는 유지된다."""
        self._bind_log()
        if not storage.owns_key(key, self._emp_no, self.template_id):
            yield rx.toast.error("잘못된 파일 참조입니다.")
            return
        self.is_streaming = True
        yield
        bn = os.path.basename(key)
        await asyncio.to_thread(memory.delete_doc, self._emp_no, self.template_id, bn)
        # 추출 마커에서도 제거(있을 때만)
        prev = await asyncio.to_thread(storage.load_extracted_docs, self._emp_no, self.template_id) or []
        if bn in prev:
            await asyncio.to_thread(
                storage.save_extracted_docs, self._emp_no, self.template_id,
                [x for x in prev if x != bn],
            )
        await self._load_style_docs()
        self.is_streaming = False
        # 단일 편집기 모델: 삭제는 목록만 정리하고, 스타일 텍스트는 편집기에서 직접 조정.
        yield rx.toast.success("문서를 목록에서 삭제했습니다. (작성 스타일 텍스트는 유지됩니다)")

    @rx.event
    async def reset_style(self):
        """작성 스타일 초기화 — AgentCore 기록 + S3 스타일 파일 삭제."""
        self._bind_log()
        self.is_streaming = True
        yield
        await asyncio.to_thread(memory.clear_style, self._emp_no, self.template_id)
        self.loaded_style = ""
        self.edited_style = ""
        self.user_mode = "text_based"
        await self._load_style_docs()
        self.is_streaming = False
        # 초기화는 그 자체로 영속 완료 — 별도 '저장'이 필요 없음을 알려 흐름 혼선을 없앤다.
        yield rx.toast.success("작성 스타일을 초기화했습니다.")

    # ══════════════════════════════════════════════════════════
    # UI 토글 / 유틸
    # ══════════════════════════════════════════════════════════
    @rx.event
    def toggle_guide(self):
        """시작 화면 '상세 작성 가이드' 펼치기/접기."""
        self.show_guide = not self.show_guide

    @rx.event
    def copy_message(self, idx: int):
        if not (0 <= idx < len(self.messages)):
            return
        content = self.messages[idx].content
        escaped = content.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        return rx.call_script(f"navigator.clipboard.writeText(`{escaped}`)")

    @rx.event
    async def save_outline_style(self, idx: int):
        """생성된 아웃라인의 스타일을 학습해 이 템플릿의 작성 스타일로 저장(+세션 즉시 반영).

        legacy 규약: 아웃라인을 style_desc 로 변환해 선호(preference)로 저장하고,
        self.loaded_style·user_mode 를 즉시 갱신한다(저장 후 편집기·후속 생성에 동기화).
        (기존 구현은 원문 아웃라인만 저장하고 세션 상태를 갱신하지 않아 동기화가 안 됐음.)
        """
        self._bind_log()
        if not (0 <= idx < len(self.messages)):
            return
        content = self.messages[idx].content
        self.is_streaming = True
        yield
        style_desc = await asyncio.to_thread(style.style_desc_from_outline, content)
        if not style_desc.strip():
            self.is_streaming = False
            yield rx.toast.error("스타일 저장에 실패했습니다. 다시 시도해주세요.")
            return
        await asyncio.to_thread(memory.save_preference, self._emp_no, self.template_id, style_desc)
        self.loaded_style = style_desc
        self.user_mode = "report_based"
        self.messages[idx] = self.messages[idx].copy(update={"style_saved": True})
        self.is_streaming = False
        yield rx.toast.success("현재 스타일을 저장했습니다.")

    # ══════════════════════════════════════════════════════════
    # 슬라이드 미리보기 (아웃라인 → 결정적 파서 → 타입 태깅 → SK 렌더)
    # ══════════════════════════════════════════════════════════
    @rx.event(background=True)
    async def open_slides(self, idx: int):
        """아웃라인 메시지를 SK 슬라이드로 렌더해 전체화면 미리보기로 연다.

        내용은 결정적 파서(slides.parse_outline)가 무손실 트리로 만들고, LLM 은 영역별
        컴포넌트 타입만 태깅한다(내용 변형 0). 동일 원문은 해시 캐시로 재생성을 건너뛴다.
        """
        self._bind_log()
        async with self:
            if not (0 <= idx < len(self.messages)):
                return
            md = self.messages[idx].content
            src_hash = hashlib.sha1(md.encode("utf-8")).hexdigest()
            cached = (src_hash == self._slides_hash and bool(self.slides_src))
            self.show_slides = True
            self.slides_loading = not cached

        if cached:
            return

        def render() -> str:
            # LLM 이 레이아웃·시각화를 판단해 슬라이드 HTML 생성(브랜드 셸로 감쌈).
            # 실패/비정상 출력 시 결정적 렌더로 폴백해 항상 결과를 낸다.
            try:
                html_out = slides.render_html_llm(md)
            except Exception:
                log.exception("LLM 슬라이드 생성 실패 — 결정적 렌더 폴백 idx=%s", idx)
                html_out = slides.build_deck(md)
            # iframe src 로 앱 CSS 와 격리해 안전하게 렌더(대용량·따옴표 걱정 없이)
            return "data:text/html;charset=utf-8," + urllib.parse.quote(html_out)

        try:
            src = await asyncio.to_thread(render)
        except Exception:
            log.exception("슬라이드 렌더 실패 idx=%s", idx)
            async with self:
                self.slides_loading = False
                self.show_slides = False
            yield rx.toast.error("슬라이드 렌더에 실패했습니다. 다시 시도해주세요.")
            return

        async with self:
            self.slides_src = src
            self._slides_hash = src_hash
            self.slides_loading = False
        log.info("슬라이드 렌더 emp_no=%s template=%s session=%s",
                 self._emp_no, self.template_id, self.session_id)

    @rx.event
    def close_slides(self):
        self.show_slides = False

    # ══════════════════════════════════════════════════════════
    # 메인 입력 처리
    # ══════════════════════════════════════════════════════════
    @rx.event(background=True)
    async def send_message(self, form_data: dict):
        # 메인 챗과 동일 패턴: background task 로 실행해 스트리밍 flush 마다
        # (async with self 경계에서) 프론트로 delta 를 push 한다. foreground 핸들러는
        # 응답이 끝날 때까지 락을 잡아 중간 갱신이 화면에 반영되지 않는다.
        self._bind_log()   # 턴 단위 로그 상관관계(emp/conv/req) 바인딩
        async with self:
            typed = (form_data.get("message") or "").strip()
            if not typed or self.is_streaming:
                return
            # 첨부 추출 텍스트는 LLM 입력에만 합치고, 말풍선엔 사용자가 친 글 + 파일칩만 표시.
            # 첨부가 있으면 사전발급 msg_id 를 메시지에 부여해 첨부(ChatMessageAttachment)와 연결.
            llm_text = typed
            file_name = ""
            file_no = 0
            msg_id = ""
            if self._uploaded_topic_text or self.pending_topic_file_no:
                llm_text = f"{self._uploaded_topic_text}\n[추가지시]\n{typed}" if self._uploaded_topic_text else typed
                file_name = self.pending_topic_file
                file_no = self.pending_topic_file_no
                msg_id = self._pending_msg_id
                self._uploaded_topic_text = ""
                self.pending_topic_file = ""
                self.pending_topic_file_no = 0
                self._pending_msg_id = ""
            self.messages.append(ReportMessage(
                role="user", content=typed, file_name=file_name, file_no=file_no, msg_id=msg_id,
            ))
        try:
            await self._route(llm_text)
        except Exception:
            log.exception("메시지 처리 실패")
            async with self:
                self.is_streaming = False
                self.messages.append(
                    ReportMessage(content="처리 중 오류가 발생했습니다. 다시 시도해주세요.")
                )
        finally:
            await self._persist_turn()

    async def _route(self, user_input: str):
        async with self:
            stage = self.flow_stage
            has_outline = bool(self.outline)
        if stage == "await_page_count":
            await self._handle_page_count(user_input)
        elif stage == "await_clarify":
            await self._handle_flow_clarify(user_input)
        elif stage == "await_deep_info":
            await self._handle_deep_info(user_input)
        elif stage == "await_struct_info":
            await self._handle_struct_info(user_input)
        elif stage == "await_outline_info":
            await self._handle_outline_info(user_input)
        else:
            intent = await asyncio.to_thread(self._classify_intent, user_input, has_outline)
            if intent == "edit" and has_outline:
                await self._edit_outline(user_input)
            elif intent == "outline":
                async with self:
                    self.outline = ""
                    self.iteration = 0
                await self._start_flow(user_input)
            else:
                await self._handle_general_chat(user_input)

    # ── STEP 1: 주제 분석 ──
    async def _start_flow(self, topic: str):
        async with self:
            self.is_streaming = True
            self.messages.append(ReportMessage(content="분석 중...", is_loading=True))
            loaded_style = self.loaded_style
            report_based = self.user_mode == "report_based"

        a = await asyncio.to_thread(analysis.analyze_topic, topic, loaded_style, report_based)
        if not a:
            async with self:
                self.messages[-1] = ReportMessage(content="분석에 실패했습니다. 다시 시도해주세요.")
                self.is_streaming = False
            return

        blocks = a.get("storyline_blocks", []) or []
        block_lines = []
        for i, b in enumerate(blocks, 1):
            nm = (b.get("name", "") if isinstance(b, dict) else str(b)).strip()
            dt = (b.get("detail", "") if isinstance(b, dict) else "").strip()
            block_lines.append(f"  {i}. {nm}" + (f" — {dt}" if dt else ""))
        blocks_text = "\n".join(block_lines)

        async with self:
            self.pending_topic = topic
            self.report_type = a.get("report_type", "")
            self.report_type_name = a.get("report_type_name", "")
            _mode = a.get("mode", "")
            self.report_mode = _mode if _mode in ("summary", "deep") else "deep"
            self.report_storyline = a.get("storyline", "")
            self.report_storyline_blocks = blocks_text

            self.flow_analysis = (
                f"목적: {a.get('purpose','')}\n현재 상태: {a.get('current_state','')}\n"
                f"핵심 메시지: {a.get('key_message','')}\n논리 흐름: {a.get('storyline','')}"
                + (f"\n논리 블록:\n{blocks_text}" if blocks_text else "")
            )
            self.page_options = a.get("page_options", [])
            self.recommended_pages = a.get("recommended_pages", 0)
            opts = "\n".join(f"* {o.get('label','')}" for o in self.page_options)
            mode_label = "서머리" if self.report_mode == "summary" else "심층 보고"

            self.flow_stage = "await_page_count"
            content = (
                "## **파악한 내용**\n"
                f"- 목적: {a.get('purpose','')}\n- 현재 상태: {a.get('current_state','')}\n"
                f"- 핵심 메시지: {a.get('key_message','')}\n"
                + (f"- 제안 흐름:\n{md_linebreaks(blocks_text)}\n\n" if blocks_text
                   else f"- 제안 흐름: {md_linebreaks(a.get('storyline',''))}\n\n")
                + f"이 내용은 **{self.report_type_name} ({mode_label})** 보고서가 적합해 보입니다.\n"
                "보고 유형을 바꾸고 싶으면 알려주세요.\n"
                "* 추천 (적정 분량을 제가 정해 드립니다)\n"
                f"{opts}\n\n"
                "몇 장 구조로 작성할까요? 정해진 분량이 있으면 골라주시고, 없으면 **추천**이라고 답해 주세요.\n\n"
                + md_linebreaks(MODE_INTRO)
            )
            idx = len(self.messages) - 1

        # 블로킹 JSON 결과를 초안과 동일한 타이핑 UX 로 점진 노출(추가 호출 없음)
        await self._reveal_into(idx, content)
        async with self:
            # 노출 완료본 확정(타이핑 중 partial 마크다운 → 최종본 고정) + 흐름 메시지 표시
            self.messages[idx] = self.messages[idx].copy(
                update={"content": content, "is_flow": True, "is_loading": False}
            )
            self.is_streaming = False

    # ── STEP 2: 페이지 수 → 정보 게이트 → 구조 제안 ──
    async def _handle_page_count(self, user_input: str):
        _u = user_input.strip()
        used_recommend = False

        async with self:
            self.is_streaming = True
            self.messages.append(ReportMessage(content="구조 설계 중...", is_loading=True))
            idx = len(self.messages) - 1
            if any(k in _u for k in ("추천", "알아서", "정해줘", "적당", "골라")):
                used_recommend = True
                pages_list = [float(o.get("pages", 0)) for o in self.page_options if o.get("pages") is not None]
                rec = None
                try:
                    cand_rec = float(self.recommended_pages)
                    if cand_rec in pages_list:
                        rec = cand_rec
                except (TypeError, ValueError):
                    rec = None
                if rec is None:
                    cand = sorted(p for p in pages_list if p >= 1) or sorted(pages_list)
                    rec = cand[0] if cand else None
                self.page_count = rec if rec is not None else 1
            else:
                self.page_count = parse_page_count(user_input)

            # 페이지 수 답변에 흐름/순서 조정 지시가 섞여 있으면 스토리라인·분석에 반영
            # (legacy _handle_page_count 동작 보존 — 이 단계의 흐름 지시가 유실되지 않도록).
            if len(_u) > 8 and any(
                k in _u for k in ("흐름", "순서", "스토리", "먼저", "강조", "빼", "추가", "바꿔")
            ):
                self.report_storyline = (self.report_storyline + f"\n[사용자 흐름 조정] {_u}").strip()
                self.flow_analysis = self.flow_analysis + f"\n[사용자 흐름 조정] {_u}"

            chosen_label = self._chosen_label()
            # 락 밖 blocking 호출용 스냅샷
            pending_topic = self.pending_topic
            flow_analysis = self.flow_analysis
            report_type_name = self.report_type_name
            page_count = self.page_count
            report_mode = self.report_mode
            loaded_style = self.loaded_style
            report_based = self.user_mode == "report_based"
            report_storyline = self.report_storyline
            report_storyline_blocks = self.report_storyline_blocks
            try:
                rec = float(self.recommended_pages)
            except (TypeError, ValueError):
                rec = 0

        # 정보 충실도 게이트 (심층 · 3장 이상 · 추천보다 많이 고른 경우)
        if (report_mode == "deep" and float(page_count) >= 3
                and (rec <= 0 or float(page_count) > rec)):
            check = await asyncio.to_thread(
                structure.check_page_info, pending_topic, flow_analysis, page_count, rec
            )
            questions = (check or {}).get("questions", [])
            if not (check or {}).get("sufficient", True) and questions:
                async with self:
                    self.pending_questions = questions
                    self.struct_gate_total = len(questions)
                    self.gate_asked_questions = list(questions)
                    self.outline_reasked = False
                    q_text = "  \n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
                    rec_phrase = f"적정 분량은 {int(rec)}장 내외이며, " if rec > 0 else ""
                    gate_content = (
                        f"{rec_phrase}{int(float(self.page_count))}장을 채우려면 아래 정보가 더 필요합니다.\n\n"
                        f"{q_text}\n\n---\n답을 입력해 주세요. 모르는 항목은 'TBD', 지금 정보로 진행하려면 '진행'."
                    )
                    self.flow_stage = "await_struct_info"
                await self._reveal_into(idx, gate_content)
                async with self:
                    self.is_streaming = False
                return

        proposal = await asyncio.to_thread(
            structure.propose_structure, pending_topic, flow_analysis,
            report_type_name, page_count, report_mode, chosen_label,
            loaded_style, report_based, report_storyline, report_storyline_blocks,
        )
        proposed_structure = proposal["structure"]

        # 서머리 다장: 심층 정보 게이트 블록
        if report_mode == "summary" and float(page_count) >= 2:
            async with self:
                need_deep = not self.deepdive_targets
            if need_deep:
                check = await asyncio.to_thread(
                    structure.check_deepdive_info, pending_topic, flow_analysis,
                    proposed_structure, page_count, chosen_label,
                )
                async with self:
                    self.deepdive_targets = ", ".join(check.get("deep_targets", [])) if check else "done"
                deep_qs = (check or {}).get("questions", []) if not (check or {}).get("sufficient", True) else []
                if deep_qs:
                    block = ("\n\n**!!  심층 보고 작성을 위해 추가 정보가 필요합니다 (반드시 입력)**\n"
                             + "\n".join(f"{i}. {q}" for i, q in enumerate(deep_qs, 1)))
                    proposed_structure = proposed_structure + block

        async with self:
            self.proposed_structure = proposed_structure
            self.pending_questions = extract_questions(self.proposed_structure)
        await self._apply_grounding()
        async with self:
            self.outline_reasked = False
            rec_note = (f"적정 분량으로 **{fmt_pages(self.page_count)}장**을 추천드립니다. 다른 분량을 원하시면 알려주세요.\n\n"
                        if used_recommend else "")
            struct_content = (
                rec_note + md_linebreaks(self.proposed_structure)
                + "\n\n---\n이 구조로 진행할까요? 수정할 점이 있으면 알려주세요."
            )
            self.flow_stage = "await_clarify"
        await self._reveal_into(idx, struct_content)
        async with self:
            self.is_streaming = False

    async def _apply_grounding(self):
        """근거 검증 패스: 구조가 요구하나 입력에 근거 없는 항목을 질문으로 추가."""
        async with self:
            asked = list(self.pending_questions or []) + list(self.gate_asked_questions or [])
            pending_topic = self.pending_topic
            flow_analysis = self.flow_analysis
            proposed_structure = self.proposed_structure
        try:
            ground_qs = await asyncio.to_thread(
                structure.check_grounding, pending_topic, flow_analysis,
                proposed_structure, asked,
            )
        except Exception:
            log.exception("[근거검증] 실패(무시)")
            ground_qs = []
        async with self:
            ground_qs = [q for q in (ground_qs or []) if q and q not in (self.pending_questions or [])]
            if ground_qs:
                self.pending_questions = list(self.pending_questions or []) + ground_qs
                self.proposed_structure = structure.merge_ground_questions(self.proposed_structure, ground_qs)

    def _chosen_label(self) -> str:
        for o in self.page_options:
            try:
                if float(o.get("pages", 0)) == float(self.page_count):
                    return o.get("label", "")
            except (TypeError, ValueError):
                continue
        return ""

    # ── STEP 3: 구조 확인/수정 ──
    async def _handle_flow_clarify(self, user_input: str):
        import re
        _u = user_input.strip()
        page_change = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(?:장|페이지|쪽)(?:으로|로)?\s*(?:바꿔|바꿔줘|해줘|변경|줄여|늘려|해)?\s*", _u
        )

        async with self:
            self.is_streaming = True
            do_repropose = False
            if page_change:
                new_pc = float(page_change.group(1))
                if new_pc != float(self.page_count):
                    self.page_count = new_pc
                    do_repropose = True
            if do_repropose:
                self.messages.append(ReportMessage(content="구조 설계 중...", is_loading=True))
                idx = len(self.messages) - 1
                snap = (
                    self.pending_topic, self.flow_analysis, self.report_type_name,
                    self.page_count, self.report_mode, self._chosen_label(),
                    self.loaded_style, self.user_mode == "report_based",
                    self.report_storyline, self.report_storyline_blocks,
                )

        if do_repropose:
            proposal = await asyncio.to_thread(structure.propose_structure, *snap)
            async with self:
                self.proposed_structure = proposal["structure"]
                repropose_content = (
                    f"**{fmt_pages(self.page_count)}장** 구조로 다시 제안드립니다.\n\n"
                    + md_linebreaks(proposal["structure"])
                    + "\n\n---\n이 구조로 진행할까요? 수정할 점이 있으면 알려주세요."
                )
                self.flow_stage = "await_clarify"
            await self._reveal_into(idx, repropose_content)
            async with self:
                self.is_streaming = False
            return

        async with self:
            self.proposed_structure += f"\n[추가 요청] {user_input}"
            self.pending_topic += f"\n[추가 정보] {user_input}"
            need_remaining = bool(self.pending_questions) and _u not in _SKIP_WORDS
            pending_questions = list(self.pending_questions)
        if need_remaining:
            pending_questions = await asyncio.to_thread(
                structure.remaining_questions, pending_questions, user_input
            )
            async with self:
                self.pending_questions = pending_questions

        async with self:
            reask = (not self.outline_reasked) and bool(self.pending_questions)
            if reask:
                q_text = "  \n".join(f"{i}. {q}" for i, q in enumerate(self.pending_questions, 1))
                confirm_content = (
                    "아웃라인 작성 전 마지막으로 확인합니다. 정보를 입력하시면 반영하고, "
                    "'진행'이라고 하시면 해당 항목은 (TBD)로 두고 작성합니다.\n\n" + q_text
                )
                self.messages.append(ReportMessage(content="확인 중...", is_loading=True))
                idx = len(self.messages) - 1
                self.outline_reasked = True
                self.flow_stage = "await_outline_info"
            else:
                self.flow_stage = ""
        if reask:
            await self._reveal_into(idx, confirm_content)
            async with self:
                self.is_streaming = False
            return
        await self._run_build()

    async def _handle_deep_info(self, user_input: str):
        async with self:
            self.is_streaming = True
            self.proposed_structure += f"\n[심층 과제 추가정보] {user_input}"
            self.pending_topic += f"\n[심층 과제 추가정보] {user_input}"
            self.flow_stage = ""
        await self._run_build()

    async def _handle_struct_info(self, user_input: str):
        _u = user_input.strip()
        skip = _u in _SKIP_WORDS
        async with self:
            self.is_streaming = True
            if not skip:
                self.pending_topic += f"\n[추가 정보] {_u}"
                self.flow_analysis += f"\n[추가 정보] {_u}"
            need_remaining = (not skip) and bool(self.pending_questions)
            pending_questions = list(self.pending_questions)
        if need_remaining:
            pending_questions = await asyncio.to_thread(
                structure.remaining_questions, pending_questions, _u
            )
            async with self:
                self.pending_questions = pending_questions

        async with self:
            total = self.struct_gate_total or 0
            remaining = len(self.pending_questions)
            answered_ratio = 0.0 if (skip or total <= 0) else (total - remaining) / total
            try:
                rec = float(self.recommended_pages)
            except (TypeError, ValueError):
                rec = 0
            shortage_note = ""
            if answered_ratio < 0.5 and rec > 0 and rec < float(self.page_count):
                self.page_count = rec
                # 별도 스피너를 또 띄우지 않고 구조 제안 메시지 앞에 붙여 함께 타이핑 노출
                shortage_note = (
                    f"입력하신 정보가 요청하신 분량을 채우기에 부족하여, 적정 분량인 "
                    f"**{fmt_pages(rec)}장**으로 구조를 제안드립니다.\n\n"
                )
            self.messages.append(ReportMessage(content="구조 설계 중...", is_loading=True))
            idx = len(self.messages) - 1
            snap = (
                self.pending_topic, self.flow_analysis, self.report_type_name,
                self.page_count, self.report_mode, self._chosen_label(),
                self.loaded_style, self.user_mode == "report_based",
                self.report_storyline, self.report_storyline_blocks,
            )

        proposal = await asyncio.to_thread(structure.propose_structure, *snap)
        async with self:
            self.proposed_structure = proposal["structure"]
            self.pending_questions = extract_questions(self.proposed_structure)
        await self._apply_grounding()
        async with self:
            self.outline_reasked = False
            struct_content = (
                shortage_note
                + md_linebreaks(proposal["structure"])
                + "\n\n---\n이 구조로 진행할까요? 수정할 점이 있으면 알려주세요."
            )
            self.flow_stage = "await_clarify"
        await self._reveal_into(idx, struct_content)
        async with self:
            self.is_streaming = False

    async def _handle_outline_info(self, user_input: str):
        _u = user_input.strip()
        async with self:
            self.is_streaming = True
            need_remaining = _u not in _SKIP_WORDS and bool(self.pending_questions)
            if _u not in _SKIP_WORDS:
                self.pending_topic += f"\n[추가 정보] {_u}"
                self.flow_analysis += f"\n[추가 정보] {_u}"
            pending_questions = list(self.pending_questions)
        if need_remaining:
            pending_questions = await asyncio.to_thread(
                structure.remaining_questions, pending_questions, _u
            )
            async with self:
                self.pending_questions = pending_questions
        async with self:
            self.flow_stage = ""
        await self._run_build()

    # ── 청크 스트리밍 헬퍼 ──
    async def _stream_into(self, idx: int, prompt: str, max_tokens: int, display, usage_out=None) -> str:
        """stream_model 을 시간 배치(STREAM_FLUSH_INTERVAL_SEC)로 messages[idx] 에 반영.

        토큰마다가 아니라 ~80ms 간격으로 묶어 flush 한다(락·네트워크 폭주 방지, WellBot
        메인 챗과 동일 패턴). background task 에서 호출되므로 flush 마다 `async with self`
        경계에서 프론트로 delta 를 push 하고, blocking 스트림 소비는 락 밖에서 한다.
        누적 원문(raw)을 반환하므로 호출측은 그 값으로 후처리한다.
        """
        raw = ""
        last_flush = time.monotonic()
        async for delta in adrain_generator(
            lambda: bedrock.stream_model(prompt, max_tokens, usage_out=usage_out)
        ):
            raw += delta
            now = time.monotonic()
            if now - last_flush >= STREAM_FLUSH_INTERVAL_SEC:
                async with self:
                    self.messages[idx] = self.messages[idx].copy(update={"content": display(raw), "is_loading": False})
                last_flush = now
        # 마지막 잔여 flush (경계 이후 남은 텍스트 반영)
        async with self:
            self.messages[idx] = self.messages[idx].copy(update={"content": display(raw)})
        return raw

    async def _reveal_into(self, idx: int, content: str) -> None:
        """완성된 문자열을 초안 스트리밍과 동일한 시간 배치로 점진 노출(타이핑 효과).

        분석은 구조화 JSON(블로킹)이라 토큰 스트리밍이 불가능하다. 이미 조립된 요약
        마크다운을 STREAM_FLUSH_INTERVAL_SEC 간격으로 잘라 messages[idx] 에 누적 반영해,
        초안 단계와 같은 '타이핑되는' UX 로 보이게 한다(추가 LLM 호출 없음). 첫 flush 에서
        is_loading 을 내려 스피너를 걷고 마크다운 렌더로 전환한다. background task 에서
        호출되므로 flush 마다 `async with self` 경계로 프론트에 push 한다.
        """
        n = len(content)
        if n == 0:
            return
        step = max(1, (n + _REVEAL_FLUSHES - 1) // _REVEAL_FLUSHES)
        shown = 0
        while shown < n:
            shown = min(n, shown + step)
            async with self:
                self.messages[idx] = self.messages[idx].copy(
                    update={"content": content[:shown], "is_loading": False}
                )
            if shown < n:
                await asyncio.sleep(STREAM_FLUSH_INTERVAL_SEC)

    # ── STEP 4: 아웃라인 빌드 (스트리밍) ──
    async def _run_build(self):
        async with self:
            self.is_streaming = True
            self.messages.append(ReportMessage(content="초안 생성 중...", is_loading=True))
            prompt = build.build_outline_prompt(
                self.pending_topic, self.loaded_style,
                strip_question_block(self.proposed_structure), self.report_type,
                self.report_type_name, self.page_count, self.report_mode,
                self.report_storyline, self.report_storyline_blocks,
                self.pending_questions, self.user_mode == "report_based",
            )
            idx = len(self.messages) - 1
        usage: dict = {}
        raw = await self._stream_into(
            idx, prompt, get_config().max_tokens_outline, md_linebreaks, usage_out=usage
        )
        if not raw.strip():
            async with self:
                self.messages[idx] = ReportMessage(content="생성에 실패했습니다. 다시 시도해주세요.")
                self.is_streaming = False
            return
        final = build.finalize_outline(raw)
        async with self:
            self.outline = final
            self.iteration = 0
            self.edit_instructions = []
            self.messages[idx] = ReportMessage(
                content=md_linebreaks(final)
                + "\n\n---\n수정 요청사항을 입력하거나, 현재 스타일을 저장하세요.",
                is_outline=True,
                model_name=get_config().model_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            self.is_streaming = False
            log.info(
                "보고서 초안 생성 emp_no=%s template=%s session=%s "
                "mode=%s pages=%s tokens(in=%s,out=%s)",
                self._emp_no, self.template_id, self.session_id, self.report_mode,
                self.page_count, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            )

    # ── 편집 루프 (스트리밍) ──
    async def _edit_outline(self, feedback: str):
        async with self:
            self.is_streaming = True
            self.messages.append(ReportMessage(content="아웃라인 수정 중...", is_loading=True))
            self.edit_instructions = self.edit_instructions + [feedback.strip()]
            prompt = build.edit_outline_prompt(
                self.outline, feedback, self.report_mode,
                self.page_count, self.edit_instructions, self.pending_questions,
            )
            idx = len(self.messages) - 1
        usage: dict = {}
        raw = await self._stream_into(
            idx, prompt, get_config().max_tokens_outline, md_linebreaks, usage_out=usage
        )
        if not raw.strip():
            async with self:
                self.messages[idx] = ReportMessage(content="수정에 실패했습니다. 다시 시도해주세요.")
                self.is_streaming = False
            return
        final = build.finalize_edit(raw)
        async with self:
            self.outline = final
            self.iteration += 1
            self.messages[idx] = ReportMessage(
                content=md_linebreaks(final) + f"\n\n수정 완료 (#{self.iteration})",
                is_outline=True,
                model_name=get_config().model_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            self.is_streaming = False
            log.info(
                "보고서 수정 emp_no=%s template=%s session=%s iter=%d "
                "tokens(in=%s,out=%s)",
                self._emp_no, self.template_id, self.session_id, self.iteration,
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            )

    # ── 의도 분류 (동기, to_thread 로 호출) ──
    # background task 의 워커 스레드에서 실행되므로 self.outline 을 직접 읽지 않고
    # 호출측(_route)이 락 안에서 스냅샷한 has_outline 을 인자로 받는다.
    def _classify_intent(self, user_input: str, has_outline: bool) -> str:
        prompt = (
            "사용자 입력을 분류하세요.\n\n"
            f"[현재 상태] 아웃라인 {'보유 중' if has_outline else '없음'}\n\n"
            f"[사용자 입력]\n{user_input}\n\n"
            "분류 기준:\n"
            "- outline : 새로운 주제/토픽 제시. 아웃라인 없음 상태에서 내용이 들어오면 거의 항상 outline.\n"
            "- edit    : 아웃라인 보유 중 + 수정 요청(수정·바꿔·추가·삭제·줄여·늘려) 또는 수치·일정·근거 등 새 값 보완.\n"
            "- chat    : 아웃라인 보유 중 + 내용을 바꾸지 않고 묻기만 하는 후속 질문, 또는 무관한 잡담.\n"
            "outline, edit, chat 중 하나만 출력하세요."
        )
        result = bedrock.call_model(prompt, 5000).strip().lower()
        if "edit" in result:
            return "edit"
        if "chat" in result:
            return "chat"
        return "outline"

    async def _handle_general_chat(self, user_input: str):
        async with self:
            self.is_streaming = True
            self.messages.append(ReportMessage(content="답변 생성 중...", is_loading=True))
            context = ""
            if self.loaded_style:
                context += "[사용자 문서 스타일]\n" + self.loaded_style + "\n\n"
            if self.pending_topic:
                context += "[사용자가 입력한 원본 내용]\n" + self.pending_topic + "\n\n"
            if self.outline:
                context += "[현재 아웃라인]\n" + self.outline + "\n\n"
            prompt = (
                context + "[사용자 질문]\n" + user_input + "\n\n"
                "사용자의 질문에 친절하고 자연스럽게 답변하세요. 아웃라인 내용의 출처를 물으면 "
                "[원본 내용]과 대조해 정직하게 답하세요(입력에 있으면 '입력하신 내용', 없으면 보고서 구성을 위해 작성한 부분).\n"
            )
            idx = len(self.messages) - 1
        usage: dict = {}
        raw = await self._stream_into(idx, prompt, 5000, lambda t: t, usage_out=usage)
        async with self:
            self.messages[idx] = ReportMessage(
                content=raw.strip() or "답변을 생성하지 못했습니다.",
                model_name=get_config().model_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            self.is_streaming = False

    # ══════════════════════════════════════════════════════════
    # 로그아웃
    # ══════════════════════════════════════════════════════════
    @rx.event
    async def logout(self):
        auth = await self.get_state(AuthState)
        self._reset_conversation()
        self.session_ready = False
        self.template_id = ""
        self.template_display = ""
        self._emp_no = ""
        return AuthState.logout
