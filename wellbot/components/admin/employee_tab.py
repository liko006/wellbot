"""사원 관리 탭 컴포넌트."""

import reflex as rx

from wellbot.components.admin import cell, col_header
from wellbot.constants import LOCK_THRESHOLD
from wellbot.state.admin_state import AdminState
from wellbot.styles import COLORS


def _emp_row(emp: dict) -> rx.Component:
    """사원 테이블 행."""
    return rx.table.row(
        cell(emp["emp_no"]),
        cell(emp["user_nm"]),
        cell(rx.badge(emp["user_role_nm"], size="1", variant="soft")),
        cell(emp["pstn_dept_cd"]),
        cell(
            rx.badge(
                emp["acnt_sts_nm"],
                size="1",
                variant="soft",
                color_scheme=rx.cond(
                    emp["acnt_sts_nm"] == "ACTIVE",
                    "green",
                    rx.cond(emp["acnt_sts_nm"] == "PENDING", "yellow", "red"),
                ),
            ),
        ),
        rx.table.cell(
            rx.hstack(
                # 가입 대기(PENDING) → 원클릭 승인
                rx.cond(
                    emp["acnt_sts_nm"] == "PENDING",
                    rx.icon_button(
                        rx.icon("user-check", size=14),
                        variant="ghost",
                        size="1",
                        cursor="pointer",
                        color_scheme="green",
                        title="승인 (PENDING → ACTIVE)",
                        on_click=AdminState.approve_employee(emp["emp_no"]),
                    ),
                ),
                # 로그인 실패 누적으로 잠긴 계정 → 원클릭 잠금 해제
                rx.cond(
                    emp["lgn_flr_tscnt"].to(int) >= LOCK_THRESHOLD,
                    rx.icon_button(
                        rx.icon("lock-open", size=14),
                        variant="ghost",
                        size="1",
                        cursor="pointer",
                        color_scheme="amber",
                        title="계정 잠금 해제",
                        on_click=AdminState.unlock_employee(emp["emp_no"]),
                    ),
                ),
                rx.icon_button(
                    rx.icon("pencil", size=14),
                    variant="ghost",
                    size="1",
                    cursor="pointer",
                    on_click=AdminState.open_edit_modal("employee", emp),
                ),
                rx.icon_button(
                    rx.icon("trash-2", size=14),
                    variant="ghost",
                    size="1",
                    cursor="pointer",
                    color_scheme="red",
                    on_click=AdminState.delete_employee(emp["emp_no"]),
                ),
                spacing="1",
                justify="center",
            ),
            text_align="center",
        ),
    )


def employee_modal() -> rx.Component:
    """사원 생성/수정 모달."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.cond(AdminState.is_editing, "사원 수정", "사원 추가"),
            ),
            rx.vstack(
                rx.cond(
                    AdminState.error_message != "",
                    rx.callout(
                        AdminState.error_message,
                        icon="triangle_alert",
                        color_scheme="red",
                        size="1",
                    ),
                ),
                rx.text("사원번호", size="2", weight="medium"),
                rx.input(
                    value=AdminState.form_data.get("emp_no", ""),
                    placeholder="예: 200XXXXX",
                    on_change=lambda v: AdminState.set_form_field("emp_no", v),
                    disabled=AdminState.is_editing,
                    max_length=15,
                ),
                rx.text("사용자명", size="2", weight="medium"),
                rx.input(
                    value=AdminState.form_data.get("user_nm", ""),
                    placeholder="홍길동",
                    on_change=lambda v: AdminState.set_form_field("user_nm", v),
                ),
                rx.text(
                    rx.cond(AdminState.is_editing, "비밀번호 (변경 시에만 입력)", "비밀번호"),
                    size="2",
                    weight="medium",
                ),
                rx.input(
                    value=AdminState.form_data.get("password", ""),
                    placeholder="••••••••",
                    type="password",
                    on_change=lambda v: AdminState.set_form_field("password", v),
                ),
                rx.text("역할", size="2", weight="medium"),
                rx.select(
                    ["ADMIN", "USER"],
                    value=AdminState.form_data.get("user_role_nm", "USER"),
                    on_change=lambda v: AdminState.set_form_field("user_role_nm", v),
                ),
                rx.text("소속 부서", size="2", weight="medium"),
                rx.select(
                    AdminState.dept_display_options,
                    value=AdminState.form_dept_display,
                    placeholder="부서 선택",
                    on_change=lambda v: AdminState.set_form_dept(v),
                ),
                rx.text("계정 상태", size="2", weight="medium"),
                rx.select(
                    ["ACTIVE", "PENDING", "LOCKED", "DISABLED"],
                    value=AdminState.form_data.get("acnt_sts_nm", "ACTIVE"),
                    on_change=lambda v: AdminState.set_form_field("acnt_sts_nm", v),
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.button("취소", variant="soft", color_scheme="gray"),
                    ),
                    rx.button("저장", on_click=AdminState.save_employee),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="480px",
        ),
        open=AdminState.show_modal & (AdminState.modal_mode == "employee"),
        on_open_change=lambda open: rx.cond(~open, AdminState.close_modal, None),  # type: ignore
    )


def employee_tab() -> rx.Component:
    """사원 관리 탭."""
    return rx.vstack(
        rx.hstack(
            rx.hstack(
                rx.heading("사원 관리", size="4"),
                rx.text("EMP_M", size="1", color=COLORS["text_secondary"]),
                align="end",
                spacing="2",
            ),
            rx.spacer(),
            rx.button(
                rx.icon("plus", size=16),
                "사원 추가",
                size="2",
                on_click=AdminState.open_create_modal("employee"),
            ),
            width="100%",
            align="center",
        ),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    col_header("사원번호", "EMP_NO"),
                    col_header("사용자명", "USER_NM"),
                    col_header("역할", "USER_ROLE_NM"),
                    col_header("소속부서", "PSTN_DEPT_CD"),
                    col_header("상태", "ACNT_STS_NM"),
                    rx.table.column_header_cell("작업"),
                ),
            ),
            rx.table.body(
                rx.foreach(AdminState.employees, _emp_row),
            ),
            width="100%",
            size="2",
        ),
        rx.cond(
            AdminState.employees.length() == 0,
            rx.center(
                rx.text("등록된 사원이 없습니다.", color=COLORS["text_secondary"], size="2"),
                padding="2em",
            ),
        ),
        employee_modal(),
        spacing="4",
        width="100%",
    )
