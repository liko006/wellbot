"""KB 정리(teardown) 패널.

'지식베이스' 탭 좌측 레일에서 정리 항목을 고르면 우측에 나타난다. 개인/팀 KB 전체
삭제와 공용 폴더 삭제가 같은 화면을 쓴다 — 대상 지정 → 미리보기 → 확인 타이핑 →
단계별 진행.

되돌릴 수 없는 작업이라 화면도 그렇게 생겼다: 미리보기 전에는 실행 버튼이 없고,
대상 이름을 그대로 입력해야 열린다.
"""

import reflex as rx

from wellbot.state.kb_cleanup_state import (
    MODE_FOLDER,
    MODE_PERSONAL,
    MODE_TEAM,
    CleanupStepRow,
    KbCleanupState,
    PreviewRow,
)
from wellbot.styles import COLORS, SPACING

# 좌측 레일의 정리 항목 (라벨, 모드, 아이콘)
CLEANUP_ITEMS = [
    ("개인 KB 정리", MODE_PERSONAL, "user-x"),
    ("팀 KB 정리", MODE_TEAM, "users"),
    ("공용 폴더 삭제", MODE_FOLDER, "folder-x"),
]


def _target_input() -> rx.Component:
    """대상 입력. 공용 폴더는 목록에서 고르게 해 오타로 엉뚱한 대상을 막는다."""
    return rx.cond(
        KbCleanupState.is_folder_mode,
        rx.select(
            KbCleanupState.folder_options,
            value=KbCleanupState.target,
            placeholder="대분류 선택",
            on_change=KbCleanupState.set_target,
            width="260px",
        ),
        rx.input(
            value=KbCleanupState.target,
            placeholder=KbCleanupState.target_label,
            on_change=KbCleanupState.set_target,
            width="260px",
        ),
    )


def _preview_row(row: PreviewRow) -> rx.Component:
    return rx.hstack(
        rx.text(row.label, size="1", color=COLORS["text_secondary"], width="160px"),
        rx.text(row.value, size="1", weight="medium"),
        width="100%",
        align="center",
        gap="0.5em",
    )


def _preview_card() -> rx.Component:
    return rx.cond(
        KbCleanupState.has_preview,
        rx.box(
            rx.vstack(
                rx.text("삭제 대상", size="2", weight="medium"),
                rx.foreach(KbCleanupState.preview, _preview_row),
                spacing="2",
                width="100%",
                align="start",
            ),
            width="100%",
            padding="1em",
            border=f"1px solid {COLORS['border']}",
            border_radius=SPACING["border_radius_sm"],
        ),
    )


def _confirm_block() -> rx.Component:
    """확인 타이핑 + 실행. 미리보기 후에만, 지울 것이 있을 때만 나타난다."""
    return rx.cond(
        KbCleanupState.has_preview,
        rx.cond(
            KbCleanupState.nothing_to_delete,
            rx.callout(
                "지울 것이 없습니다. 대상이 맞는지 확인해 주세요.",
                icon="info",
                size="1",
                width="100%",
            ),
            rx.vstack(
                rx.callout(
                    "되돌릴 수 없습니다. 위 목록이 전부 삭제됩니다.",
                    icon="triangle_alert",
                    color_scheme="red",
                    size="1",
                    width="100%",
                ),
                rx.text(KbCleanupState.confirm_hint, size="1", color=COLORS["text_secondary"]),
                rx.hstack(
                    rx.input(
                        value=KbCleanupState.confirm_text,
                        placeholder="대상 이름 입력",
                        on_change=KbCleanupState.set_confirm_text,
                        width="260px",
                    ),
                    rx.button(
                        rx.cond(
                            KbCleanupState.running,
                            rx.spinner(size="1"),
                            rx.text("삭제 실행"),
                        ),
                        color_scheme="red",
                        disabled=~KbCleanupState.can_delete,
                        on_click=KbCleanupState.run_cleanup,
                    ),
                    align="center",
                    gap="0.5em",
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
        ),
    )


def _step_row(step: CleanupStepRow) -> rx.Component:
    return rx.hstack(
        rx.icon(step.icon, size=14, color=rx.color(step.color, 9), flex_shrink="0"),
        rx.text(step.name, size="1", weight="medium", width="180px", flex_shrink="0"),
        rx.badge(step.label, size="1", variant="soft", color_scheme=step.color),
        rx.text(
            step.detail,
            size="1",
            color=COLORS["text_secondary"],
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
            flex="1",
            min_width="0",
        ),
        width="100%",
        align="center",
        gap="0.5em",
        padding_y="0.25em",
    )


def _progress() -> rx.Component:
    return rx.cond(
        KbCleanupState.steps.length() > 0,
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.text("진행", size="2", weight="medium"),
                    rx.cond(KbCleanupState.running, rx.spinner(size="1")),
                    align="center",
                    gap="0.5em",
                ),
                rx.foreach(KbCleanupState.steps, _step_row),
                rx.cond(
                    KbCleanupState.finished,
                    rx.cond(
                        KbCleanupState.failed,
                        rx.callout(
                            "실패한 단계에서 멈췄습니다. 원인을 해결한 뒤 다시 실행하면 "
                            "남은 것부터 이어서 정리됩니다.",
                            icon="triangle_alert",
                            color_scheme="red",
                            size="1",
                            width="100%",
                        ),
                        rx.callout(
                            "정리를 마쳤습니다.",
                            icon="check",
                            color_scheme="green",
                            size="1",
                            width="100%",
                        ),
                    ),
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
            width="100%",
            padding="1em",
            border=f"1px solid {COLORS['border']}",
            border_radius=SPACING["border_radius_sm"],
        ),
    )


def cleanup_panel() -> rx.Component:
    """정리 화면 (우측 본문)."""
    return rx.vstack(
        rx.hstack(
            rx.icon("trash-2", size=16, color=COLORS["text_secondary"]),
            rx.heading(KbCleanupState.mode_label, size="3"),
            align="center",
            gap="0.4em",
        ),
        rx.cond(
            KbCleanupState.error != "",
            rx.callout(
                KbCleanupState.error,
                icon="triangle_alert",
                color_scheme="red",
                size="1",
                width="100%",
            ),
        ),
        rx.hstack(
            _target_input(),
            rx.button(
                rx.cond(
                    KbCleanupState.loading_preview,
                    rx.spinner(size="1"),
                    rx.text("미리보기"),
                ),
                size="2",
                variant="soft",
                disabled=KbCleanupState.running | KbCleanupState.loading_preview,
                on_click=KbCleanupState.load_preview,
            ),
            align="center",
            gap="0.5em",
            wrap="wrap",
        ),
        _preview_card(),
        _confirm_block(),
        _progress(),
        flex="1",
        min_width="0",
        spacing="3",
        padding_left="1em",
        align="start",
    )
