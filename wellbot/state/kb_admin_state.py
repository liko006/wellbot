"""공용(회사) KB 관리 화면 State.

admin 페이지 '지식베이스' 탭 전용. 폴더(대분류=Data Source) 생성, 문서 업로드·삭제,
색인 상태 확인, 문서별 권위 티어·담당 부서 설정을 담당한다. 도메인 로직은
`shared_kb_service`(S3·Bedrock)와 `shared_kb_docs`(설정 레지스트리)에 있고, 여기서는
화면 상태와 스레드 오프로드만 관리한다.

접근 통제:
    렌더 게이트(`pages/admin.py`)와 **별개로 모든 이벤트가 첫 줄에서 DB ADMIN 세션을
    재검증**한다. 렌더만 막으면 화면이 안 보이는 상태에서도 이벤트는 부를 수 있다.
    ENV 비밀번호로 들어온 SUPER 는 세션 쿠키가 없어 이 탭을 쓸 수 없다(의도된 정책 —
    공용 KB 는 전사 검색 결과를 바꾸므로 실명 계정에만 허용).

블로킹 IO:
    S3·Bedrock·yaml 접근은 전부 동기 호출이라 `asyncio.to_thread` 로 넘긴다. 이벤트
    루프에서 직접 돌리면 그 시간만큼 **다른 사용자의 채팅 스트리밍이 멈춘다**.
"""

from __future__ import annotations

import asyncio
import json
import logging

import reflex as rx
from pydantic import BaseModel

from wellbot.constants import KB_UPLOAD_MAX_PER_REQUEST
from wellbot.services.knowledgebase import shared_kb_docs, shared_kb_service
from wellbot.services.knowledgebase.config import reload_kb_config
from wellbot.services.knowledgebase.kb_utils import (
    PARSER_AUTO,
    PARSER_LOCAL,
    PARSER_UPSTAGE,
    SHARED_PARSE_POLICY,
)
from wellbot.state.chat_models import PendingFile, format_file_size

log = logging.getLogger(__name__)

# 티어 드롭다운의 '미배정' 항목. rx.select 는 값이 문자열이라 숫자 티어도 문자열로 다룬다.
TIER_NONE = "없음"

# 파서 라디오의 표시 라벨 → ParsePolicy 값. 화면에는 라벨만 다루고 변환은 여기서 한 번만
# 한다(컴포넌트에서 rx.match 로 양방향 매핑하면 읽기 어렵고 기본값이 두 곳에 생긴다).
PARSER_LABELS: dict[str, str] = {
    "자동": PARSER_AUTO,
    "Upstage": PARSER_UPSTAGE,
    "로컬": PARSER_LOCAL,
}
PARSER_LABEL_DEFAULT = "자동"

_ADMIN_ONLY = "DB ADMIN 계정으로 로그인해야 공용 KB 를 관리할 수 있습니다."

_STATUS_LABELS = {
    "STARTING": "시작 중",
    "IN_PROGRESS": "처리 중",
    "COMPLETE": "완료",
    "FAILED": "실패",
    "STOPPED": "중단",
}


class KbAdminFolder(BaseModel):
    """좌측 레일의 폴더 한 줄 (N단계 평탄화)."""

    path: str = ""          # 대분류부터의 논리 경로
    name: str = ""          # 마지막 세그먼트(표시명)
    depth: int = 0
    indent: str = "0em"
    doc_count: int = 0      # 이 폴더 직속 문서 수
    has_children: bool = False   # 하위 폴더 유무 (펼침 토글 표시 여부)


class KbAdminDoc(BaseModel):
    """우측 문서 표의 한 행."""

    path: str = ""          # 논리 경로 = docs 레지스트리 키
    name: str = ""
    parent: str = ""        # 소속 폴더 경로
    uploaded_at: str = ""
    tier: str = TIER_NONE
    dept: str = ""


def _fetch_all() -> tuple[list[dict], list[str], dict]:
    """목록 조회를 한 번의 오프로드로 묶는다 (설정 재읽기 + S3 + 설정 조회).

    설정을 먼저 파일에서 다시 읽는 이유: CLI 는 별도 프로세스라 앱의 설정 캐시를
    갱신할 수 없다. 다시 읽지 않으면 CLI 로 폴더 이름을 바꾼 뒤에도 화면이 옛 이름을
    보여주고, 그 행에서 티어를 바꾸면 사라진 경로로 새 항목이 생긴다.
    """
    reload_kb_config()
    return (
        shared_kb_service.list_tree(),
        sorted(shared_kb_service.list_folders()),
        shared_kb_docs.list_doc_attrs(),
    )


def _parent_of(path: str) -> str:
    """논리 경로의 부모 폴더. 최상위면 빈 문자열."""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _ancestors(path: str) -> list[str]:
    """논리 경로의 조상 폴더 목록. '사규/규정/인사' → ['사규', '사규/규정']."""
    segments = [s for s in path.split("/") if s]
    return ["/".join(segments[:i]) for i in range(1, len(segments))]


def _indent(depth: int) -> str:
    """폴더 줄의 좌측 여백. 기본 여백(0.5em)을 포함해 계산한다 —
    padding_left 가 padding_x 를 덮어쓰므로 여기서 합쳐야 최상위 줄도 여백을 갖는다.
    0.5·1.25 는 이진수 정확값이라 0.5/1.75/3.0em 으로 딱 떨어진다.
    """
    return f"{0.5 + depth * 1.25}em"


def _build_folders(rows: list[dict], registered: list[str]) -> list[KbAdminFolder]:
    """트리 행 + 등록된 대분류를 좌측 레일 목록으로.

    등록만 되고 S3 에 객체가 없는 대분류(방금 만든 폴더)는 트리에 안 나오므로 따로
    합친다 — 안 그러면 폴더를 만들어도 화면에 없어서 업로드 대상으로 고를 수 없다.
    경로 사전순 = 부모가 자식보다 항상 먼저(접두사) + 형제는 이름순이라 트리 순서가 된다.
    """
    counts: dict[str, int] = {}
    for row in rows:
        if not row["is_folder"]:
            parent = _parent_of(row["path"])
            counts[parent] = counts.get(parent, 0) + 1

    tree_folders = [(row["path"], row["name"], row["depth"]) for row in rows if row["is_folder"]]
    known = {path for path, _, _ in tree_folders}
    tree_folders += [(top, top, 0) for top in registered if top not in known]

    # 하위 폴더가 있는지 = 누군가의 부모인지. 토글 표시 여부를 여기서 정해둔다.
    parents = {_parent_of(path) for path, _, _ in tree_folders}

    folders = [
        KbAdminFolder(
            path=path,
            name=name,
            depth=depth,
            indent=_indent(depth),
            doc_count=counts.get(path, 0),
            has_children=path in parents,
        )
        for path, name, depth in tree_folders
    ]
    folders.sort(key=lambda f: f.path)
    return folders


def _build_docs(rows: list[dict], attrs: dict) -> list[KbAdminDoc]:
    """트리의 파일 행에 설정된 티어·부서를 붙여 문서 표 행으로."""
    docs: list[KbAdminDoc] = []
    for row in rows:
        if row["is_folder"]:
            continue
        entry = attrs.get(row["path"]) or {}
        tier = entry.get("tier")
        docs.append(KbAdminDoc(
            path=row["path"],
            name=row["name"],
            parent=_parent_of(row["path"]),
            uploaded_at=row["uploaded_at"],
            tier=TIER_NONE if tier is None else str(tier),
            dept=str(entry.get("dept") or ""),
        ))
    return docs


class KbAdminState(rx.State):
    """공용 KB 관리 탭 상태."""

    # ── 목록 ──
    folders: list[KbAdminFolder] = []
    docs: list[KbAdminDoc] = []
    registered_tops: list[str] = []      # DS 가 있는 대분류(업로드 가능 대상)
    tier_choices: list[str] = []         # [없음, 0, 1, ...] — 설정된 사다리에서
    selected_folder: str = ""
    expanded_folders: list[str] = []     # 펼쳐진 폴더 경로 (기본은 전부 접힘)
    selected_docs: list[str] = []        # 체크된 문서 경로 (일괄 삭제 대상)

    loading: bool = False
    error: str = ""
    success: str = ""

    # 실제 문서가 없는 속성 키 (설정 파일에 남은 유령 항목)
    orphan_doc_keys: list[str] = []
    show_orphan_modal: bool = False

    # ── 폴더 생성 모달 ──
    show_folder_modal: bool = False
    new_folder_name: str = ""
    creating_folder: bool = False

    # ── 업로드 모달 ──
    show_upload_modal: bool = False
    upload_sub: str = ""                 # 소분류(비우면 대분류 직속)
    upload_parser_label: str = PARSER_LABEL_DEFAULT
    pending: list[PendingFile] = []
    upload_phase: str = ""               # "" = 진행 중 아님
    upload_folder: str = ""              # 전송 시작 시점에 고정한 대상 폴더

    # ── 삭제 확인 모달 ──
    show_delete_modal: bool = False
    deleting: bool = False

    # ── 색인 상태 ──
    ingest_label: str = ""
    ingest_detail: str = ""
    ingest_failed: bool = False

    # ──────────────────────────────────────────
    # 권한 / 공통
    # ──────────────────────────────────────────
    async def _is_db_admin(self) -> bool:
        """DB ADMIN 세션인지. ENV 비밀번호(SUPER)는 세션이 없어 False."""
        from wellbot.state.auth_state import AuthState

        auth = await self.get_state(AuthState)
        return bool(auth.is_authenticated and auth.current_user_role == "ADMIN")

    def _clear_messages(self) -> None:
        self.error = ""
        self.success = ""

    @rx.var
    def visible_folders(self) -> list[KbAdminFolder]:
        """조상이 모두 펼쳐진 폴더만. 최상위(대분류)는 항상 보인다.

        Reflex 는 임의 깊이 재귀 렌더가 안 되므로 평탄 목록을 필터해 단일 foreach 로
        그린다 — 채팅 KB 패널(visible_shared_rows)과 같은 방식.
        """
        expanded = set(self.expanded_folders)
        return [
            folder for folder in self.folders
            if all(ancestor in expanded for ancestor in _ancestors(folder.path))
        ]

    @rx.var
    def visible_docs(self) -> list[KbAdminDoc]:
        """선택된 폴더 직속 문서."""
        return [d for d in self.docs if d.parent == self.selected_folder]

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_docs)

    @rx.var
    def has_doc_selection(self) -> bool:
        return bool(self.selected_docs)

    @rx.var
    def all_visible_selected(self) -> bool:
        """헤더 체크박스 상태. 현재 폴더의 문서가 전부 선택됐는지."""
        visible = [d.path for d in self.docs if d.parent == self.selected_folder]
        return bool(visible) and all(path in self.selected_docs for path in visible)

    @rx.var
    def has_orphan_docs(self) -> bool:
        return bool(self.orphan_doc_keys)

    @rx.var
    def orphan_notice(self) -> str:
        return (
            f"실제 문서가 없는 속성 항목이 {len(self.orphan_doc_keys)}건 있습니다. "
            "문서 목록에 표시되지 않아 여기서만 정리할 수 있습니다."
        )

    @rx.var
    def delete_summary(self) -> str:
        """삭제 확인 모달에 보여줄 대상 요약 (길면 뒤를 접는다)."""
        names = [path.rsplit("/", 1)[-1] for path in self.selected_docs]
        if len(names) <= 5:
            return ", ".join(names)
        return ", ".join(names[:5]) + f" 외 {len(names) - 5}건"

    @rx.var
    def selected_top(self) -> str:
        """선택된 폴더의 대분류(= Data Source 단위)."""
        return self.selected_folder.split("/")[0] if self.selected_folder else ""

    @rx.var
    def selected_sub(self) -> str:
        """선택된 폴더의 소분류 경로(대분류 직속이면 빈 문자열)."""
        _, _, sub = self.selected_folder.partition("/")
        return sub

    @rx.var
    def upload_target_label(self) -> str:
        """업로드 모달에 보여줄 최종 경로."""
        sub = self.upload_sub.strip("/ ")
        return f"{self.selected_top}/{sub}" if sub else self.selected_top

    @rx.var
    def pending_label(self) -> str:
        return f"{len(self.pending)}개 선택"

    @rx.var
    def is_busy(self) -> bool:
        """긴 작업 중 — 버튼 중복 클릭을 막는다."""
        return bool(self.upload_phase) or self.creating_folder or self.deleting

    # ──────────────────────────────────────────
    # 목록 로드
    # ──────────────────────────────────────────
    async def load_on_open(self):
        """탭이 화면에 올라올 때마다 다시 읽는다.

        한 번만 읽으면(이전 구현) CLI 나 다른 관리자가 바꾼 내용을 못 본다 — 그 상태에서
        티어를 바꾸면 사라진 경로로 새 항목이 생긴다. 비용은 설정 재읽기 + S3 목록 1회다.
        """
        if self.loading:
            return
        async for _ in self._load():
            yield

    async def reload(self):
        """수동 새로고침."""
        async for _ in self._load():
            yield

    def _apply_lists(self, rows: list[dict], tops: list[str], attrs: dict) -> None:
        """조회 결과를 화면 상태에 반영 (IO 없음).

        조회(오프로드)와 분리해 둔다 — background 이벤트에서는 상태 변경이 반드시
        `async with self:` 안이어야 하므로, 그쪽에서도 이 함수만 락 안에서 부른다.
        """
        self.folders = _build_folders(rows, tops)
        self.docs = _build_docs(rows, attrs)
        self.registered_tops = tops
        self.tier_choices = [TIER_NONE] + [str(t) for t in shared_kb_docs.tier_options()]

        known = {f.path for f in self.folders}
        # 선택했던 폴더가 사라졌으면(문서 전부 삭제 등) 첫 폴더로 되돌린다
        if self.selected_folder not in known:
            self.selected_folder = self.folders[0].path if self.folders else ""
        # 사라진 폴더의 펼침 상태는 버리고, 선택된 폴더는 보이도록 조상을 펼친다
        # (업로드 직후 목록을 다시 그려도 방금 보던 위치가 접히지 않게).
        keep = [p for p in self.expanded_folders if p in known]
        self.expanded_folders = list(dict.fromkeys(keep + _ancestors(self.selected_folder)))
        # 목록이 바뀌면 이전 선택은 의미가 없다(경로가 사라졌을 수 있다)
        self.selected_docs = [
            path for path in self.selected_docs if any(d.path == path for d in self.docs)
        ]

        # 실제 문서가 없는 속성 키(유령 항목) 탐지. 설정 파일에는 참조 무결성이 없어
        # 이런 키가 생길 수 있고(외부 rename 중 편집 등), **문서 목록에 뜨지 않으니
        # 화면에서 지울 방법이 없다** — 여기서 세어 관리자에게 보여준다.
        # 트리에 파일이 하나도 없으면 판단을 보류한다(S3 조회 실패를 전체 유령으로
        # 오인해 전부 지우자고 권하면 안 된다).
        live = {d.path for d in self.docs}
        self.orphan_doc_keys = sorted(attrs) if live else []
        self.orphan_doc_keys = [key for key in self.orphan_doc_keys if key not in live]

    async def _load(self):
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        self.loading = True
        self.error = ""
        yield                                        # 스피너 표시용 중간 렌더

        try:
            rows, tops, attrs = await asyncio.to_thread(_fetch_all)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("공용 KB 목록 조회 실패")
            self.loading = False
            self.error = f"목록 조회 실패: {exc}"
            return

        self._apply_lists(rows, tops, attrs)
        self.loading = False

    def select_folder(self, path: str) -> None:
        self.selected_folder = path
        # 선택은 폴더 단위라 문서 체크도 폴더를 벗어나면 초기화한다 —
        # 안 보이는 문서가 선택된 채로 남으면 삭제 대상이 화면과 어긋난다.
        self.selected_docs = []
        self.ingest_label = ""
        self.ingest_detail = ""
        self.ingest_failed = False
        self._clear_messages()

    def toggle_folder(self, path: str) -> None:
        """펼침/접힘. 기본은 접힘이라 대분류부터 필요한 갈래만 열어 본다."""
        if path in self.expanded_folders:
            # 접을 때는 하위 폴더의 펼침 상태도 같이 정리한다(다시 열면 접힌 상태)
            prefix = f"{path}/"
            self.expanded_folders = [
                p for p in self.expanded_folders
                if p != path and not p.startswith(prefix)
            ]
        else:
            self.expanded_folders = self.expanded_folders + [path]

    # ──────────────────────────────────────────
    # 폴더(대분류) 생성
    # ──────────────────────────────────────────
    def open_folder_modal(self) -> None:
        self.new_folder_name = ""
        self._clear_messages()
        self.show_folder_modal = True

    def close_folder_modal(self) -> None:
        self.show_folder_modal = False

    def set_new_folder_name(self, value: str) -> None:
        self.new_folder_name = value

    async def create_folder(self):
        """대분류 = Bedrock Data Source 1개. 수초 걸려 스피너를 띄운다."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        name = self.new_folder_name.strip().strip("/")
        if not name:
            self.error = "폴더 이름을 입력해 주세요."
            return
        if "/" in name:
            self.error = "대분류 이름에는 '/' 를 쓸 수 없습니다. 소분류는 업로드에서 지정합니다."
            return

        self.creating_folder = True
        self.error = ""
        yield

        try:
            await asyncio.to_thread(shared_kb_service.create_folder, name)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("공용 KB 폴더 생성 실패: %s", name)
            self.creating_folder = False
            self.error = f"폴더 생성 실패: {exc}"
            return

        self.creating_folder = False
        self.show_folder_modal = False
        self.success = f"대분류 '{name}' 을 만들었습니다."
        self.selected_folder = name
        async for _ in self._load():
            yield

    # ──────────────────────────────────────────
    # 업로드
    # ──────────────────────────────────────────
    def open_upload_modal(self) -> None:
        if not self.selected_top:
            self.error = "먼저 대분류를 선택해 주세요."
            return
        self.upload_sub = self.selected_sub
        self.upload_parser_label = PARSER_LABEL_DEFAULT
        self.pending = []
        self.upload_phase = ""
        self._clear_messages()
        self.show_upload_modal = True

    def close_upload_modal(self) -> rx.event.EventSpec:
        self.show_upload_modal = False
        self.pending = []
        return rx.call_script("clearKbSelectedFiles()")

    def set_upload_sub(self, value: str) -> None:
        self.upload_sub = value

    def set_upload_parser_label(self, label: str) -> None:
        if label in PARSER_LABELS:
            self.upload_parser_label = label

    def pick_files(self) -> rx.event.EventSpec:
        """파일 선택 다이얼로그. 채팅 KB 패널과 같은 picker 를 그대로 쓴다.

        취소를 'cancel' 이벤트로 즉시 감지하지 않으면 Promise 가 timeout 까지 남아
        다른 이벤트가 큐에 쌓인다(채팅 쪽에서 겪은 문제).
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
            callback=KbAdminState.add_picked_files,
        )

    def add_picked_files(self, files_meta: list[dict]) -> None:
        """picker 콜백 — 파일 메타만 받아 대기 목록에 누적(실제 바이트는 브라우저에)."""
        for meta in files_meta or []:
            name = meta.get("name", "")
            if not name or any(f.name == name for f in self.pending):
                continue
            size = int(meta.get("size", 0) or 0)
            self.pending = self.pending + [
                PendingFile(name=name, size=size, size_display=format_file_size(size))
            ]

    def remove_pending(self, name: str) -> rx.event.EventSpec:
        """대기 목록에서 제거. JS 누적 선택에서도 빼야 같은 파일을 다시 고를 수 있다."""
        self.pending = [f for f in self.pending if f.name != name]
        return rx.call_script(f"removeKbSelectedFile({json.dumps(name)})")

    async def confirm_upload(self):
        """선택 파일을 배치로 전송 → staging 적재 후 on_staged 콜백."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        if not self.pending:
            self.error = "업로드할 파일을 선택해 주세요."
            return

        folder = self.upload_target_label
        names = [f.name for f in self.pending]
        batches = -(-len(names) // KB_UPLOAD_MAX_PER_REQUEST)      # 올림 나눗셈
        # 대상 폴더를 지금 고정한다 — 전송 중에 관리자가 다른 폴더를 클릭하면
        # upload_target_label 이 바뀌어 색인이 엉뚱한 폴더로 간다.
        self.upload_folder = folder
        self.upload_phase = f"전송 중 — {len(names)}개 ({batches}회로 나눠 보냄)"
        self.error = ""
        yield

        args = json.dumps([folder, names, KB_UPLOAD_MAX_PER_REQUEST])
        yield rx.call_script(
            f"uploadSharedKbFiles.apply(null, {args})",
            callback=KbAdminState.on_staged,
        )

    @rx.event(background=True)
    async def on_staged(self, result):
        """전송 완료 콜백 → 변환·색인 시작.

        background 이벤트인 이유: Upstage 변환이 파일 수에 비례해 길어지는데, 그 동안
        이벤트 채널을 점유하면 관리자 화면 전체가 멈춘 것처럼 보이고 websocket 이
        유휴로 끊긴다. 상태 변경은 `async with self:` 안에서만 한다.
        """
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:  # noqa: BLE001 - 응답 형식 자체가 깨진 경우
                async with self:
                    self.upload_phase = ""
                    self.error = f"업로드 응답을 해석할 수 없습니다: {result}"
                return

        result = result or {}
        staged = list(result.get("staged") or [])
        error = result.get("error")

        async with self:
            folder = self.upload_folder

        if error:
            # 배치 중간 실패 — 앞선 배치가 staging/ 에 남으므로 정리한다(고아 방지).
            if staged:
                try:
                    await asyncio.to_thread(shared_kb_service.discard_staged, folder, staged)
                except Exception:  # noqa: BLE001 - 정리는 best-effort
                    log.warning("staging 정리 실패: folder=%s", folder, exc_info=True)
            async with self:
                self.upload_phase = ""
                self.error = f"업로드 실패: {error}"
                # 대기 목록도 비운다 — JS 쪽 누적 선택(_kbSelectedFiles)은 이미 비워졌으므로
                # 목록만 남겨두면 재시도가 '선택된 파일 없음' 으로 실패한다.
                self.pending = []
            return

        if not staged:
            async with self:
                self.upload_phase = ""
                self.error = "적재된 파일이 없습니다. 파일을 다시 선택해 주세요."
                self.pending = []
            return

        async with self:
            self.upload_phase = f"변환·색인 중 — {len(staged)}개"
            self.show_upload_modal = False
            self.pending = []
            parser_label = self.upload_parser_label

        policy = SHARED_PARSE_POLICY.with_parser(
            PARSER_LABELS.get(parser_label, PARSER_AUTO)
        )
        try:
            await asyncio.to_thread(shared_kb_service.process_staged, folder, staged, policy)
            job_id = await asyncio.to_thread(shared_kb_service.start_ingestion, folder)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("공용 KB 색인 준비 실패: folder=%s", folder)
            async with self:
                self.upload_phase = ""
                self.error = f"색인 실패: {exc}"
            return

        log.info("[KbAdmin] 색인 시작: folder=%s, job_id=%s", folder, job_id)
        async with self:
            self.upload_phase = ""
            self.success = (
                f"{len(staged)}개를 올렸습니다. 색인이 시작됐습니다 — "
                "'상태 확인' 으로 결과를 볼 수 있습니다."
            )
            self.ingest_label = _STATUS_LABELS["STARTING"]
            self.ingest_detail = ""
            self.ingest_failed = False

        # 목록 갱신. background 이벤트라 _load(포그라운드 제너레이터)를 쓸 수 없어
        # 조회는 락 밖, 반영만 락 안에서 한다. 여기서 실패해도 업로드는 이미 성공이므로
        # 에러로 덮지 않고 경고만 남긴다(관리자가 '목록 새로고침' 으로 다시 볼 수 있다).
        try:
            rows, tops, attrs = await asyncio.to_thread(_fetch_all)
        except Exception:  # noqa: BLE001 - 목록 갱신 실패는 치명적이지 않다
            log.warning("업로드 후 목록 갱신 실패: folder=%s", folder, exc_info=True)
            return
        async with self:
            self._apply_lists(rows, tops, attrs)

    # ──────────────────────────────────────────
    # 문서 삭제
    # ──────────────────────────────────────────
    def toggle_doc(self, path: str) -> None:
        if path in self.selected_docs:
            self.selected_docs = [p for p in self.selected_docs if p != path]
        else:
            self.selected_docs = self.selected_docs + [path]

    def toggle_all_docs(self) -> None:
        """헤더 체크박스 — 현재 폴더 문서 전체 선택/해제."""
        visible = [d.path for d in self.docs if d.parent == self.selected_folder]
        if visible and all(path in self.selected_docs for path in visible):
            self.selected_docs = [p for p in self.selected_docs if p not in visible]
        else:
            remaining = [p for p in self.selected_docs if p not in visible]
            self.selected_docs = remaining + visible

    def open_delete_modal(self) -> None:
        if not self.selected_docs:
            self.error = "삭제할 문서를 선택해 주세요."
            return
        self._clear_messages()
        self.show_delete_modal = True

    def close_delete_modal(self) -> None:
        self.show_delete_modal = False

    async def confirm_delete(self):
        """선택 문서를 S3(raw+originals)에서 지우고 재-ingest.

        재색인을 돌리지 않으면 벡터가 남아 지운 문서가 계속 검색된다. 재색인은
        **대분류(Data Source)당 한 번**이면 충분하다 — 문서마다 job 을 띄우면 동시
        실행이 겹쳐 뒤 job 이 거부되거나 불필요하게 전체를 다시 훑는다.
        """
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        targets = list(self.selected_docs)
        if not targets:
            return

        self.deleting = True
        self.error = ""
        yield

        try:
            await asyncio.to_thread(shared_kb_service.delete_docs, targets)
            tops = list(dict.fromkeys(path.split("/")[0] for path in targets))
            for top in tops:
                job_id = await asyncio.to_thread(shared_kb_service.start_ingestion, top)
                log.info("[KbAdmin] 삭제 후 재색인: top=%s, job_id=%s", top, job_id)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("공용 KB 문서 삭제 실패: %d건", len(targets))
            self.deleting = False
            self.error = f"삭제 실패: {exc}"
            return

        self.deleting = False
        self.show_delete_modal = False
        self.selected_docs = []
        self.success = f"{len(targets)}건을 삭제하고 재색인을 시작했습니다."
        self.ingest_label = _STATUS_LABELS["STARTING"]
        self.ingest_detail = ""
        self.ingest_failed = False
        async for _ in self._load():
            yield

    # ──────────────────────────────────────────
    # 문서별 속성 (권위 티어 / 담당 부서)
    # ──────────────────────────────────────────
    def _replace_doc(
        self, path: str, *, tier: str | None = None, dept: str | None = None,
    ) -> None:
        """문서 표의 한 행만 갱신 — 전체 재조회(S3 나열) 없이 화면을 맞춘다.

        모델을 새로 만들어 넣는다(`copy(update=)` 는 pydantic 버전에 따라 동작이 달라짐).
        """
        updated: list[KbAdminDoc] = []
        for doc in self.docs:
            if doc.path != path:
                updated.append(doc)
                continue
            updated.append(KbAdminDoc(
                path=doc.path,
                name=doc.name,
                parent=doc.parent,
                uploaded_at=doc.uploaded_at,
                tier=doc.tier if tier is None else tier,
                dept=doc.dept if dept is None else dept,
            ))
        self.docs = updated

    def set_dept_draft(self, path: str, value: str) -> None:
        """담당 부서 입력 중 값(저장 안 함).

        controlled input 으로 두는 이유: `default_value` 로 두면 폴더를 바꿔도 React 가
        같은 위치의 input DOM 을 재사용해 **이전 폴더의 부서명이 남는다**.
        """
        self._replace_doc(path, dept=value)

    async def change_tier(self, path: str, value: str):
        """티어 드롭다운 변경 → 설정에 즉시 기록(재시작 없이 다음 검색부터 반영)."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        tier = None if value == TIER_NONE else int(value)
        try:
            await asyncio.to_thread(shared_kb_docs.set_doc_tier, path, tier)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("문서 티어 기록 실패: %s", path)
            self.error = f"티어 저장 실패: {exc}"
            return
        self._replace_doc(path, tier=value)
        self.success = f"'{path.rsplit('/', 1)[-1]}' 티어를 {value} 로 저장했습니다."

    async def change_dept(self, path: str, value: str):
        """담당 부서 입력 확정(blur) → 설정에 기록. 빈값이면 해제."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        # 비교 기준은 화면 값이 아니라 **저장된 값** — 화면 값은 타이핑 중 draft 라
        # 그걸로 비교하면 항상 같아서 저장이 안 된다. 설정 캐시 조회라 IO 없음.
        saved = str((shared_kb_docs.list_doc_attrs().get(path) or {}).get("dept") or "")
        name = (value or "").strip()
        if name == saved:
            self._replace_doc(path, dept=saved)      # 공백만 지운 입력을 되돌린다
            return
        try:
            await asyncio.to_thread(shared_kb_docs.set_doc_dept, path, name)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("문서 담당부서 기록 실패: %s", path)
            self.error = f"담당 부서 저장 실패: {exc}"
            return
        self._replace_doc(path, dept=name)
        self.success = f"'{path.rsplit('/', 1)[-1]}' 담당 부서를 저장했습니다."

    # ──────────────────────────────────────────
    # 유령 속성 항목 정리
    # ──────────────────────────────────────────
    def open_orphan_modal(self) -> None:
        self._clear_messages()
        self.show_orphan_modal = True

    def close_orphan_modal(self) -> None:
        self.show_orphan_modal = False

    async def clear_orphan_docs(self):
        """실제 문서가 없는 속성 항목을 제거. S3 는 건드리지 않는다(설정만 정리)."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        targets = list(self.orphan_doc_keys)
        if not targets:
            self.show_orphan_modal = False
            return

        try:
            removed = await asyncio.to_thread(shared_kb_docs.remove_docs, targets)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("유령 속성 항목 정리 실패: %d건", len(targets))
            self.error = f"정리 실패: {exc}"
            return

        self.show_orphan_modal = False
        self.orphan_doc_keys = []
        self.success = f"속성 항목 {removed}건을 정리했습니다."

    # ──────────────────────────────────────────
    # 색인 상태
    # ──────────────────────────────────────────
    async def refresh_status(self):
        """최신 Ingestion Job 조회. 업로드는 fire-and-forget 이라 실패를 여기서 본다."""
        if not await self._is_db_admin():
            self.error = _ADMIN_ONLY
            return
        top = self.selected_top
        if not top:
            self.error = "먼저 대분류를 선택해 주세요."
            return
        if top not in self.registered_tops:
            self.error = f"'{top}' 은 Data Source 가 없어 색인 상태가 없습니다."
            return

        self.ingest_label = "조회 중"
        self.ingest_detail = ""
        self.error = ""
        yield

        try:
            info = await asyncio.to_thread(shared_kb_service.latest_ingestion, top)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            log.exception("색인 상태 조회 실패: %s", top)
            self.ingest_label = ""
            self.error = f"상태 조회 실패: {exc}"
            return

        if not info:
            self.ingest_label = "색인 기록 없음"
            self.ingest_detail = ""
            self.ingest_failed = False
            return

        status = info.get("status", "")
        failed = int(info.get("failed", 0) or 0)
        self.ingest_label = _STATUS_LABELS.get(status, status)
        self.ingest_detail = (
            f"신규 {info.get('indexed', 0)} · 삭제 {info.get('deleted', 0)} · "
            f"실패 {failed} · {info.get('updated_at', '')}"
        )
        # 부분 실패(COMPLETE + 실패 N건)도 경고로 본다 — 완료 배지에 묻히면 놓친다.
        self.ingest_failed = status in ("FAILED", "STOPPED") or failed > 0
