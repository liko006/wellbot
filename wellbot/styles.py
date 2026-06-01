"""디자인 토큰 및 테마 설정.

rx.color() 기반으로 다크/라이트 모드 자동 전환 지원.
"""

import reflex as rx

# rx.color() 기반 — 다크/라이트 모드 자동 전환
COLORS = {
    "sidebar_bg": rx.color("gray", 2),
    "sidebar_hover": rx.color("gray", 4),
    "sidebar_active": rx.color("gray", 5),
    "main_bg": rx.color("gray", 1),
    "user_bubble": rx.color("gray", 4),
    "ai_bubble": "transparent",
    "input_bg": rx.color("gray", 3),
    "input_border": rx.color("gray", 6),
    "text_primary": rx.color("gray", 12),
    "text_secondary": rx.color("gray", 10),
    "accent": rx.color("gray", 9),
    "accent_hover": rx.color("gray", 11),
    "border": rx.color("gray", 4),
    "tool_btn_bg": rx.color("gray", 4),
    "tool_btn_hover": rx.color("gray", 5),
    "category_text": rx.color("gray", 9),
    "scrollbar_thumb": rx.color("gray", 6),
}

SPACING = {
    "sidebar_width": "260px",
    "sidebar_collapsed_width": "60px",
    "input_bar_height": "100px",
    "message_max_width": "768px",
    "border_radius": "24px",
    "border_radius_sm": "8px",
    "border_radius_md": "16px",
    "gnb_height": "48px",
    "padding_page": "1.5em",
    "padding_component": "1em",
}

TYPOGRAPHY = {
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    "heading_size": "6",
    "body_size": "3",
    "small_size": "2",
}

GLOBAL_STYLE = {
    "font_family": TYPOGRAPHY["font_family"],
    "::selection": {
        "background_color": rx.color("accent", 5),
    },
    "::-webkit-scrollbar": {
        "width": "6px",
        "height": "6px",
    },
    "::-webkit-scrollbar-track": {
        "background": "transparent",
    },
    "::-webkit-scrollbar-thumb": {
        "background": str(COLORS["scrollbar_thumb"]),
        "border_radius": "3px",
    },
    "::-webkit-scrollbar-thumb:hover": {
        "background": str(rx.color("gray", 8)),
    },
    ".codeblock-wrapper pre": {
        "background": "transparent !important",
        "margin": "0 !important",
        "border_radius": "0 !important",
        "padding": "1em !important",
    },
    ".codeblock-wrapper pre code": {
        "background": "transparent !important",
    },
    ".codeblock-wrapper pre code span": {
        "background": "transparent !important",
    },
}

THEME = rx.theme(
    appearance="dark",
    has_background=True,
    radius="medium",
    accent_color="gray",
    gray_color="slate",
)


def _table_border() -> str:
    """테이블 border 색상 문자열"""
    return f"1px solid {rx.color('gray', 6)}"


def _custom_codeblock(value: object, **props) -> rx.Component:
    """코드블럭 - 언어 라벨 + 복사 버튼 헤더 포함"""
    from reflex.components.datadisplay.code import CodeBlock

    language = props.pop("language", "")
    return rx.box(
        # 헤더: 언어 라벨 + 복사 버튼
        rx.hstack(
            rx.hstack(
                rx.icon("code", size=14, color=rx.color("gray", 10)),
                rx.text(
                    language,
                    size="1",
                    weight="medium",
                    color=rx.color("gray", 10),
                    text_transform="capitalize",
                ),
                align="center",
                gap="0.4em",
            ),
            rx.tooltip(
                rx.el.button(
                    rx.icon("copy", size=14),
                    on_click=rx.set_clipboard(value),  # type: ignore
                    background="transparent",
                    border="none",
                    cursor="pointer",
                    color=str(rx.color("gray", 10)),
                    padding="0.25em",
                    border_radius="4px",
                    display="flex",
                    align_items="center",
                    _hover={"color": str(rx.color("gray", 12)), "background": str(rx.color("gray", 5))},
                ),
                content="복사",
            ),
            width="100%",
            padding_x="1em",
            padding_y="0.5em",
            align="center",
            justify="between",
            border_bottom=f"1px solid {rx.color('gray', 5)}",
        ),
        CodeBlock.create(value, wrap_long_lines=True, **props),
        background=rx.color("gray", 2),
        border_radius="8px",
        border=f"1px solid {rx.color('gray', 4)}",
        overflow="hidden",
        margin_y="0.75em",
        class_name="codeblock-wrapper",
    )


# rx.markdown 공통 component_map — 테이블 border·코드블럭 커스텀 적용
MARKDOWN_COMPONENT_MAP: dict = {
    "code": lambda text: rx.code(text, color_scheme="gray", variant="ghost"),
    "pre": _custom_codeblock,
    "table": lambda *children, **props: rx.el.table(
        *children,
        border_collapse="collapse",
        width="100%",
        margin_y="0.5em",
        font_size="0.875rem",
        **props,
    ),
    "th": lambda *children, **props: rx.el.th(
        *children,
        border=_table_border(),
        padding="0.5em 0.75em",
        text_align="left",
        font_weight="600",
        background=rx.color("gray", 3),
        **props,
    ),
    "td": lambda *children, **props: rx.el.td(
        *children,
        border=_table_border(),
        padding="0.5em 0.75em",
        **props,
    ),
    "tr": lambda *children, **props: rx.el.tr(
        *children,
        **props,
    ),
    "thead": lambda *children, **props: rx.el.thead(
        *children,
        **props,
    ),
    "tbody": lambda *children, **props: rx.el.tbody(
        *children,
        **props,
    ),
}
