"""Admin 관리 페이지.

.env 비밀번호 또는 DB ADMIN 계정으로 인증 후 부서/사원/에이전트를 관리.
"""

import reflex as rx

from wellbot.components.admin.agent_tab import agent_tab
from wellbot.components.admin.dept_tab import dept_tab
from wellbot.components.admin.employee_tab import employee_tab
from wellbot.state.admin_state import AdminState
from wellbot.styles import COLORS


def _login_form() -> rx.Component:
    """관리자 로그인 폼."""
    return rx.center(
        rx.card(
            rx.form(
                rx.vstack(
                    rx.vstack(
                        rx.icon("shield-check", size=48, color=COLORS["accent"]),
                        rx.heading("관리자 인증", size="5"),
                        rx.text(
                            "SUPER/ADMIN 인증",
                            size="2",
                            color=COLORS["text_secondary"],
                        ),
                        align="center",
                        spacing="2",
                    ),
                    rx.cond(
                        AdminState.auth_error != "",
                        rx.callout(
                            AdminState.auth_error,
                            icon="triangle_alert",
                            color_scheme="red",
                            size="1",
                            width="100%",
                        ),
                    ),
                    rx.vstack(
                        rx.text("사원번호", size="2", weight="medium"),
                        rx.input(
                            value=AdminState.auth_emp_no,
                            placeholder="사원번호",
                            on_change=AdminState.set_auth_emp_no,
                            max_length=15,
                            width="100%",
                        ),
                        rx.text("비밀번호", size="2", weight="medium"),
                        rx.input(
                            value=AdminState.auth_password,
                            placeholder="비밀번호",
                            type="password",
                            on_change=AdminState.set_auth_password,
                            width="100%",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.button(
                        "로그인",
                        width="100%",
                        type="submit",
                    ),
                    spacing="4",
                    width="100%",
                    align="center",
                ),
                on_submit=lambda _: AdminState.check_admin_auth(),
                reset_on_submit=False,
            ),
            width="360px",
            padding="2em",
        ),
        height="100vh",
    )


def _admin_content() -> rx.Component:
    """관리 화면 콘텐츠."""
    return rx.vstack(
        rx.hstack(
            rx.hstack(
                rx.icon("settings-2", size=24),
                rx.heading("WellBot 관리", size="5"),
                spacing="2",
                align="center",
            ),
            rx.spacer(),
            rx.hstack(
                rx.badge(
                    AdminState.admin_label,
                    size="2",
                    variant="surface",
                    height="28px",
                ),
                rx.cond(
                    AdminState.admin_label != "SUPER",
                    rx.button(
                        rx.icon("message-circle", size=14),
                        "챗으로 이동",
                        variant="soft",
                        color_scheme="blue",
                        size="2",
                        height="28px",
                        on_click=rx.redirect("/"),
                    ),
                ),
                rx.button(
                    rx.icon("log-out", size=14),
                    "로그아웃",
                    variant="soft",
                    color_scheme="gray",
                    size="2",
                    height="28px",
                    on_click=AdminState.admin_logout,
                ),
                rx.icon_button(
                    rx.color_mode_cond(
                        light=rx.icon("moon", size=14),
                        dark=rx.icon("sun", size=14),
                    ),
                    variant="ghost",
                    size="2",
                    height="28px",
                    width="28px",
                    cursor="pointer",
                    on_click=rx.toggle_color_mode,
                ),
                spacing="2",
                align="center",
            ),
            width="100%",
            align="center",
            padding_bottom="1em",
            border_bottom=f"1px solid {COLORS['border']}",
        ),
        rx.cond(
            AdminState.success_message != "",
            rx.callout(
                AdminState.success_message,
                icon="check",
                color_scheme="green",
                size="1",
            ),
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("부서 관리", value="dept"),
                rx.tabs.trigger("사원 관리", value="employee"),
                rx.tabs.trigger("에이전트 관리", value="agent"),
            ),
            rx.tabs.content(dept_tab(), value="dept", padding_top="1em"),
            rx.tabs.content(employee_tab(), value="employee", padding_top="1em"),
            rx.tabs.content(agent_tab(), value="agent", padding_top="1em"),
            value=AdminState.active_tab,
            on_change=AdminState.set_active_tab,
            width="100%",
        ),
        spacing="4",
        width="100%",
        max_width="1200px",
        margin="0 auto",
        padding="2em",
    )


def admin_page() -> rx.Component:
    """Admin 페이지."""
    return rx.box(
        rx.cond(
            AdminState.is_authenticated,
            _admin_content(),
            _login_form(),
        ),
        bg=COLORS["main_bg"],
        min_height="100vh",
    )
