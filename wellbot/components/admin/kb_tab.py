"""공용(회사) 지식베이스 관리 탭.

2분할 콘솔 — 좌측은 폴더 레일(대분류/소분류), 우측은 선택한 폴더의 문서 표다.
문서 행에서 권위 티어와 담당 부서를 바로 고칠 수 있고(설정에 즉시 기록), 폴더 생성 ·
업로드 · 삭제는 기존 admin 탭들과 같은 `rx.dialog` 모달을 쓴다.

여기까지가 `scripts/shared_kb_manager.py` CLI 로만 하던 작업이다. CLI 는 헤드리스
운영(대량 배치·rename)용으로 남는다.
"""

import reflex as rx

from wellbot.components.admin.cleanup_panel import CLEANUP_ITEMS, cleanup_panel
from wellbot.components.chat.file_icon import file_icon_by_name
from wellbot.state.chat_models import PendingFile
from wellbot.state.kb_cleanup_state import KbCleanupState
from wellbot.state.kb_admin_state import (
    PARSER_LABELS,
    KbAdminDoc,
    KbAdminFolder,
    KbAdminState,
)
from wellbot.styles import COLORS, SPACING

_RAIL_WIDTH = "260px"

# 문서 표의 고정 열 폭. 문서명 열만 남는 폭을 차지한다(table_layout=fixed).
_COL_CHECK = "44px"
_COL_DATE = "96px"
_COL_TIER = "88px"
_COL_DEPT = "150px"


# ──────────────────────────────────────────────
# 좌측: 폴더 레일
# ──────────────────────────────────────────────
def _toggle_box(folder: KbAdminFolder) -> rx.Component:
    """+/- 펼침 토글. 하위 폴더가 없으면 자리만 비워 정렬을 유지한다.

    행 전체가 아니라 이 박스에만 토글을 걸고 선택은 옆 영역이 받는다 —
    이벤트 전파에 의존하지 않아 '펼치려다 선택이 바뀌는' 일이 없다.
    """
    is_expanded = KbAdminState.expanded_folders.contains(folder.path)
    return rx.cond(
        folder.has_children,
        rx.box(
            rx.icon(
                rx.cond(is_expanded, "minus", "plus"),
                size=10,
                color=COLORS["text_secondary"],
            ),
            width="18px",
            height="18px",
            flex_shrink="0",
            display="flex",
            align_items="center",
            justify_content="center",
            cursor="pointer",
            border=f"1px solid {COLORS['border']}",
            border_radius="3px",
            on_click=KbAdminState.toggle_folder(folder.path),
            _hover={"bg": COLORS["sidebar_hover"]},
        ),
        rx.box(width="18px", flex_shrink="0"),
    )


def _folder_row(folder: KbAdminFolder) -> rx.Component:
    """폴더 한 줄. depth 만큼 들여쓰고, 선택된 폴더를 강조."""
    is_selected = KbAdminState.selected_folder == folder.path
    return rx.hstack(
        _toggle_box(folder),
        rx.hstack(
            rx.icon("folder", size=14, color=COLORS["text_secondary"], flex_shrink="0"),
            rx.text(
                folder.name,
                size="2",
                color=COLORS["text_primary"],
                weight=rx.cond(is_selected, "medium", "regular"),
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
                flex="1",
                min_width="0",
            ),
            rx.text(
                folder.doc_count.to_string(),
                size="1",
                color=COLORS["text_secondary"],
                flex_shrink="0",
            ),
            align="center",
            gap="0.4em",
            flex="1",
            min_width="0",
            cursor="pointer",
            on_click=KbAdminState.select_folder(folder.path),
        ),
        width="100%",
        align="center",
        gap="0.4em",
        padding_y="0.4em",
        padding_right="0.5em",
        padding_left=folder.indent,      # 기본 여백 포함 (state._indent)
        border_radius=SPACING["border_radius_sm"],
        bg=rx.cond(is_selected, COLORS["sidebar_active"], "transparent"),
        _hover={"bg": rx.cond(is_selected, COLORS["sidebar_active"], COLORS["sidebar_hover"])},
    )


def _cleanup_item(label: str, mode: str, icon: str) -> rx.Component:
    """정리 항목 한 줄. 고르면 우측이 정리 화면으로 바뀐다."""
    is_active = (KbAdminState.view == "cleanup") & (KbCleanupState.mode == mode)
    return rx.hstack(
        rx.icon(icon, size=14, color=COLORS["text_secondary"], flex_shrink="0"),
        rx.text(
            label,
            size="2",
            color=COLORS["text_primary"],
            weight=rx.cond(is_active, "medium", "regular"),
        ),
        width="100%",
        align="center",
        gap="0.4em",
        padding_y="0.4em",
        padding_x="0.5em",
        cursor="pointer",
        border_radius=SPACING["border_radius_sm"],
        bg=rx.cond(is_active, COLORS["sidebar_active"], "transparent"),
        _hover={"bg": rx.cond(is_active, COLORS["sidebar_active"], COLORS["sidebar_hover"])},
        on_click=KbCleanupState.open(mode),
    )


def _folder_rail() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("공용 KB", size="1", color=COLORS["category_text"], weight="medium"),
            rx.spacer(),
            rx.button(
                rx.icon("folder-plus", size=14),
                "대분류",
                size="1",
                variant="soft",
                on_click=KbAdminState.open_folder_modal,
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            KbAdminState.folders.length() == 0,
            rx.text(
                "등록된 대분류가 없습니다.",
                size="1",
                color=COLORS["text_secondary"],
                padding_y="0.5em",
            ),
            rx.box(
                rx.foreach(KbAdminState.visible_folders, _folder_row),
                width="100%",
                overflow_y="auto",
                overflow_x="hidden",
                max_height="45vh",
            ),
        ),
        rx.divider(margin_y="0.5em"),
        rx.text("정리", size="1", color=COLORS["category_text"], weight="medium"),
        *[_cleanup_item(label, mode, icon) for label, mode, icon in CLEANUP_ITEMS],
        width=_RAIL_WIDTH,
        flex_shrink="0",
        spacing="2",
        align="start",
        padding_right="1em",
        border_right=f"1px solid {COLORS['border']}",
    )


# ──────────────────────────────────────────────
# 우측: 문서 표
# ──────────────────────────────────────────────
def _doc_row(doc: KbAdminDoc) -> rx.Component:
    """문서 한 행. 티어·담당부서는 그 자리에서 고쳐 설정에 바로 기록한다."""
    return rx.table.row(
        rx.table.cell(
            rx.checkbox(
                checked=KbAdminState.selected_docs.contains(doc.path),
                on_change=lambda _: KbAdminState.toggle_doc(doc.path),
            ),
            text_align="center",
        ),
        rx.table.cell(
            rx.hstack(
                file_icon_by_name(doc.name),
                # 열 폭이 고정이라 긴 제목은 잘린다 → 전체 제목은 hover 툴팁으로.
                rx.tooltip(
                    rx.text(
                        doc.name,
                        size="2",
                        overflow="hidden",
                        text_overflow="ellipsis",
                        white_space="nowrap",
                    ),
                    content=doc.name,
                ),
                align="center",
                gap="0.4em",
                min_width="0",
            ),
            overflow="hidden",
        ),
        rx.table.cell(
            rx.text(doc.uploaded_at, size="1", color=COLORS["text_secondary"]),
            text_align="center",
        ),
        rx.table.cell(
            rx.select(
                KbAdminState.tier_choices,
                value=doc.tier,
                on_change=lambda value: KbAdminState.change_tier(doc.path, value),
                size="1",
                width="72px",
            ),
            text_align="center",
        ),
        rx.table.cell(
            # 저장은 on_blur 에서 한 번만 — 입력 중 매 글자마다 yaml 을 쓰지 않는다.
            rx.input(
                value=doc.dept,
                placeholder="미지정",
                on_change=lambda value: KbAdminState.set_dept_draft(doc.path, value),
                on_blur=lambda value: KbAdminState.change_dept(doc.path, value),
                size="1",
                width="100%",
            ),
        ),
    )


def _status_badge() -> rx.Component:
    """색인 상태 배지. 실패·부분실패는 색으로 구분해 완료에 묻히지 않게 한다."""
    return rx.cond(
        KbAdminState.ingest_label != "",
        rx.hstack(
            rx.badge(
                KbAdminState.ingest_label,
                size="1",
                variant="soft",
                color_scheme=rx.cond(KbAdminState.ingest_failed, "red", "green"),
            ),
            rx.text(KbAdminState.ingest_detail, size="1", color=COLORS["text_secondary"]),
            align="center",
            gap="0.5em",
        ),
    )


def _doc_toolbar() -> rx.Component:
    return rx.hstack(
        rx.hstack(
            rx.icon("database", size=16, color=COLORS["text_secondary"]),
            rx.text(
                rx.cond(KbAdminState.selected_folder != "", KbAdminState.selected_folder, "—"),
                size="2",
                weight="medium",
            ),
            align="center",
            gap="0.4em",
            min_width="0",
        ),
        rx.spacer(),
        _status_badge(),
        rx.cond(
            KbAdminState.has_doc_selection,
            rx.button(
                rx.icon("trash-2", size=14),
                f"선택 삭제 ({KbAdminState.selected_count})",
                size="2",
                color_scheme="red",
                variant="soft",
                disabled=KbAdminState.is_busy,
                on_click=KbAdminState.open_delete_modal,
            ),
        ),
        rx.button(
            rx.icon("upload", size=14),
            "업로드",
            size="2",
            disabled=KbAdminState.is_busy,
            on_click=KbAdminState.open_upload_modal,
        ),
        rx.button(
            rx.icon("refresh-cw", size=14),
            "상태 확인",
            size="2",
            variant="soft",
            on_click=KbAdminState.refresh_status,
        ),
        width="100%",
        align="center",
        gap="0.5em",
        wrap="wrap",
    )


def _doc_table() -> rx.Component:
    return rx.vstack(
        _doc_toolbar(),
        rx.cond(
            KbAdminState.upload_phase != "",
            rx.hstack(
                rx.spinner(size="1"),
                rx.text(KbAdminState.upload_phase, size="1", color=COLORS["text_secondary"]),
                align="center",
                gap="0.5em",
            ),
        ),
        rx.cond(
            KbAdminState.visible_docs.length() == 0,
            rx.center(
                rx.text(
                    "이 폴더에 문서가 없습니다.",
                    size="2",
                    color=COLORS["text_secondary"],
                ),
                padding="2em",
                width="100%",
            ),
            rx.box(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell(
                                rx.checkbox(
                                    checked=KbAdminState.all_visible_selected,
                                    on_change=lambda _: KbAdminState.toggle_all_docs(),
                                ),
                                width=_COL_CHECK,
                                text_align="center",
                            ),
                            rx.table.column_header_cell("문서"),
                            rx.table.column_header_cell(
                                "업로드", width=_COL_DATE, text_align="center",
                            ),
                            rx.table.column_header_cell(
                                "티어", width=_COL_TIER, text_align="center",
                            ),
                            rx.table.column_header_cell(
                                "담당 부서", width=_COL_DEPT, text_align="center",
                            ),
                        ),
                    ),
                    rx.table.body(rx.foreach(KbAdminState.visible_docs, _doc_row)),
                    # table_layout=fixed 가 핵심 — 기본(auto)은 내용 폭으로 열을 다시
                    # 계산해서, 폴더를 바꿀 때마다 열 위치가 좌우로 흔들린다.
                    table_layout="fixed",
                    width="100%",
                    min_width="620px",
                    size="1",
                ),
                width="100%",
                overflow_x="auto",
            ),
        ),
        flex="1",
        min_width="0",
        spacing="3",
        padding_left="1em",
        align="start",
    )


# ──────────────────────────────────────────────
# 모달
# ──────────────────────────────────────────────
def _folder_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("새 대분류 폴더"),
            rx.vstack(
                rx.text("대분류 이름", size="2", weight="medium"),
                rx.input(
                    value=KbAdminState.new_folder_name,
                    placeholder="예: 사규",
                    on_change=KbAdminState.set_new_folder_name,
                    width="100%",
                ),
                rx.callout(
                    "Bedrock Data Source 가 하나 생성됩니다. 소분류는 업로드할 때 "
                    "경로로 지정하며 별도 리소스를 만들지 않습니다.",
                    icon="info",
                    size="1",
                ),
                rx.hstack(
                    rx.button(
                        "취소",
                        variant="soft",
                        color_scheme="gray",
                        on_click=KbAdminState.close_folder_modal,
                    ),
                    rx.button(
                        rx.cond(KbAdminState.creating_folder, rx.spinner(size="1"), rx.text("생성")),
                        disabled=KbAdminState.creating_folder,
                        on_click=KbAdminState.create_folder,
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="440px",
        ),
        open=KbAdminState.show_folder_modal,
        on_open_change=lambda is_open: rx.cond(  # type: ignore[misc]
            ~is_open, KbAdminState.close_folder_modal, None
        ),
    )


def _pending_row(file: PendingFile) -> rx.Component:
    return rx.hstack(
        file_icon_by_name(file.name),
        rx.text(
            file.name,
            size="1",
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
            flex="1",
            min_width="0",
        ),
        rx.text(file.size_display, size="1", color=COLORS["text_secondary"], flex_shrink="0"),
        rx.icon_button(
            rx.icon("x", size=12),
            variant="ghost",
            size="1",
            cursor="pointer",
            on_click=KbAdminState.remove_pending(file.name),
        ),
        width="100%",
        align="center",
        gap="0.4em",
    )


def _upload_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("문서 업로드"),
            rx.vstack(
                rx.text("소분류 (비우면 대분류 직속)", size="2", weight="medium"),
                rx.input(
                    value=KbAdminState.upload_sub,
                    placeholder="예: 규정/인사노무",
                    on_change=KbAdminState.set_upload_sub,
                    width="100%",
                ),
                rx.hstack(
                    rx.text("업로드 경로", size="1", color=COLORS["text_secondary"]),
                    rx.code(KbAdminState.upload_target_label, size="1"),
                    align="center",
                    gap="0.5em",
                ),
                rx.text("파서", size="2", weight="medium"),
                rx.radio(
                    list(PARSER_LABELS),
                    value=KbAdminState.upload_parser_label,
                    on_change=KbAdminState.set_upload_parser_label,
                    direction="row",
                    spacing="4",
                    size="1",
                ),
                rx.hstack(
                    rx.button(
                        rx.icon("plus", size=14),
                        "파일 선택",
                        size="2",
                        variant="soft",
                        on_click=KbAdminState.pick_files,
                    ),
                    rx.text(
                        KbAdminState.pending_label,
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    align="center",
                    gap="0.5em",
                ),
                rx.cond(
                    KbAdminState.pending.length() > 0,
                    rx.box(
                        rx.vstack(
                            rx.foreach(KbAdminState.pending, _pending_row),
                            spacing="1",
                            width="100%",
                        ),
                        width="100%",
                        max_height="200px",
                        overflow_y="auto",
                        overflow_x="hidden",
                    ),
                ),
                rx.hstack(
                    rx.button(
                        "취소",
                        variant="soft",
                        color_scheme="gray",
                        on_click=KbAdminState.close_upload_modal,
                    ),
                    rx.button(
                        rx.cond(
                            KbAdminState.upload_phase != "",
                            rx.spinner(size="1"),
                            rx.text("업로드 + 색인"),
                        ),
                        # 전송 중에도 모달이 열려 있어 중복 클릭이 가능하다 → 명시적으로 막는다
                        disabled=(
                            (KbAdminState.pending.length() == 0)
                            | (KbAdminState.upload_phase != "")
                        ),
                        on_click=KbAdminState.confirm_upload,
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="520px",
        ),
        open=KbAdminState.show_upload_modal,
        on_open_change=lambda is_open: rx.cond(  # type: ignore[misc]
            ~is_open, KbAdminState.close_upload_modal, None
        ),
    )


def _orphan_modal() -> rx.Component:
    """유령 속성 항목 확인 모달. 지울 키를 그대로 보여주고 확인을 받는다."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("속성 항목 정리"),
            rx.vstack(
                rx.text(
                    "아래 항목은 실제 문서가 없습니다. 설정에서만 제거하며 "
                    "S3 파일은 건드리지 않습니다.",
                    size="2",
                ),
                rx.box(
                    rx.vstack(
                        rx.foreach(
                            KbAdminState.orphan_doc_keys,
                            lambda key: rx.text(
                                key, size="1", color=COLORS["text_secondary"],
                            ),
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    width="100%",
                    max_height="220px",
                    overflow_y="auto",
                    overflow_x="auto",
                ),
                rx.hstack(
                    rx.button(
                        "취소",
                        variant="soft",
                        color_scheme="gray",
                        on_click=KbAdminState.close_orphan_modal,
                    ),
                    rx.button(
                        "정리",
                        color_scheme="red",
                        on_click=KbAdminState.clear_orphan_docs,
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="560px",
        ),
        open=KbAdminState.show_orphan_modal,
        on_open_change=lambda is_open: rx.cond(  # type: ignore[misc]
            ~is_open, KbAdminState.close_orphan_modal, None
        ),
    )


def _delete_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("문서 삭제"),
            rx.vstack(
                rx.text(
                    f"{KbAdminState.selected_count}건을 삭제합니다.",
                    size="2",
                    weight="medium",
                ),
                rx.text(KbAdminState.delete_summary, size="1", color=COLORS["text_secondary"]),
                rx.callout(
                    "S3 원본과 색인본을 지우고 바로 재색인합니다. 재색인이 끝나기 전까지는 "
                    "검색 결과에 남아 있을 수 있습니다.",
                    icon="triangle_alert",
                    color_scheme="red",
                    size="1",
                ),
                rx.hstack(
                    rx.button(
                        "취소",
                        variant="soft",
                        color_scheme="gray",
                        on_click=KbAdminState.close_delete_modal,
                    ),
                    rx.button(
                        rx.cond(KbAdminState.deleting, rx.spinner(size="1"), rx.text("삭제")),
                        color_scheme="red",
                        disabled=KbAdminState.deleting,
                        on_click=KbAdminState.confirm_delete,
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="440px",
        ),
        open=KbAdminState.show_delete_modal,
        on_open_change=lambda is_open: rx.cond(  # type: ignore[misc]
            ~is_open, KbAdminState.close_delete_modal, None
        ),
    )


# ──────────────────────────────────────────────
# 탭 진입점
# ──────────────────────────────────────────────
def kb_tab() -> rx.Component:
    """공용 KB 관리 탭."""
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.hstack(
                    rx.heading("지식베이스 관리", size="4"),
                    rx.text("공용(회사) KB", size="1", color=COLORS["text_secondary"]),
                    align="end",
                    spacing="2",
                ),
                rx.spacer(),
                rx.button(
                    rx.icon("refresh-cw", size=14),
                    "목록 새로고침",
                    size="2",
                    variant="soft",
                    on_click=KbAdminState.reload,
                ),
                width="100%",
                align="center",
            ),
            rx.cond(
                KbAdminState.error != "",
                rx.callout(
                    KbAdminState.error, icon="triangle_alert", color_scheme="red", size="1",
                    width="100%",
                ),
            ),
            rx.cond(
                KbAdminState.has_orphan_docs,
                rx.callout(
                    rx.hstack(
                        rx.text(KbAdminState.orphan_notice, size="1"),
                        rx.button(
                            "확인 후 정리",
                            size="1",
                            variant="soft",
                            color_scheme="amber",
                            on_click=KbAdminState.open_orphan_modal,
                        ),
                        align="center",
                        gap="0.75em",
                        wrap="wrap",
                    ),
                    icon="triangle_alert",
                    color_scheme="amber",
                    size="1",
                    width="100%",
                ),
            ),
            rx.cond(
                KbAdminState.success != "",
                rx.callout(
                    KbAdminState.success, icon="check", color_scheme="green", size="1",
                    width="100%",
                ),
            ),
            rx.cond(
                KbAdminState.loading,
                rx.center(rx.spinner(size="3"), padding="3em", width="100%"),
                rx.hstack(
                    _folder_rail(),
                    rx.cond(
                        KbAdminState.view == "cleanup",
                        cleanup_panel(),
                        _doc_table(),
                    ),
                    width="100%",
                    align="start",
                    spacing="0",
                ),
            ),
            _folder_modal(),
            _upload_modal(),
            _delete_modal(),
            _orphan_modal(),
            spacing="4",
            width="100%",
        ),
        # 탭이 올라올 때마다 다시 읽는다 — CLI 나 다른 관리자가 바꾼 내용을 보려면
        # 한 번만 읽어선 안 된다(옛 스냅샷에서 편집하면 유령 항목이 생긴다).
        on_mount=KbAdminState.load_on_open,
        width="100%",
    )
