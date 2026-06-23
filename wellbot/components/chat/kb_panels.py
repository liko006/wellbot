"""지식베이스(KB) UI 컴포넌트.

입력 바의 + 메뉴에서 진입하는 KB 관련 패널/플라이아웃 모음.
input_bar.py 가 길어져 KB 전용 컴포넌트를 이 모듈로 분리했다.

공개 진입점 (input_bar 에서 import):
    kb_flyout         - + 메뉴의 '지식베이스' hover flyout (검색 범위/문서목록/업로드)
    kb_docs_panel     - 문서 목록 조회 패널
    kb_upload_panel   - 문서 업로드 패널
    ingestion_banner  - ingestion 진행 배너 (패널 닫혔을 때)
나머지 _* 함수는 모듈 내부 헬퍼.
"""

import reflex as rx

from wellbot.components.chat.file_icon import file_icon_by_name
from wellbot.state.chat_models import (
    KbSharedFile,
    KbSharedFolder,
    KbSharedSubfolder,
    PendingFile,
)
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, SPACING


def kb_flyout() -> rx.Component:
    """지식베이스 서브 항목 — hover 시 오른쪽 flyout (hover_card).

    'KB 검색 범위' 는 클릭 시 inline 으로 확장되어 체크박스가 펼쳐진다.
    '문서 목록', '업로드' 는 클릭 시 입력창 위 패널을 연다.
    """
    def _item(icon: str, label: str, on_click) -> rx.Component:
        return rx.hstack(
            rx.icon(icon, size=14, color=COLORS["text_secondary"]),
            rx.text(label, size="2"),
            align="center",
            gap="0.6em",
            padding="0.4em 0.75em",
            width="100%",
            border_radius=SPACING["border_radius_sm"],
            cursor="pointer",
            _hover={"bg": COLORS["sidebar_hover"]},
            on_click=on_click,
        )

    return rx.hover_card.root(
        rx.hover_card.trigger(
            rx.hstack(
                rx.icon("database-search", size=16, color=COLORS["text_secondary"]),
                rx.text("지식베이스", size="2"),
                rx.spacer(),
                rx.icon("chevron-right", size=14, color=COLORS["text_secondary"]),
                align="center",
                gap="0.6em",
                padding="0.5em 0.75em",
                width="100%",
                border_radius=SPACING["border_radius_sm"],
                cursor="pointer",
                # flyout 이 열려 있을 땐 회색 배경으로 '선택 중' 표시
                bg=rx.cond(
                    ChatState.kb_flyout_open,
                    COLORS["sidebar_hover"],
                    "transparent",
                ),
                _hover={"bg": COLORS["sidebar_hover"]},
            ),
        ),
        rx.hover_card.content(
            rx.vstack(
                # KB 검색 범위 (inline expand)
                rx.hstack(
                    rx.icon("search", size=14, color=COLORS["text_secondary"]),
                    rx.text("검색 범위", size="2"),
                    rx.spacer(),
                    rx.icon(
                        rx.cond(
                            ChatState.kb_scope_inline_expanded,
                            "chevron-up",
                            "chevron-down",
                        ),
                        size=14,
                        color=COLORS["text_secondary"],
                    ),
                    align="center",
                    gap="0.6em",
                    padding="0.4em 0.75em",
                    width="100%",
                    border_radius=SPACING["border_radius_sm"],
                    cursor="pointer",
                    _hover={"bg": COLORS["sidebar_hover"]},
                    on_click=ChatState.toggle_kb_scope_inline,
                ),
                # 확장 시 체크박스 3개 (한 칸 들여쓰기)
                rx.cond(
                    ChatState.kb_scope_inline_expanded,
                    rx.vstack(
                        _scope_checkbox("회사", "shared"),
                        _scope_checkbox("팀", "team"),
                        _scope_checkbox("개인", "personal"),
                        spacing="2",
                        padding_left="1.75em",
                        padding_top="0.4em",
                        padding_bottom="0.4em",
                        align_items="start",
                        width="100%",
                    ),
                ),
                _item(
                    "list",
                    "문서 목록",
                    [
                        ChatState.close_kb_flyout,
                        ChatState.open_panel("kb_docs"),
                        ChatState.load_kb_docs,
                    ],
                ),
                _item(
                    "folder-open",
                    "문서 업로드",
                    [ChatState.close_kb_flyout, ChatState.open_panel("upload")],
                ),
                spacing="0",
                width="100%",
            ),
            side="right",
            align="end",
            side_offset=-4,
            style={
                "padding": "0.4em",
                "border_radius": SPACING["border_radius_md"],
                "bg": COLORS["sidebar_bg"],
                "border": f"1px solid {COLORS['border']}",
                "box_shadow": "0 4px 24px rgba(0,0,0,0.25)",
                "min_width": "160px",
            },
        ),
        open=ChatState.kb_flyout_open,
        on_open_change=ChatState.on_kb_flyout_open_change,
        open_delay=100,
    )


def _scope_checkbox(label: str, mode: str) -> rx.Component:
    """KB 검색 범위 체크박스 항목."""
    return rx.hstack(
        rx.checkbox(
            checked=ChatState.kb_modes.contains(mode),
            on_change=lambda _: ChatState.toggle_kb_mode(mode),
            size="2",
        ),
        rx.text(label, size="2"),
        align="center",
        gap="0.5em",
        cursor="pointer",
    )


def _pending_file_row(f: PendingFile) -> rx.Component:
    """대기 중인 KB 업로드 파일 행."""
    return rx.hstack(
        file_icon_by_name(f.name),
        rx.text(
            f.name,
            size="1",
            flex="1",
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
        ),
        rx.text(f.size_display, size="1", color=COLORS["text_secondary"]),
        rx.icon_button(
            rx.icon("x", size=12),
            size="1",
            variant="ghost",
            cursor="pointer",
            color=COLORS["text_secondary"],
            on_click=ChatState.remove_pending_file(f.name),
        ),
        width="100%",
        align="center",
        gap="0.5em",
    )


def _kb_docs_tab_btn(label: str, tab: str) -> rx.Component:
    """문서 목록 탭 버튼."""
    is_active = ChatState.kb_doc_list_tab == tab
    is_team = tab == "team"
    is_disabled = is_team & (~ChatState.team_kb_exists | (ChatState.dept_cd == ""))
    return rx.el.button(
        label,
        on_click=rx.cond(is_disabled, rx.prevent_default, ChatState.set_kb_doc_list_tab(tab)),
        font_size="0.8rem",
        font_weight=rx.cond(is_active, "600", "400"),
        color=rx.cond(
            is_disabled,
            str(COLORS["text_secondary"]),
            rx.cond(is_active, str(COLORS["text_primary"]), str(COLORS["text_secondary"])),
        ),
        border_bottom=rx.cond(is_active, f"2px solid {COLORS['accent']}", "2px solid transparent"),
        background="transparent",
        border_top="none",
        border_left="none",
        border_right="none",
        padding="0.35em 0.75em",
        cursor=rx.cond(is_disabled, "not-allowed", "pointer"),
        opacity=rx.cond(is_disabled, "0.45", "1"),
    )


def _kb_doc_row(doc: rx.Var) -> rx.Component:
    """문서 목록 행."""
    return rx.hstack(
        # 체크박스 (개인/팀 탭에서 표시, 회사 탭에서는 공간만 차지하여 정렬 유지)
        rx.box(
            rx.checkbox(
                checked=ChatState.selected_kb_docs.contains(doc["file_name"]),
                on_change=lambda _: ChatState.toggle_kb_doc_selection(doc["file_name"]),
                size="1",
            ),
            width="20px",
            flex_shrink="0",
            display="flex",
            align_items="center",
            justify_content="center",
            style={
                "visibility": rx.cond(
                    ChatState.kb_doc_list_tab == "shared",
                    "hidden",
                    "visible",
                ),
            },
        ),
        # 파일 아이콘 + 파일명
        rx.hstack(
            file_icon_by_name(doc["file_name"]),
            rx.text(
                doc["file_name"],
                size="1",
                color=COLORS["text_primary"],
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
            ),
            align="center",
            gap="0.4em",
            flex="1",
            min_width="0",
            overflow="hidden",
        ),
        rx.text(
            doc["uploaded_at"],
            size="1",
            color=COLORS["text_secondary"],
            flex_shrink="0",
            width="80px",
            text_align="center",
        ),
        rx.text(
            doc["expires_at"],
            size="1",
            color=COLORS["text_secondary"],
            flex_shrink="0",
            width="80px",
            text_align="center",
        ),
        width="100%",
        align="center",
        gap="0.5em",
        padding_y="0.3em",
        border_bottom=f"1px solid {COLORS['border']}",
    )


def _kb_shared_file_row(doc: KbSharedFile, padding_left: str = "1em") -> rx.Component:
    """회사 KB 의 파일 행 (폴더 안에 들여쓰기로 표시).

    padding_left 로 들여쓰기 깊이 조절: 대분류 직속=1em, 소분류 하위=2.5em.
    """
    return rx.hstack(
        # 토글 박스 자리 비움 (들여쓰기 효과)
        rx.box(width="20px", flex_shrink="0"),
        # 파일 아이콘 + 파일명
        rx.hstack(
            file_icon_by_name(doc.file_name),
            rx.text(
                doc.file_name,
                size="1",
                color=COLORS["text_primary"],
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
            ),
            align="center",
            gap="0.4em",
            flex="1",
            min_width="0",
            overflow="hidden",
        ),
        rx.text(
            doc.uploaded_at,
            size="1",
            color=COLORS["text_secondary"],
            flex_shrink="0",
            width="80px",
            text_align="center",
        ),
        rx.text(
            doc.expires_at,
            size="1",
            color=COLORS["text_secondary"],
            flex_shrink="0",
            width="80px",
            text_align="center",
        ),
        width="100%",
        align="center",
        gap="0.5em",
        padding_y="0.3em",
        padding_left=padding_left,
        border_bottom=f"1px solid {COLORS['border']}",
    )


def _kb_toggle_box(key) -> rx.Component:
    """+/- 펼침 토글 박스. key 는 대분류(folder_type) 또는 '대분류/소분류' 복합키."""
    is_expanded = ChatState.expanded_kb_folders.contains(key)
    return rx.box(
        rx.icon(
            rx.cond(is_expanded, "minus", "plus"),
            size=10,
            color=COLORS["text_secondary"],
        ),
        width="20px",
        height="20px",
        flex_shrink="0",
        display="flex",
        align_items="center",
        justify_content="center",
        cursor="pointer",
        border=f"1px solid {COLORS['border']}",
        border_radius="3px",
        on_click=ChatState.toggle_kb_folder(key),
        _hover={"bg": COLORS["sidebar_hover"]},
    )


def _kb_subfolder_row(folder_type, sub: KbSharedSubfolder) -> rx.Component:
    """회사 KB 소분류 행 + 펼침 시 하위 파일 목록 (대분류 안에 한 단계 들여씀)."""
    composite_key = folder_type + "/" + sub.sub_name
    is_expanded = ChatState.expanded_kb_folders.contains(composite_key)
    return rx.vstack(
        rx.hstack(
            _kb_toggle_box(composite_key),
            rx.hstack(
                rx.icon("folder", size=13, color=COLORS["text_secondary"]),
                rx.text(
                    sub.sub_name,
                    size="1",
                    color=COLORS["text_primary"],
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                align="center",
                gap="0.4em",
                flex="1",
                min_width="0",
                cursor="default",
                user_select="none",
                on_double_click=ChatState.toggle_kb_folder(composite_key),
            ),
            rx.box(width="80px", flex_shrink="0"),
            rx.box(width="80px", flex_shrink="0"),
            width="100%",
            align="center",
            gap="0.5em",
            padding_y="0.3em",
            padding_left="1em",
            border_bottom=f"1px solid {COLORS['border']}",
        ),
        rx.cond(
            is_expanded,
            rx.foreach(sub.files, lambda d: _kb_shared_file_row(d, padding_left="2.5em")),
        ),
        width="100%",
        spacing="0",
        align_items="start",
    )


def _kb_subgroup(folder_type, sub: KbSharedSubfolder) -> rx.Component:
    """소분류 그룹 렌더. sub_name 이 빈 문자열이면 대분류 직속 파일로 바로 표시."""
    return rx.cond(
        sub.sub_name == "",
        rx.foreach(sub.files, _kb_shared_file_row),  # 대분류 raw/ 바로 밑 파일 (1em)
        _kb_subfolder_row(folder_type, sub),
    )


def _kb_folder_row(folder: KbSharedFolder) -> rx.Component:
    """회사 KB 대분류 행 + 펼침 시 소분류/파일 트리."""
    is_expanded = ChatState.expanded_kb_folders.contains(folder.folder_type)
    return rx.vstack(
        # 대분류 헤더
        rx.hstack(
            _kb_toggle_box(folder.folder_type),
            # 폴더 아이콘 + 대분류명 (더블클릭으로도 토글)
            rx.hstack(
                rx.icon("folder", size=14, color=COLORS["text_secondary"]),
                rx.text(
                    folder.folder_type,
                    size="1",
                    color=COLORS["text_primary"],
                    weight="medium",
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                align="center",
                gap="0.4em",
                flex="1",
                min_width="0",
                cursor="default",
                user_select="none",
                on_double_click=ChatState.toggle_kb_folder(folder.folder_type),
            ),
            # 날짜 컬럼 자리 비움 (폴더 단위에는 의미 없음)
            rx.box(width="80px", flex_shrink="0"),
            rx.box(width="80px", flex_shrink="0"),
            width="100%",
            align="center",
            gap="0.5em",
            padding_y="0.3em",
            border_bottom=f"1px solid {COLORS['border']}",
        ),
        # 펼침 시 소분류/파일 트리
        rx.cond(
            is_expanded,
            rx.foreach(
                folder.subfolders,
                lambda sf: _kb_subgroup(folder.folder_type, sf),
            ),
        ),
        width="100%",
        spacing="0",
        align_items="start",
    )


def kb_docs_panel() -> rx.Component:
    """KB 문서 목록 조회 패널 (입력창 위)."""
    return rx.cond(
        ChatState.active_panel == "kb_docs",
        rx.box(
            rx.vstack(
                # 헤더
                rx.hstack(
                    rx.icon("list", size=14, color=COLORS["text_secondary"]),
                    rx.text("문서 목록", size="2", weight="medium"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("x", size=14),
                        variant="ghost",
                        size="1",
                        cursor="pointer",
                        color=COLORS["text_secondary"],
                        on_click=ChatState.close_panel,
                    ),
                    width="100%",
                    align="center",
                ),
                rx.separator(size="4", color=COLORS["border"]),
                # 탭
                rx.hstack(
                    _kb_docs_tab_btn("회사", "shared"),
                    _kb_docs_tab_btn("팀", "team"),
                    _kb_docs_tab_btn("개인", "personal"),
                    gap="0",
                    border_bottom=f"1px solid {COLORS['border']}",
                    width="100%",
                ),
                # 로딩 / 빈 상태 / 목록
                rx.cond(
                    ChatState.kb_doc_list_loading,
                    rx.hstack(
                        rx.spinner(size="2"),
                        rx.text("불러오는 중...", size="1", color=COLORS["text_secondary"]),
                        justify="center",
                        width="100%",
                        padding_y="1em",
                    ),
                    rx.cond(
                        ChatState.kb_docs_empty,
                        rx.text(
                            "업로드된 문서가 없습니다.",
                            size="1",
                            color=COLORS["text_secondary"],
                            text_align="center",
                            width="100%",
                            padding_y="1em",
                        ),
                        rx.vstack(
                            # 컬럼 헤더 (행과 동일하게 체크박스 자리 확보)
                            rx.hstack(
                                rx.box(width="20px", flex_shrink="0"),
                                rx.text("파일명", size="1", color=COLORS["text_secondary"], flex="1", font_weight="500"),
                                rx.text("업로드일", size="1", color=COLORS["text_secondary"], width="80px", text_align="center", font_weight="500"),
                                rx.text("만료일", size="1", color=COLORS["text_secondary"], width="80px", text_align="center", font_weight="500"),
                                width="100%",
                                align="center",
                                gap="0.5em",
                                padding_bottom="0.3em",
                                border_bottom=f"1px solid {COLORS['border']}",
                            ),
                            rx.cond(
                                ChatState.kb_doc_list_tab == "shared",
                                # 회사 탭: 폴더(문서종류) 단위 그룹 뷰
                                rx.vstack(
                                    rx.foreach(ChatState.kb_folder_list, _kb_folder_row),
                                    spacing="0",
                                    width="100%",
                                    max_height="150px",
                                    overflow_y="auto",
                                ),
                                # 개인/팀 탭: 기존 flat 뷰
                                rx.vstack(
                                    rx.foreach(ChatState.kb_doc_list, _kb_doc_row),
                                    spacing="0",
                                    width="100%",
                                    max_height="150px",
                                    overflow_y="auto",
                                ),
                            ),
                            spacing="0",
                            width="100%",
                        ),
                    ),
                ),
                # 푸터: 상태 + 선택 삭제 버튼 (개인/팀 탭에서 표시, 회사 탭은 숨김)
                rx.cond(
                    ChatState.kb_doc_list_tab != "shared",
                    rx.hstack(
                        # 상태 메시지 (왼쪽)
                        rx.cond(
                            ChatState.kb_delete_status == "processing",
                            rx.hstack(
                                rx.spinner(size="1"),
                                rx.text(
                                    "삭제 처리 중... (수 분 소요)",
                                    size="1",
                                    color=rx.color("amber", 9),
                                ),
                                align="center",
                                gap="0.5em",
                            ),
                            rx.cond(
                                ChatState.kb_delete_status == "ready",
                                rx.hstack(
                                    rx.icon("circle-check", size=14, color=rx.color("green", 9)),
                                    rx.text("삭제 완료", size="1", color=rx.color("green", 9)),
                                    align="center",
                                    gap="0.4em",
                                ),
                                rx.cond(
                                    ChatState.kb_delete_status == "error",
                                    rx.hstack(
                                        rx.icon("circle-alert", size=14, color=rx.color("red", 9)),
                                        rx.text(
                                            ChatState.kb_delete_error,
                                            size="1",
                                            color=rx.color("red", 9),
                                        ),
                                        align="center",
                                        gap="0.4em",
                                    ),
                                    rx.fragment(),
                                ),
                            ),
                        ),
                        rx.spacer(),
                        # 선택 삭제 버튼 + 확인 다이얼로그
                        rx.alert_dialog.root(
                            rx.alert_dialog.trigger(
                                rx.button(
                                    rx.icon("trash-2", size=14),
                                    rx.text(ChatState.kb_delete_button_label, size="1"),
                                    size="2",
                                    variant="solid",
                                    color_scheme="red",
                                    disabled=(ChatState.selected_kb_docs.length() == 0)
                                    | (ChatState.kb_delete_status == "processing"),
                                    cursor=rx.cond(
                                        (ChatState.selected_kb_docs.length() == 0)
                                        | (ChatState.kb_delete_status == "processing"),
                                        "not-allowed",
                                        "pointer",
                                    ),
                                ),
                            ),
                            rx.alert_dialog.content(
                                rx.alert_dialog.title("선택한 파일을 삭제하시겠습니까?"),
                                rx.alert_dialog.description(
                                    rx.cond(
                                        ChatState.kb_doc_list_tab == "team",
                                        "선택한 파일이 팀 지식베이스에서 제거됩니다. "
                                        "팀원 모두가 영향을 받습니다. "
                                        "검색 인덱스 정리에 수 분 소요됩니다.",
                                        "선택한 파일이 지식베이스에서 제거됩니다. "
                                        "검색 인덱스 정리에 수 분 소요됩니다.",
                                    ),
                                    size="2",
                                ),
                                rx.flex(
                                    rx.alert_dialog.cancel(
                                        rx.button("취소", variant="soft", color_scheme="gray"),
                                    ),
                                    rx.alert_dialog.action(
                                        rx.button(
                                            "삭제",
                                            color_scheme="red",
                                            on_click=ChatState.confirm_kb_delete,
                                        ),
                                    ),
                                    spacing="3",
                                    justify="end",
                                    margin_top="1em",
                                ),
                            ),
                        ),
                        width="100%",
                        align="center",
                    ),
                ),
                spacing="2",
                width="100%",
            ),
            bg=COLORS["sidebar_bg"],
            border=f"1px solid {COLORS['border']}",
            border_radius=SPACING["border_radius_md"],
            padding="0.75em",
            width="100%",
            max_width=SPACING["message_max_width"],
            margin_x="auto",
            margin_bottom="0.5em",
        ),
        rx.fragment(),
    )


def _xlsx_format_callout() -> rx.Component:
    """엑셀(.xlsx)·CSV 검색 정확도 안내 강조박스.

    개인/팀 KB 업로드는 Lambda 의 '머리글: 값' 행 단위 청킹을 사용하므로, 첫 행이
    머리글이고 셀 병합·빈 행이 없어야 머리글과 데이터가 올바르게 매칭된다.
    업로드 대기 목록에 .xlsx/.csv 가 있을 때만 노출 (has_tabular_pending).
    """
    return rx.box(
        rx.hstack(
            rx.icon(
                "info",
                size=14,
                color=rx.color("amber", 11),
                flex_shrink="0",
                margin_top="0.15em",
            ),
            rx.vstack(
                rx.text(
                    "엑셀(.xlsx) · CSV 검색 정확도 안내",
                    size="1",
                    weight="bold",
                    color=COLORS["text_primary"],
                ),
                rx.text(
                    "시트의 첫 행을 머리글(컬럼명)로 두고 셀 병합·빈 행 없이 정리해주세요. "
                    "머리글과 데이터가 어긋나면 검색이 잘 되지 않습니다.",
                    size="1",
                    color=COLORS["text_secondary"],
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
        bg=rx.color("amber", 2),
        border=f"1px solid {rx.color('amber', 6)}",
        border_radius=SPACING["border_radius_sm"],
        padding="0.5em 0.75em",
        width="100%",
    )


def kb_upload_panel() -> rx.Component:
    """KB 문서 업로드 패널 (입력창 위)."""
    return rx.cond(
        ChatState.active_panel == "upload",
        rx.box(
            rx.vstack(
                # 헤더
                rx.hstack(
                    rx.icon("folder-open", size=14, color=COLORS["text_secondary"]),
                    rx.text("문서 업로드", size="2", weight="medium"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("x", size=14),
                        variant="ghost",
                        size="1",
                        cursor="pointer",
                        color=COLORS["text_secondary"],
                        on_click=ChatState.close_panel,
                    ),
                    width="100%",
                    align="center",
                ),
                rx.vstack(
                    rx.text("  - 복호화한 파일만 업로드 가능합니다.", size="1", color=COLORS["text_secondary"]),
                    spacing="1",
                    width="100%",
                ),
                rx.separator(size="4", color=COLORS["border"]),
                # 파일 선택 영역 (JS file picker — rx.upload의 10MB 제한 우회)
                rx.box(
                    rx.hstack(
                        rx.icon("folder-open", size=16, color=COLORS["text_secondary"]),
                        rx.text(
                            "클릭하여 파일 선택",
                            size="2",
                            color=COLORS["text_secondary"],
                        ),
                        spacing="2",
                        align="center",
                        justify="center",
                        width="100%",
                    ),
                    border=f"2px dashed {COLORS['input_border']}",
                    border_radius=SPACING["border_radius_sm"],
                    padding="0.75em",
                    width="100%",
                    cursor="pointer",
                    _hover={"border_color": COLORS["accent"]},
                    on_click=ChatState.open_file_picker,
                ),
                # 지원 형식 안내
                rx.text(
                    ".pdf .docx .pptx .xlsx .csv .md .txt .json .html  (최대 5개)",
                    size="1",
                    color=COLORS["text_secondary"],
                ),
                # 선택된 파일 목록
                rx.cond(
                    ChatState.pending_files.length() > 0,
                    rx.vstack(
                        rx.foreach(ChatState.pending_files, _pending_file_row),
                        spacing="1",
                        width="100%",
                        max_height="120px",
                        overflow_y="auto",
                    ),
                ),
                # 엑셀/CSV 형식 안내 (대기 목록에 .xlsx/.csv 가 있을 때만)
                rx.cond(
                    ChatState.has_tabular_pending,
                    _xlsx_format_callout(),
                ),
                # 업로드 대상 선택 + 확정 버튼
                rx.cond(
                    ChatState.pending_files.length() > 0,
                    rx.hstack(
                        rx.hstack(
                            rx.text("대상 지식베이스 :", size="1", color=COLORS["text_secondary"]),
                            rx.select.root(
                                rx.select.trigger(size="1"),
                                rx.select.content(
                                    rx.select.item("개인", value="personal"),
                                    rx.select.item("팀", value="team"),
                                ),
                                value=ChatState.upload_target,
                                on_change=ChatState.set_upload_target,
                            ),
                            align="center",
                            gap="0.5em",
                        ),
                        rx.spacer(),
                        rx.button(
                            rx.icon("upload", size=14),
                            rx.text("업로드", size="1"),
                            size="2",
                            variant="solid",
                            disabled=(ChatState.ingestion_status == "uploading")
                            | (ChatState.ingestion_status == "processing"),
                            cursor=rx.cond(
                                (ChatState.ingestion_status == "uploading")
                                | (ChatState.ingestion_status == "processing"),
                                "not-allowed",
                                "pointer",
                            ),
                            on_click=ChatState.confirm_upload_via_api,
                        ),
                        width="100%",
                        align="center",
                    ),
                ),
                # Ingestion 상태
                rx.cond(
                    ChatState.ingestion_status == "uploading",
                    rx.hstack(
                        rx.spinner(size="1"),
                        rx.text("파일 업로드 중...", size="1", color=COLORS["text_secondary"]),
                        align="center",
                        gap="0.5em",
                    ),
                ),
                rx.cond(
                    ChatState.ingestion_status == "processing",
                    rx.hstack(
                        rx.spinner(size="1"),
                        rx.text(
                            "지식베이스에 입력 중... (수 분 소요)",
                            size="1",
                            color=rx.color("amber", 9),
                        ),
                        align="center",
                        gap="0.5em",
                    ),
                ),
                rx.cond(
                    ChatState.ingestion_status == "ready",
                    rx.vstack(
                        rx.hstack(
                            rx.icon("circle-check", size=14, color=rx.color("green", 9)),
                            rx.text("지식베이스 처리 완료", size="1", color=rx.color("green", 9)),
                            align="center",
                            gap="0.4em",
                        ),
                        rx.cond(
                            ChatState.ingestion_error != "",
                            rx.text(
                                "⚠ " + ChatState.ingestion_error,
                                size="1",
                                color=rx.color("amber", 9),
                            ),
                        ),
                        spacing="1",
                    ),
                ),
                rx.cond(
                    ChatState.ingestion_status == "error",
                    rx.hstack(
                        rx.icon("circle-alert", size=14, color=rx.color("red", 9)),
                        rx.text(
                            ChatState.ingestion_error,
                            size="1",
                            color=rx.color("red", 9),
                        ),
                        align="center",
                        gap="0.4em",
                    ),
                ),
                spacing="2",
                width="100%",
            ),
            bg=COLORS["sidebar_bg"],
            border=f"1px solid {COLORS['border']}",
            border_radius=SPACING["border_radius_md"],
            padding="0.75em",
            width="100%",
            max_width=SPACING["message_max_width"],
            margin_x="auto",
            margin_bottom="0.5em",
        ),
    )


def ingestion_banner() -> rx.Component:
    """Ingestion 진행 중 배너 (업로드 패널 닫혔을 때)."""
    return rx.cond(
        (ChatState.ingestion_status == "processing") & (ChatState.active_panel != "upload"),
        rx.hstack(
            rx.spinner(size="1"),
            rx.text(
                "문서 처리 중... 완료 후 새 문서 검색이 가능합니다.",
                size="2",
                color=COLORS["text_primary"],
                weight="medium",
            ),
            align="center",
            spacing="2",
            padding="0.5em 0.9em",
            bg=COLORS["user_bubble"],
            border=f"1px solid {COLORS['input_border']}",
            border_radius="9999px",
            box_shadow="0 2px 8px rgba(0,0,0,0.08)",
            margin_x="auto",
            margin_bottom="0.5em",
        ),
    )
