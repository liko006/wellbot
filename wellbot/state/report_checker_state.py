"""보고서 오류 검출 State.

흐름:
  1. pick_file → 브라우저 파일 선택 (reportPickFile)
  2. start_analysis → 업로드 (reportUpload) → job_id 수신
  3. analyze (background) → S3 원본 다운로드 → 페이지 추출 → 분석(진행률 통지)
     → 결과 HTML S3 저장 → presigned 다운로드 URL 발급 → 결과 State 반영
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path

import reflex as rx

from wellbot.logger import log_context
from wellbot.services.report_checker import db, storage
from wellbot.services.report_checker.config import get_config
from wellbot.services.report_checker.models import (
    AnalysisCancelled,
    ProgressEvent,
    Usage,
    UserDictionary,
)
from wellbot.services.report_checker.pdf_extract import extract_pages_from_bytes
from wellbot.services.report_checker.pipeline import run_analysis
from wellbot.services.report_checker.report_html import generate_html
from wellbot.state.auth_state import AuthState
from wellbot.state.report_checker_scripts import build_report_download_script

log = logging.getLogger(__name__)

# 스텝퍼 순서 (건너뛴 단계가 있어도 비교는 이 인덱스로 일관되게 동작)
_STAGE_ORDER = {
    "parsing": 0,
    "typo": 1,
    "attention": 2,
    "notation": 3,
    "consistency": 4,
    "done": 5,
}

# job_id → 취소 신호 Event. State 는 스레드 Event 를 var 로 못 들고 있으므로
# (같은 프로세스) 모듈 레지스트리로 백그라운드 워커와 취소 이벤트를 연결한다.
_CANCEL_EVENTS: dict[str, threading.Event] = {}


class ReportCheckerState(rx.State):
    """보고서 오류 검출 페이지 상태."""

    # ── 입력 ──
    pending_file_name: str = ""
    pending_file_size: int = 0
    exclusions_text: str = ""       # 제외어 (콤마/줄바꿈 구분)
    aliases_text: str = ""          # 동일 항목 별칭 (한 줄 = 한 묶음, 콤마 구분)
    watch_items_text: str = ""      # 주의 항목 (한 줄 = 한 규칙)
    include_consistency: bool = False  # 값 일관성 검사 포함 여부 (기본: 오탈자만)
    include_notation: bool = False  # 표기 일관성 검사 포함 여부
    ran_consistency: bool = True    # 이번 결과에 값 일관성 검사가 실제 수행됐는지
    watch_active: bool = False      # 이번 결과에 주의 항목 검사가 수행됐는지
    notation_active: bool = False   # 이번 결과에 표기 일관성 검사가 수행됐는지

    # ── 진행 ──
    status: str = "idle"        # idle | uploading | analyzing | done | error
    stage: str = ""             # parsing | typo | attention | consistency | done
    stage_index: int = 0        # 단계 순서 인덱스 (스텝퍼 상태 계산용)
    stage_current: int = 0      # 현재 단계의 진행 (예: 청크 3/5 의 3)
    stage_total: int = 0        # 현재 단계의 총량 (예: 청크 3/5 의 5)
    progress_pct: int = 0
    progress_detail: str = ""
    typo_count: int = 0
    consistency_count: int = 0
    cancel_requested: bool = False   # 중단 요청됨 (현재 단계 완료 후 정지)
    error_message: str = ""

    # ── 결과 ──
    job_id: str = ""
    source_file_name: str = ""
    _emp_no: str = ""   # 백엔드 전용 — 업로드 시 인증 세션에서 확보 (클라이언트 비노출)
    typo_errors: list[dict] = []
    consistency_errors: list[dict] = []
    attention_errors: list[dict] = []
    attention_count: int = 0
    notation_errors: list[dict] = []
    notation_count: int = 0
    download_ready: bool = False
    download_filename: str = ""

    # ── 파생 ──
    @rx.var
    def is_running(self) -> bool:
        return self.status in ("uploading", "analyzing")

    @rx.var
    def has_file(self) -> bool:
        return bool(self.pending_file_name)

    @rx.var
    def has_result(self) -> bool:
        return self.status == "done"

    @rx.var
    def total_errors(self) -> int:
        return (
            self.typo_count
            + self.consistency_count
            + self.attention_count
            + self.notation_count
        )

    @rx.var
    def file_size_label(self) -> str:
        if self.pending_file_size <= 0:
            return ""
        mb = self.pending_file_size / (1024 * 1024)
        return f"{mb:.1f} MB"

    # ── 입력 세터 ──
    def set_exclusions_text(self, value: str) -> None:
        self.exclusions_text = value

    def set_aliases_text(self, value: str) -> None:
        self.aliases_text = value

    def set_watch_items_text(self, value: str) -> None:
        self.watch_items_text = value

    def set_include_consistency(self, value: bool) -> None:
        self.include_consistency = value

    def set_include_notation(self, value: bool) -> None:
        self.include_notation = value

    # ── 파일 선택 ──
    def pick_file(self):
        return rx.call_script(
            "reportPickFile()", callback=ReportCheckerState.on_file_picked
        )

    def on_file_picked(self, meta):
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = None
        if not meta:
            return
        self.pending_file_name = meta.get("name", "")
        self.pending_file_size = int(meta.get("size", 0) or 0)

    # ── 분석 시작 ──
    def start_analysis(self):
        if not self.has_file:
            return rx.toast.error("PDF 파일을 먼저 선택해주세요.")
        self.status = "uploading"
        self.stage = ""
        self.cancel_requested = False
        self.error_message = ""
        self.typo_errors = []
        self.consistency_errors = []
        self.attention_errors = []
        self.notation_errors = []
        self.typo_count = 0
        self.consistency_count = 0
        self.attention_count = 0
        self.notation_count = 0
        self.download_ready = False
        self.download_filename = ""
        self.progress_pct = 0
        self.stage_index = 0
        self.stage_current = 0
        self.stage_total = 0
        self.progress_detail = "업로드 중..."
        return rx.call_script(
            "reportUpload()", callback=ReportCheckerState.on_uploaded
        )

    async def on_uploaded(self, result):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {"error": result}
        if not result or result.get("error") or not result.get("job_id"):
            self.status = "error"
            self.error_message = (result or {}).get("error") or "업로드에 실패했습니다."
            return
        # 인증 세션에서 emp_no 확보 (일반 이벤트라 get_state 자유롭게 가능).
        # S3 키가 업로드 엔드포인트가 쓴 경로와 일치하도록 백엔드에 보관.
        auth = await self.get_state(AuthState)
        self._emp_no = auth.current_emp_no
        self.job_id = result["job_id"]
        self.source_file_name = result.get("filename") or self.pending_file_name
        self.status = "analyzing"
        self.stage = "parsing"
        self.progress_detail = "PDF 분석 준비 중..."
        return ReportCheckerState.analyze

    def _parse_dictionary(self) -> UserDictionary:
        exclusions = [t.strip() for t in re.split(r"[\n,]", self.exclusions_text) if t.strip()]
        groups: list[list[str]] = []
        for line in self.aliases_text.splitlines():
            terms = [t.strip() for t in line.split(",") if t.strip()]
            if len(terms) >= 2:  # 별칭은 최소 2개 표기가 있어야 의미
                groups.append(terms)
        watch = [ln.strip() for ln in self.watch_items_text.splitlines() if ln.strip()]
        return UserDictionary(
            exclusions=exclusions, alias_groups=groups, watch_items=watch
        )

    def _apply_progress(self, evt: ProgressEvent) -> None:
        self.stage = evt.stage
        self.stage_index = _STAGE_ORDER.get(evt.stage, self.stage_index)
        self.stage_current = evt.current
        self.stage_total = evt.total
        if evt.detail:
            self.progress_detail = evt.detail
        if evt.typo_count:
            self.typo_count = evt.typo_count
        if evt.consistency_count:
            self.consistency_count = evt.consistency_count
        if evt.attention_count:
            self.attention_count = evt.attention_count
        if evt.notation_count:
            self.notation_count = evt.notation_count
        total = evt.total or 1
        # 활성 단계들에 5→100% 구간을 균등 배분 (오탈자는 항상 포함).
        active = ["typo"]
        if self.watch_active:
            active.append("attention")
        if self.notation_active:
            active.append("notation")
        if self.include_consistency:
            active.append("consistency")
        if evt.stage == "parsing":
            self.progress_pct = 5
        elif evt.stage == "done":
            self.progress_pct = 100
        elif evt.stage in active:
            span = 95 / len(active)
            idx = active.index(evt.stage)
            self.progress_pct = min(
                99, int(5 + span * idx + span * (evt.current / total))
            )

    @rx.event(background=True)
    async def analyze(self):
        """S3 원본을 읽어 분석 실행. 진행률은 큐로 받아 UI 갱신."""
        async with self:
            emp_no = self._emp_no
            job_id = self.job_id
            source_name = self.source_file_name
            dictionary = self._parse_dictionary()
            do_consistency = self.include_consistency
            do_notation = self.include_notation
            watch_active = bool(dictionary.watch_items)
            self.watch_active = watch_active
            self.notation_active = do_notation

        if not emp_no or not job_id:
            async with self:
                self.status = "error"
                self.error_message = "세션 정보를 확인할 수 없습니다. 다시 로그인해주세요."
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel_event = threading.Event()
        _CANCEL_EVENTS[job_id] = cancel_event

        cfg = get_config()
        # Usage 를 여기서 만들어 run_analysis 에 주입 → 취소/에러로 중단돼도
        # 그때까지의 부분 토큰을 이 참조로 읽어 로깅할 수 있다.
        usage = Usage()
        job_stats = {"pages": 0}
        started = time.perf_counter()

        def on_progress(evt: ProgressEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, evt)

        def worker():
            data = storage.download_source(emp_no, job_id)
            pages = extract_pages_from_bytes(data)
            if not pages:
                raise RuntimeError("PDF 에서 텍스트를 추출하지 못했습니다. 스캔본 PDF 는 지원하지 않습니다.")
            job_stats["pages"] = len(pages)
            on_progress(
                ProgressEvent(
                    stage="parsing",
                    detail=f"{len(pages)}개 페이지 추출 완료",
                    total=len(pages),
                )
            )
            return run_analysis(
                pages,
                dictionary,
                on_progress,
                do_consistency=do_consistency,
                do_notation=do_notation,
                cancel_check=cancel_event.is_set,
                usage=usage,
            )

        def emit_usage_log(status: str, result=None) -> None:
            """완료/취소/에러 공통 사용량 로그 — 모니터링 분리 집계 소스."""
            log_context.bind(emp_no=emp_no)
            log.info(
                f"report_checker {status}",
                extra={
                    "service": "report_checker",
                    "status": status,
                    "agnt_id": cfg.agent_id,
                    "model": cfg.model_id,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    "llm_calls": usage.calls,
                    "pages": job_stats["pages"],
                    "typo_count": len(result.typo_errors) if result else 0,
                    "consistency_count": len(result.consistency_errors) if result else 0,
                    "attention_count": len(result.attention_errors) if result else 0,
                    "notation_count": len(result.notation_errors) if result else 0,
                    "consistency_checked": do_consistency,
                    "notation_checked": do_notation,
                    "attention_checked": watch_active,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
            )

        async def persist_usage(status: str, result=None) -> None:
            """로그 + DB 원장 기록(chtb 재활용). DB 는 블로킹이라 스레드로."""
            emit_usage_log(status, result)
            if result is not None:
                summary = (
                    f"오탈자 {len(result.typo_errors)} · 표기 {len(result.notation_errors)} · "
                    f"값 {len(result.consistency_errors)} · 주의 {len(result.attention_errors)}건 "
                    f"(페이지 {job_stats['pages']})"
                )
            else:
                summary = f"분석 {status} (페이지 {job_stats['pages']})"
            await asyncio.to_thread(
                db.record_usage,
                emp_no=emp_no,
                agent_id=cfg.agent_id,
                source_file=source_name,
                model_name=cfg.model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                elapsed_sec=time.perf_counter() - started,
                status=status,
                summary=summary,
            )
        try:
            task = asyncio.create_task(asyncio.to_thread(worker))
            try:
                while not (task.done() and queue.empty()):
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=0.15)
                    except asyncio.TimeoutError:
                        continue
                    async with self:
                        self._apply_progress(evt)
                result = await task
            except AnalysisCancelled:
                log.info("report_checker 분석 중단 job_id=%s", job_id)
                await persist_usage("cancelled")
                async with self:
                    self.status = "cancelled"
                    self.stage = ""
                    self.cancel_requested = False
                    self.progress_detail = "분석이 중단되었습니다."
                return
            except Exception as e:  # noqa: BLE001 - UI 에 표면화
                log.exception("report_checker 분석 실패 job_id=%s", job_id)
                await persist_usage("failed")
                async with self:
                    self.status = "error"
                    self.cancel_requested = False
                    self.error_message = str(e)
                return
        finally:
            _CANCEL_EVENTS.pop(job_id, None)

        # 결과 HTML 생성 + S3 저장 + 다운로드 URL (블로킹은 스레드로)
        html = generate_html(
            result,
            source_name,
            consistency_checked=do_consistency,
            attention_checked=watch_active,
            notation_checked=do_notation,
        )
        stem = Path(source_name).stem or "report"
        filename = f"{stem}_검출결과.html"

        try:
            await asyncio.to_thread(storage.save_result_html, emp_no, job_id, html)
            download_ready = True
        except Exception:  # noqa: BLE001
            log.exception("report_checker 결과 저장 실패 job_id=%s", job_id)
            download_ready = False

        # 사용량 로그 + DB 원장 기록 (완료)
        await persist_usage("done", result)

        async with self:
            self.typo_errors = [e.to_dict() for e in result.typo_errors]
            # 중첩 foreach 를 피하기 위해 표시용 문자열을 미리 합쳐 둔다.
            self.consistency_errors = [
                {
                    **e.to_dict(),
                    "values_str": " vs ".join(str(v) for v in e.values),
                    "pages_str": ", ".join(f"{p}p" for p in sorted(e.pages)),
                }
                for e in result.consistency_errors
            ]
            self.typo_count = len(result.typo_errors)
            self.consistency_count = len(result.consistency_errors)
            self.ran_consistency = do_consistency
            self.attention_errors = [e.to_dict() for e in result.attention_errors]
            self.attention_count = len(result.attention_errors)
            self.notation_errors = [e.to_dict() for e in result.notation_errors]
            self.notation_count = len(result.notation_errors)
            self.download_filename = filename
            self.download_ready = download_ready
            self.status = "done"
            self.stage = "done"
            self.progress_pct = 100
            self.progress_detail = "분석 완료"

    def request_cancel(self):
        """분석 중단 요청 — 현재 단계 완료 후 다음 단계 진입 전에 정지."""
        if self.status != "analyzing":
            return
        self.cancel_requested = True
        self.progress_detail = "중단 요청됨 — 현재 단계 완료 후 정지합니다..."
        ev = _CANCEL_EVENTS.get(self.job_id)
        if ev is not None:
            ev.set()

    def download_result(self):
        """결과 HTML 다운로드 (백엔드 프록시 경유 → a[download])."""
        if not self.download_ready or not self.job_id:
            return rx.toast.error("다운로드할 결과가 없습니다.")
        return rx.call_script(
            build_report_download_script(self.job_id, self.download_filename)
        )

    def reset_checker(self):
        """새 분석을 위해 상태 초기화."""
        self.status = "idle"
        self.stage = ""
        self.pending_file_name = ""
        self.pending_file_size = 0
        self.exclusions_text = ""
        self.aliases_text = ""
        self.watch_items_text = ""
        self.include_consistency = False
        self.include_notation = False
        self.ran_consistency = False
        self.watch_active = False
        self.notation_active = False
        self.cancel_requested = False
        self.progress_pct = 0
        self.stage_index = 0
        self.stage_current = 0
        self.stage_total = 0
        self.progress_detail = ""
        self.typo_count = 0
        self.consistency_count = 0
        self.error_message = ""
        self.job_id = ""
        self.source_file_name = ""
        self.typo_errors = []
        self.consistency_errors = []
        self.attention_errors = []
        self.attention_count = 0
        self.notation_errors = []
        self.notation_count = 0
        self.download_ready = False
        self.download_filename = ""
