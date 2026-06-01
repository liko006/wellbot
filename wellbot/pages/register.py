"""회원가입 페이지.

PENDING 상태로 사원을 등록하고, 관리자 승인 후 로그인 가능.
"""

import reflex as rx

from wellbot.state.auth_state import AuthState
from wellbot.styles import COLORS


def _register_form() -> rx.Component:
    """회원가입 폼."""
    return rx.form(
        rx.vstack(
            rx.vstack(
                rx.icon("user-plus", size=48, color=COLORS["accent"]),
                rx.heading("사용자 등록 신청", size="6"),
                rx.text(
                    "신청 후 관리자 승인이 필요합니다.",
                    size="2",
                    color=COLORS["text_secondary"],
                ),
                align="center",
                spacing="2",
            ),
            rx.cond(
                AuthState.reg_error != "",
                rx.callout(
                    AuthState.reg_error,
                    icon="triangle_alert",
                    color_scheme="red",
                    size="1",
                    width="100%",
                ),
            ),
            rx.vstack(
                rx.text("사원번호", size="2", weight="medium"),
                rx.input(
                    value=AuthState.reg_emp_no,
                    placeholder="사원번호를 입력하세요",
                    on_change=AuthState.set_reg_emp_no,
                    max_length=15,
                    width="100%",
                    auto_focus=True,
                ),
                rx.text("이름", size="2", weight="medium"),
                rx.input(
                    value=AuthState.reg_user_nm,
                    placeholder="이름을 입력하세요",
                    on_change=AuthState.set_reg_user_nm,
                    max_length=50,
                    width="100%",
                ),
                rx.text("비밀번호", size="2", weight="medium"),
                rx.input(
                    value=AuthState.reg_password,
                    placeholder="비밀번호 (8자 이상)",
                    type="password",
                    on_change=AuthState.set_reg_password,
                    width="100%",
                ),
                rx.text("비밀번호 확인", size="2", weight="medium"),
                rx.input(
                    value=AuthState.reg_password_confirm,
                    placeholder="비밀번호를 다시 입력하세요",
                    type="password",
                    on_change=AuthState.set_reg_password_confirm,
                    width="100%",
                ),
                rx.text("소속 부서", size="2", weight="medium"),
                rx.select(
                    AuthState.reg_dept_names,
                    value=AuthState.reg_dept_display,
                    placeholder="부서를 선택하세요",
                    on_change=AuthState.set_reg_dept,
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
            rx.button(
                rx.cond(
                    AuthState.is_registering,
                    rx.hstack(
                        rx.spinner(size="3"),
                        rx.text("등록 중..."),
                        align="center",
                        spacing="2",
                    ),
                    rx.text("사용자 등록 신청"),
                ),
                width="100%",
                type="submit",
                disabled=AuthState.is_registering,
            ),
            rx.center(
                rx.link(
                    "이미 계정이 있으신가요? 로그인",
                    href="/login",
                    size="2",
                    color=COLORS["text_secondary"],
                ),
            ),
            spacing="4",
            width="100%",
            align="center",
        ),
        on_submit=AuthState.handle_register,
        reset_on_submit=False,
    )


def _success_message() -> rx.Component:
    """가입 완료 메시지."""
    return rx.vstack(
        rx.icon("circle-check", size=48, color=rx.color("green", 9)),
        rx.heading("등록 신청 완료", size="5"),
        rx.text(
            "관리자 승인 후 로그인할 수 있습니다.",
            size="2",
            color=COLORS["text_secondary"],
            text_align="center",
        ),
        rx.button(
            "로그인 페이지로",
            width="100%",
            variant="soft",
            on_click=rx.redirect("/login"),
        ),
        align="center",
        spacing="3",
    )


def register_page() -> rx.Component:
    """회원가입 페이지."""
    return rx.box(
        rx.center(
            rx.card(
                rx.cond(
                    AuthState.reg_success,
                    _success_message(),
                    _register_form(),
                ),
                width="380px",
                padding="2em",
            ),
            height="100vh",
        ),
        bg=COLORS["main_bg"],
        min_height="100vh",
    )
