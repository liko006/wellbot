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
    # 스크롤바만 rx.color() 가 아닌 CSS 변수 문자열이다 — GLOBAL_STYLE(전역 스타일시트)은
    # Var 를 해석하지 못해 str(rx.color(...)) 가 무효값이 되고 브라우저가 선언을 버린다
    # (폭만 잡히고 아무것도 안 그려짐). --gray-N 은 Radix 가 gray_color 설정(slate)에
    # 맞춰 정의하므로 테마 톤도 그대로 따라간다. rx.color() 로 되돌리지 말 것.
    "scrollbar_thumb": "var(--gray-8)",
    "scrollbar_thumb_hover": "var(--gray-10)",
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
        "background_color": "#cde9fb",
        "color": "#0a2540",
    },
    # 폭 10px 중 thumb 의 투명 테두리 2px×2 를 뺀 6px 이 실제로 보이는 두께다.
    "::-webkit-scrollbar": {
        "width": "10px",
        "height": "10px",
    },
    "::-webkit-scrollbar-track": {
        "background": "transparent",
    },
    # Chrome/Edge 전용 셀렉터(사내 표준 브라우저 기준). 색은 반드시 CSS 변수 문자열로
    # — COLORS 주석 참고.
    # 스크롤바에는 margin 이 없다(브라우저가 그리는 영역). 투명 테두리 + padding-box
    # 클립으로 안쪽만 칠해 화면 끝에서 떨어뜨린다.
    "::-webkit-scrollbar-thumb": {
        "background": COLORS["scrollbar_thumb"],
        "border": "2px solid transparent",
        "background_clip": "padding-box",
        "border_radius": "5px",
    },
    "::-webkit-scrollbar-thumb:hover": {
        "background": COLORS["scrollbar_thumb_hover"],
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
