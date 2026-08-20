"""KB 정리(teardown) 화면 State.

admin '지식베이스' 탭의 정리 섹션 전용. 세 가지 비가역 삭제를 다룬다.

    개인 KB 정리   — 퇴사자 등. KB·DS·벡터 인덱스·S3·DB 행 전부
    팀 KB 정리     — 팀 해체 등. 같은 범위
    공용 폴더 삭제 — 대분류(=Data Source) 하나. 벡터는 재색인으로 빼낸다

실제 삭제 순서와 그 이유는 `kb_cleanup_service` 가 갖는다. 여기서는 화면 상태만
관리한다 — 대상 지정, 미리보기, 확인 타이핑, 단계별 진행 표시.

되돌릴 수 없는 작업이라 세 겹으로 막는다.
    ① 이벤트마다 DB ADMIN 세션 재검증
    ② **미리보기 없이는 실행 버튼이 열리지 않는다** — 무엇이 지워지는지 본 뒤에만
    ③ 대상 이름을 그대로 타이핑해야 버튼이 활성화된다(오타·오선택 방어)

진행 표시:
    서비스가 단계별 제너레이터를 돌려주므로 `next()` 를 한 번씩 스레드에서 돌려
    받는 즉시 화면에 붙인다. DS 삭제 대기·재색인 대기가 각각 수십 초에서 수 분이라,
    한꺼번에 모아 받으면 화면이 멈춘 것처럼 보인다.
"""

from __future__ import annotations

import asyncio
import logging

import reflex as rx
from pydantic import BaseModel

from wellbot.services.knowledgebase import kb_cleanup_service, shared_kb_service
from wellbot.services.knowledgebase.config import reload_kb_config

log = logging.getLogger(__name__)

MODE_PERSONAL = "personal"
MODE_TEAM = "team"
MODE_FOLDER = "folder"

_MODE_LABELS = {
    MODE_PERSONAL: "개인 KB 정리",
    MODE_TEAM: "팀 KB 정리",
    MODE_FOLDER: "공용 폴더 삭제",
}
_TARGET_LABELS = {
    MODE_PERSONAL: "사번",
    MODE_TEAM: "부서 코드",
    MODE_FOLDER: "대분류",
}

_ADMIN_ONLY = "DB ADMIN 계정으로 로그인해야 KB 를 정리할 수 있습니다."

# 단계 상태 → 표시(아이콘/색). 서비스의 STEP_* 값을 화면 어휘로 옮긴다.
_STATUS_VIEW = {
    kb_cleanup_service.STEP_DONE: ("check", "green", "완료"),
    kb_cleanup_service.STEP_SKIPPED: ("minus", "gray", "해당 없음"),
    kb_cleanup_service.STEP_FAILED: ("x", "red", "실패"),
}


class CleanupStepRow(BaseModel):
    """진행 표시용 한 줄."""

    name: str = ""
    detail: str = ""
    label: str = ""
    icon: str = "check"
    color: str = "gray"


class PreviewRow(BaseModel):
    """미리보기 항목 (라벨 - 값)."""

    label: str = ""
    value: str = ""


def _plan_for(mode: str, target: str):
    """대상 스냅샷 수집. 블로킹(S3·DB·Bedrock) — 스레드에서 호출한다."""
    reload_kb_config()          # CLI 가 바꾼 레지스트리를 반영 (별도 프로세스라 캐시 밖)
    if mode == MODE_FOLDER:
        return kb_cleanup_service.gather_folder_resources(target)
    return kb_cleanup_service.gather_resources(mode, target)


def _preview_rows(mode: str, plan) -> list[PreviewRow]:
    """계획을 화면 항목으로. 숫자는 '무엇이 사라지는지'를 그대로 보여준다."""
    if mode == MODE_FOLDER:
        return [
            PreviewRow(label="Data Source", value=plan.data_source_id or "없음"),
            PreviewRow(label="S3 문서", value=f"{len(plan.keys)}개"),
            PreviewRow(label="S3 중간 산출물", value=f"{len(plan.intermediate_keys)}개"),
            PreviewRow(label="문서 속성(티어·부서)", value=f"{len(plan.doc_keys)}건"),
            PreviewRow(label="벡터", value="재색인으로 제거 (인덱스는 공유라 유지)"),
        ]
    return [
        PreviewRow(label="Knowledge Base", value=plan.kb_id or "없음"),
        PreviewRow(label="Data Source", value=plan.data_source_id or "없음"),
        PreviewRow(label="벡터 인덱스", value=plan.vector_index or "없음"),
        PreviewRow(label="S3 원본/변환본", value=f"{len(plan.main_keys)}개"),
        PreviewRow(label="S3 중간 산출물", value=f"{len(plan.intermediate_keys)}개"),
        PreviewRow(label="DB 행", value=f"{plan.db_row_count}건"),
    ]


def _step_row(step) -> CleanupStepRow:
    icon, color, label = _STATUS_VIEW.get(step.status, ("info", "gray", step.status))
    return CleanupStepRow(
        name=step.name, detail=step.detail, label=label, icon=icon, color=color,
    )


class KbCleanupState(rx.State):
    """KB 정리 화면 상태."""

    mode: str = MODE_PERSONAL
    target: str = ""
    folder_options: list[str] = []

    # 미리보기 — preview_target 이 현재 target 과 다르면 낡은 것으로 본다
    preview: list[PreviewRow] = []
    preview_target: str = ""
    nothing_to_delete: bool = False

    confirm_text: str = ""

    steps: list[CleanupStepRow] = []
    running: bool = False
    finished: bool = False
    failed: bool = False

    loading_preview: bool = False
    error: str = ""

    # ──────────────────────────────────────────
    # 권한 / 표시
    # ──────────────────────────────────────────
    async def _is_db_admin(self) -> bool:
        from wellbot.state.auth_state import AuthState

        auth = await self.get_state(AuthState)
        return bool(auth.is_authenticated and auth.current_user_role == "ADMIN")

    @rx.var
    def mode_label(self) -> str:
        return _MODE_LABELS.get(self.mode, "")

    @rx.var
    def target_label(self) -> str:
        return _TARGET_LABELS.get(self.mode, "대상")

    @rx.var
    def is_folder_mode(self) -> bool:
        return self.mode == MODE_FOLDER

    @rx.var
    def has_preview(self) -> bool:
        """현재 대상에 대한 미리보기가 있는지. 대상을 고치면 무효가 된다."""
        return bool(self.preview) and self.preview_target == self.target.strip()

    @rx.var
    def can_delete(self) -> bool:
        """실행 버튼 활성 조건 — 미리보기 + 이름 일치 + 지울 것이 있음 + 실행 중 아님."""
        return (
            self.has_preview
            and not self.nothing_to_delete
            and not self.running
            and self.confirm_text.strip() == self.target.strip()
            and bool(self.target.strip())
        )

    @rx.var
    def confirm_hint(self) -> str:
        return f"실행하려면 '{self.target.strip()}' 을 그대로 입력하세요."

    # ──────────────────────────────────────────
    # 대상 선택
    # ──────────────────────────────────────────
    def _reset_progress(self) -> None:
        self.preview = []
        self.preview_target = ""
        self.nothing_to_delete = False
        self.confirm_text = ""
        self.steps = []
        self.finished = False
        self.failed = False
        self.error = ""

    async def open(self, mode: str):
        """좌측 레일에서 정리 항목을 고를 때. 우측 화면을 정리 모드로 바꾼다."""
        from wellbot.state.kb_admin_state import KbAdminState

        admin = await self.get_state(KbAdminState)
        admin.view = "cleanup"

        self.mode = mode
        self.target = ""
        self._reset_progress()
        if mode == MODE_FOLDER:
            # 공용 폴더는 목록에서 고르게 한다 — 오타로 엉뚱한 대상을 지우지 않도록.
            try:
                self.folder_options = await asyncio.to_thread(_folder_options)
            except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
                log.exception("공용 폴더 목록 조회 실패")
                self.error = f"폴더 목록 조회 실패: {exc}"

    def set_target(self, value: str) -> None:
        self.target = value
        # 대상이 바뀌면 앞서 본 미리보기와 확인 입력은 의미가 없다
        self._reset_progress()

    def set_confirm_text(self, value: str) -> None:
        self.confirm_text = value

    # ──────────────────────────────────────────
    # 미리보기
    # ──────────────────────────────────────────
    async def load_preview(self):
        """무엇이 지워지는지 먼저 보여준다. 이걸 통과하지 않으면 실행 버튼이 안 열린다."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        target = self.target.strip()
        if not target:
            self.error = f"{self.target_label}를 입력해 주세요."
            return

        self.loading_preview = True
        self.error = ""
        self.steps = []
        self.finished = False
        self.failed = False
        yield

        try:
            plan = await asyncio.to_thread(_plan_for, self.mode, target)
        except ValueError as exc:
            self.loading_preview = False
            self.error = str(exc)
            return
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("정리 대상 조회 실패: mode=%s target=%s", self.mode, target)
            self.loading_preview = False
            self.error = f"대상 조회 실패: {exc}"
            return

        self.preview = _preview_rows(self.mode, plan)
        self.preview_target = target
        self.nothing_to_delete = plan.has_nothing_to_delete
        self.confirm_text = ""
        self.loading_preview = False

    # ──────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────
    @rx.event(background=True)
    async def run_cleanup(self):
        """단계별로 실행하며 결과를 즉시 화면에 붙인다.

        제너레이터를 `next()` 한 번씩 스레드에서 돌린다 — 한꺼번에 모아 받으면
        DS 삭제·재색인 대기 동안(수십 초~수 분) 화면이 멈춘 것처럼 보인다.
        background 이벤트라 그 사이에도 다른 사용자의 요청이 막히지 않는다.
        """
        async with self:
            if not (self.has_preview and self.confirm_text.strip() == self.target.strip()):
                self.error = "미리보기와 확인 입력을 먼저 완료해 주세요."
                return
            mode, target = self.mode, self.target.strip()
            self.running = True
            self.finished = False
            self.failed = False
            self.steps = []
            self.error = ""

        try:
            # 실행 직전에 대상을 다시 수집한다 — 미리보기 이후 바뀐 것이 있으면
            # 그 최신 상태를 지운다(오래된 스냅샷으로 지우면 새로 생긴 객체가 남는다).
            plan = await asyncio.to_thread(_plan_for, mode, target)
            if mode == MODE_FOLDER:
                steps = kb_cleanup_service.execute_folder_cleanup(plan)
            else:
                steps = kb_cleanup_service.execute_cleanup(plan)

            while True:
                step = await asyncio.to_thread(next, steps, None)
                if step is None:
                    break
                async with self:
                    self.steps = self.steps + [_step_row(step)]
                    if step.is_failed:
                        self.failed = True
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("KB 정리 실패: mode=%s target=%s", mode, target)
            async with self:
                self.running = False
                self.finished = True
                self.failed = True
                self.error = f"정리 중 오류: {exc}"
            return

        log.info("KB 정리 종료: mode=%s target=%s failed=%s", mode, target, self.failed)
        async with self:
            self.running = False
            self.finished = True
            # 실행 뒤 상태가 바뀌었으므로 미리보기를 무효화한다(재실행 전 다시 확인).
            self.preview_target = ""
            self.confirm_text = ""


def _folder_options() -> list[str]:
    """등록된 공용 대분류 목록. 설정을 다시 읽어 CLI 변경도 반영한다."""
    reload_kb_config()
    return sorted(shared_kb_service.list_folders())
