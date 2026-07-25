"""AI 서비스 카탈로그 페이지.

config/ai_services.yaml 의 서비스 목록을 카드 그리드로 표시.
사이드바를 유지하기 위해 chat_layout 으로 래핑.
"""

import reflex as rx

from wellbot.components.layout import chat_layout
from wellbot.services.core.settings import AIServiceConfig, get_ai_services
from wellbot.styles import COLORS, SPACING


def _service_card(svc: AIServiceConfig) -> rx.Component:
    """AI 서비스 카드 한 개.

    route 가 비었거나 enabled=False 면 '준비 중'으로 표시하고 토스트만 띄움.
    """
    ready = svc.enabled and bool(svc.route)
    on_click = (
        rx.redirect(svc.route, is_external=svc.external)
        if ready
        else rx.toast.info("준비 중인 서비스입니다.")
    )

    header_children = [
        rx.box(
            rx.icon(svc.icon, size=22, color=COLORS["text_primary"]),
            display="flex",
            align_items="center",
            justify_content="center",
            width="44px",
            height="44px",
            border_radius=SPACING["border_radius_sm"],
            bg=COLORS["sidebar_hover"],
            flex_shrink="0",
        ),
        rx.spacer(),
    ]
    if not ready:
        header_children.append(
            rx.badge("준비 중", color_scheme="gray", variant="soft", size="1"),
        )

    return rx.box(
        rx.vstack(
            rx.hstack(*header_children, width="100%", align="center"),
            rx.text(
                svc.name,
                size="4",
                weight="bold",
                color=COLORS["text_primary"],
            ),
            rx.text(
                svc.description,
                size="2",
                color=COLORS["text_secondary"],
                line_height="1.5",
            ),
            align="start",
            spacing="3",
            width="100%",
            height="100%",
        ),
        on_click=on_click,
        cursor="pointer",
        padding="1.25em",
        border=f"1px solid {COLORS['border']}",
        border_radius=SPACING["border_radius_md"],
        bg=COLORS["sidebar_bg"],
        height="100%",
        transition="border-color 0.15s ease, background 0.15s ease",
        _hover={
            "border_color": COLORS["text_secondary"],
            "bg": COLORS["sidebar_hover"],
        },
    )


def _service_grid() -> rx.Component:
    """서비스 카드 그리드 (없으면 빈 상태)."""
    services = get_ai_services()
    if not services:
        return rx.center(
            rx.text(
                "등록된 AI 서비스가 없습니다.",
                size="2",
                color=COLORS["text_secondary"],
            ),
            width="100%",
            padding_y="4em",
        )
    return rx.box(
        *[_service_card(s) for s in services],
        display="grid",
        grid_template_columns="repeat(auto-fill, minmax(280px, 1fr))",
        gap="1em",
        width="100%",
    )


def ai_services_page() -> rx.Component:
    """AI 서비스 카탈로그 페이지."""
    return chat_layout(
        rx.box(
            rx.vstack(
                rx.vstack(
                    rx.heading(
                        "AI 업무 특화 서비스",
                        size="7",
                        color=COLORS["text_primary"],
                    ),
                    rx.text(
                        "업무에 활용할 수 있는 AI 기반 서비스 모음입니다.",
                        size="3",
                        color=COLORS["text_secondary"],
                    ),
                    spacing="1",
                    align="start",
                    width="100%",
                ),
                _service_grid(),
                spacing="6",
                width="100%",
                max_width="1100px",
                margin="0 auto",
            ),
            width="100%",
            height="100%",
            overflow_y="auto",
            padding="2.5em 2em",
        )
    )
