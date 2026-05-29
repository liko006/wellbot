"""로그인 페이지.

사원번호 + 비밀번호 입력으로 DB 인증 후 세션 토큰 발급.
"""

import reflex as rx

from wellbot.components.icons import north_star_icon
from wellbot.state.auth_state import AuthState
from wellbot.styles import COLORS


def _notice_section() -> rx.Component:
    """config/notice.md 기반 공지사항 영역."""
    return rx.cond(
        AuthState.notice_html != "",
        rx.box(
            rx.el.div(
                rx.markdown(AuthState.notice_html),
            ),
            width="380px",
            padding="1em 1.5em",
            border_radius="var(--radius-3)",
            background="var(--color-panel)",
            border="1px solid var(--gray-a5)",
            margin_top="1em",
            max_height="200px",
            overflow_y="auto",
            opacity="0.9",
        ),
    )


def login_page() -> rx.Component:
    """로그인 페이지."""
    return rx.box(
        rx.center(
            rx.vstack(
                rx.card(
                    rx.form(
                        rx.vstack(
                            rx.vstack(
                                rx.box(
                                    north_star_icon(size=48),
                                    color=COLORS["accent"],
                                    on_click=AuthState.handle_easter_egg_click,
                                ),
                                rx.heading("WellBot", size="6"),
                                rx.text(
                                    "사원번호와 비밀번호를 입력하세요.",
                                    size="2",
                                    color=COLORS["text_secondary"],
                                ),
                                align="center",
                                spacing="2",
                            ),
                            rx.cond(
                                AuthState.login_error != "",
                                rx.callout(
                                    AuthState.login_error,
                                    icon="triangle_alert",
                                    color_scheme="red",
                                    size="1",
                                    width="100%",
                                ),
                            ),
                            rx.vstack(
                                rx.text("사원번호", size="2", weight="medium"),
                                rx.input(
                                    value=AuthState.login_emp_no,
                                    placeholder="사원번호를 입력하세요",
                                    on_change=AuthState.set_login_emp_no,
                                    max_length=15,
                                    width="100%",
                                    auto_focus=True,
                                ),
                                rx.hstack(
                                    rx.box(flex="1"),
                                    rx.checkbox(
                                        "기억하기",
                                        checked=AuthState.remember_me,
                                        on_change=AuthState.toggle_remember_me,
                                        size="1",
                                    ),
                                    width="100%",
                                    align="center",
                                    margin_bottom="-16px",
                                ),
                                rx.text("비밀번호", size="2", weight="medium"),
                                rx.input(
                                    value=AuthState.login_password,
                                    placeholder="비밀번호를 입력하세요",
                                    type="password",
                                    on_change=AuthState.set_login_password,
                                    width="100%",
                                ),
                                spacing="2",
                                width="100%",
                            ),
                            rx.button(
                                rx.cond(
                                    AuthState.is_logging_in,
                                    rx.hstack(
                                        rx.spinner(size="3"),
                                        rx.text("로그인 중..."),
                                        align="center",
                                        spacing="2",
                                    ),
                                    rx.text("로그인"),
                                ),
                                width="100%",
                                type="submit",
                                disabled=AuthState.is_logging_in,
                            ),
                            rx.center(
                                rx.link(
                                    "사용자 등록 신청",
                                    href="/register",
                                    size="2",
                                    color=COLORS["text_secondary"],
                                ),
                            ),
                            spacing="4",
                            width="100%",
                            align="center",
                        ),
                        on_submit=AuthState.handle_login,
                        reset_on_submit=False,
                    ),
                    width="380px",
                    padding="2em",
                ),
                _notice_section(),
                align="center",
                spacing="0",
            ),
            min_height="100vh",
            padding_y="2em",
        ),
        bg=COLORS["main_bg"],
        min_height="100vh",
    )
