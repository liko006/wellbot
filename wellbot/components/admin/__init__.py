"""Admin UI 컴포넌트."""

import reflex as rx

from wellbot.styles import COLORS

_CELL_BORDER = f"1px solid {rx.color('gray', 5)}"


def col_header(label: str, col_name: str) -> rx.Component:
    """테이블 헤더: 한글 라벨 + (DB 컬럼명)."""
    return rx.table.column_header_cell(
        rx.vstack(
            rx.text(label, size="2", weight="medium"),
            rx.text(
                f"({col_name})",
                size="1",
                color=COLORS["text_secondary"],
                weight="regular",
                font_size="10px",
            ),
            spacing="0",
            align="center",
        ),
        text_align="center",
        border_right=_CELL_BORDER,
    )


def cell(content: rx.Component | rx.Var, **kwargs) -> rx.Component:
    """데이터 셀: 중앙 정렬 + border."""
    return rx.table.cell(
        content,
        text_align="center",
        vertical_align="middle",
        border_right=_CELL_BORDER,
        **kwargs,
    )
