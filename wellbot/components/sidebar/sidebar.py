"""Sidebar 컴포넌트.

ChatGPT 스타일 접이식 사이드바.
접힘: 아이콘만 표시 (60px), 펼침: 전체 표시 (260px).
"""

import reflex as rx

from wellbot.components.icons import north_star_icon
from wellbot.components.sidebar.conversation_list import conversation_list
from wellbot.state.auth_state import AuthState
from wellbot.state.chat_state import ChatState
from wellbot.state.ui_state import UIState
from wellbot.styles import COLORS, SPACING

_ICON_SIZE = 18
_ICON_BOX = "36px"


def _collapsed_icon(
    icon_name: str,
    on_click,
    tooltip_text: str,
) -> rx.Component:
    """접힌 상태 아이콘 버튼 (균일 크기)."""
    return rx.tooltip(
        rx.box(
            rx.icon(icon_name, size=_ICON_SIZE),
            display="flex",
            align_items="center",
            justify_content="center",
            width=_ICON_BOX,
            height=_ICON_BOX,
            border_radius="8px",
            cursor="pointer",
            color=COLORS["text_secondary"],
            _hover={
                "bg": COLORS["sidebar_hover"],
                "color": COLORS["text_primary"],
            },
            on_click=on_click,
        ),
        content=tooltip_text,
        side="right",
    )


def _nav_item(
    icon_name: str,
    label: str,
    on_click,
) -> rx.Component:
    """펼친 상태 네비게이션 항목 (아이콘 + 텍스트)."""
    return rx.hstack(
        rx.icon(icon_name, size=_ICON_SIZE, flex_shrink="0"),
        rx.text(
            label,
            size="2",
            weight="medium",
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
        ),
        align="center",
        spacing="3",
        padding_x="0.75em",
        padding_y="0.5em",
        width="100%",
        border_radius=SPACING["border_radius_sm"],
        cursor="pointer",
        color=COLORS["text_secondary"],
        _hover={
            "bg": COLORS["sidebar_hover"],
            "color": COLORS["text_primary"],
        },
        on_click=on_click,
    )


def _logo_expand_button() -> rx.Component:
    """접힌 상태 로고: hover 시 사이드바 열기 아이콘으로 전환."""
    return rx.tooltip(
        rx.box(
            rx.box(
                north_star_icon(size=_ICON_SIZE),
                class_name="logo-default",
                transition="opacity 0.15s ease",
            ),
            rx.box(
                rx.icon("panel-left-open", size=_ICON_SIZE),
                class_name="logo-hover",
                position="absolute",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
                opacity="0",
                transition="opacity 0.15s ease",
            ),
            position="relative",
            display="flex",
            align_items="center",
            justify_content="center",
            width=_ICON_BOX,
            height=_ICON_BOX,
            border_radius="8px",
            cursor="pointer",
            color=COLORS["text_primary"],
            on_click=UIState.expand_sidebar,
            _hover={
                "bg": COLORS["sidebar_hover"],
                "& .logo-default": {"opacity": "0"},
                "& .logo-hover": {"opacity": "1"},
            },
        ),
        content="사이드바 열기",
        side="right",
    )


def _expanded_header() -> rx.Component:
    """펼친 상태 헤더: 로고 + 닫기 버튼."""
    return rx.hstack(
        rx.hstack(
            rx.box(
                north_star_icon(size=_ICON_SIZE),
                color=COLORS["text_primary"],
            ),
            rx.spacer(),
            rx.text(
                "WellBot",
                size="4",
                weight="bold",
                color=COLORS["text_primary"],
                white_space="nowrap",
            ),
            align="center",
            spacing="2",
        ),
        rx.spacer(),
        rx.box(
            rx.icon("panel-left-close", size=_ICON_SIZE),
            display="flex",
            align_items="center",
            justify_content="center",
            width=_ICON_BOX,
            height=_ICON_BOX,
            border_radius="8px",
            cursor="pointer",
            color=COLORS["text_secondary"],
            on_click=UIState.collapse_sidebar,
            _hover={
                "bg": COLORS["sidebar_hover"],
                "color": COLORS["text_primary"],
            },
        ),
        width="100%",
        align="center",
        padding_x="0.75em",
        padding_y="0.625em",
    )



def _collapsed_nav() -> rx.Component:
    """접힌 상태 상단 네비게이션 (아이콘 세로 정렬)."""
    return rx.vstack(
        _logo_expand_button(),
        _collapsed_icon(
            "square-pen",
            ChatState.create_new_conversation,
            "새 채팅",
        ),
        _collapsed_icon(
            "search",
            UIState.open_search,
            "채팅 검색",
        ),
        rx.separator(color_scheme="gray", size="4", margin_y="0.25em"),
        _collapsed_icon(
            "layers-plus",
            rx.redirect("/ai-services"),
            "AI 업무 특화 서비스",
        ),
        spacing="1",
        align="center",
        width="100%",
        padding_x="0.75em",
        padding_y="0.625em",
    )


def user_profile() -> rx.Component:
    """인증된 사용자 정보 + 로그아웃."""
    user_avatar = rx.box(
        rx.icon("user", size=_ICON_SIZE, color=COLORS["text_primary"]),
        width="32px",
        height="32px",
        border_radius="50%",
        bg=COLORS["sidebar_hover"],
        display="flex",
        align_items="center",
        justify_content="center",
        flex_shrink="0",
    )

    # 비밀번호 변경 다이얼로그 (팝오버 밖에 위치해야 팝오버 닫힌 뒤에도 유지됨)
    change_pw_dialog = rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("비밀번호 변경", size="4"),
            rx.dialog.description(
                "",
                size="2",
                color=COLORS["text_secondary"],
                margin_bottom="1em",
            ),
            rx.cond(
                AuthState.chpw_success,
                rx.vstack(
                    rx.callout(
                        "비밀번호가 변경되었습니다.",
                        icon="check",
                        color_scheme="green",
                        width="100%",
                    ),
                    rx.flex(
                        rx.dialog.close(
                            rx.button(
                                "닫기",
                                variant="solid",
                                on_click=AuthState.close_change_password,
                            ),
                        ),
                        justify="end",
                        width="100%",
                        margin_top="1em",
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.form(
                    rx.vstack(
                        rx.vstack(
                            rx.text("현재 비밀번호", size="2", weight="medium"),
                            rx.input(
                                placeholder="현재 비밀번호 입력",
                                type="password",
                                value=AuthState.chpw_current,
                                on_change=AuthState.set_chpw_current,
                                width="100%",
                                auto_focus=True,
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("새 비밀번호", size="2", weight="medium"),
                            rx.input(
                                placeholder="새 비밀번호 입력",
                                type="password",
                                value=AuthState.chpw_new,
                                on_change=AuthState.set_chpw_new,
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("새 비밀번호 확인", size="2", weight="medium"),
                            rx.input(
                                placeholder="새 비밀번호 다시 입력",
                                type="password",
                                value=AuthState.chpw_confirm,
                                on_change=AuthState.set_chpw_confirm,
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.cond(
                            AuthState.chpw_error != "",
                            rx.callout(
                                AuthState.chpw_error,
                                icon="triangle_alert",
                                color_scheme="red",
                                width="100%",
                            ),
                        ),
                        rx.flex(
                            rx.button(
                                "취소",
                                type="button",
                                variant="soft",
                                color_scheme="gray",
                                on_click=AuthState.close_change_password,
                            ),
                            rx.button(
                                "변경하기",
                                type="submit",
                                loading=AuthState.is_changing_password,
                            ),
                            justify="end",
                            gap="0.5em",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    on_submit=AuthState.handle_change_password,
                    width="100%",
                ),
            ),
            max_width="400px",
        ),
        open=AuthState.show_change_password,
        on_open_change=lambda open: rx.cond(~open, AuthState.close_change_password, None),  # type: ignore
    )

    def _popover_menu() -> rx.Component:
        return rx.vstack(
            rx.vstack(
                rx.text(
                    AuthState.current_user_nm,
                    size="2",
                    weight="medium",
                ),
                rx.text(
                    AuthState.current_emp_no,
                    size="1",
                    color=COLORS["text_secondary"],
                ),
                spacing="0",
                padding="0.25em 0.5em",
            ),
            rx.separator(size="4"),
            rx.cond(
                AuthState.current_user_role == "ADMIN",
                rx.hstack(
                    rx.icon("settings-2", size=14),
                    rx.text("관리자 페이지", size="2"),
                    align="center",
                    spacing="2",
                    padding="0.5em 0.75em",
                    width="100%",
                    border_radius="6px",
                    cursor="pointer",
                    color=COLORS["text_secondary"],
                    _hover={
                        "bg": COLORS["sidebar_hover"],
                        "color": COLORS["text_primary"],
                    },
                    on_click=rx.redirect("/admin"),
                ),
            ),
            rx.popover.close(
                rx.hstack(
                    rx.icon("key-round", size=14),
                    rx.text("비밀번호 변경", size="2"),
                    align="center",
                    spacing="2",
                    padding="0.5em 0.75em",
                    width="100%",
                    border_radius="6px",
                    cursor="pointer",
                    color=COLORS["text_secondary"],
                    _hover={
                        "bg": COLORS["sidebar_hover"],
                        "color": COLORS["text_primary"],
                    },
                    on_click=AuthState.open_change_password,
                ),
                width="100%",
            ),
            rx.hstack(
                rx.icon("log-out", size=14),
                rx.text("로그아웃", size="2"),
                align="center",
                spacing="2",
                padding="0.5em 0.75em",
                width="100%",
                border_radius="6px",
                cursor="pointer",
                color=COLORS["text_secondary"],
                _hover={
                    "bg": COLORS["sidebar_hover"],
                    "color": COLORS["text_primary"],
                },
                on_click=AuthState.logout,
            ),
            spacing="1",
            width="100%",
        )

    return rx.fragment(
        change_pw_dialog,
        rx.cond(
            UIState.sidebar_expanded,
            rx.popover.root(
                rx.popover.trigger(
                    rx.hstack(
                        user_avatar,
                        rx.vstack(
                            rx.text(
                                AuthState.current_user_nm,
                                size="2",
                                color=COLORS["text_primary"],
                                weight="medium",
                            ),
                            rx.text(
                                AuthState.current_emp_no,
                                size="1",
                                color=COLORS["text_secondary"],
                            ),
                            spacing="0",
                        ),
                        rx.spacer(),
                        rx.icon(
                            "ellipsis",
                            size=_ICON_SIZE,
                            color=COLORS["text_secondary"],
                        ),
                        width="100%",
                        align="center",
                        spacing="2",
                        padding="0.75em",
                        border_radius=SPACING["border_radius_sm"],
                        cursor="pointer",
                        _hover={"bg": COLORS["sidebar_hover"]},
                    ),
                ),
                rx.popover.content(
                    _popover_menu(),
                    side="top",
                    align="start",
                    max_width="240px",
                ),
            ),
            rx.center(
                rx.popover.root(
                    rx.popover.trigger(
                        rx.box(
                            user_avatar,
                            cursor="pointer",
                        ),
                    ),
                    rx.popover.content(
                        _popover_menu(),
                        side="right",
                        align="end",
                        min_width="200px",
                    ),
                ),
                width="100%",
                padding="0.75em",
            ),
        ),
    )


def sidebar_footer() -> rx.Component:
    """사이드바 하단 영역."""
    return rx.box(
        rx.separator(color_scheme="gray", size="4"),
        rx.box(
            user_profile(),
            padding_x="0.5em",
            padding_y="0.5em",
        ),
        width="100%",
        flex_shrink="0",
    )


def sidebar() -> rx.Component:
    """Sidebar 메인 컴포넌트."""
    return rx.box(
        rx.vstack(
            rx.cond(
                UIState.sidebar_expanded,
                rx.vstack(
                    _expanded_header(),
                    rx.box(
                        _nav_item(
                            "square-pen",
                            "새 채팅",
                            ChatState.create_new_conversation,
                        ),
                        _nav_item(
                            "search",
                            "채팅 검색",
                            UIState.open_search,
                        ),
                        rx.separator(color_scheme="gray", size="4", margin_y="0.25em"),
                        _nav_item(
                            "layers-plus",
                            "AI 업무 특화 서비스",
                            rx.redirect("/ai-services"),
                        ),
                        padding_x="0.5em",
                        width="100%",
                    ),
                    spacing="0",
                    width="100%",
                ),
                _collapsed_nav(),
            ),
            rx.cond(
                UIState.sidebar_expanded,
                conversation_list(),
                rx.box(flex="1"),
            ),
            sidebar_footer(),
            height="100%",
            width="100%",
            spacing="0",
        ),
        bg=COLORS["sidebar_bg"],
        width=rx.cond(
            UIState.sidebar_expanded,
            SPACING["sidebar_width"],
            SPACING["sidebar_collapsed_width"],
        ),
        min_width=rx.cond(
            UIState.sidebar_expanded,
            SPACING["sidebar_width"],
            SPACING["sidebar_collapsed_width"],
        ),
        max_width=rx.cond(
            UIState.sidebar_expanded,
            SPACING["sidebar_width"],
            SPACING["sidebar_collapsed_width"],
        ),
        height="100vh",
        border_right=f"1px solid {COLORS['border']}",
        display="flex",
        flex_direction="column",
        overflow="hidden",
        transition="width 0.2s ease, min-width 0.2s ease, max-width 0.2s ease",
        on_click=ChatState.close_panel,
    )
