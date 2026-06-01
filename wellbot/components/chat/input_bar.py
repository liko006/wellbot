"""메시지 입력 바 컴포넌트.

ChatGPT/Claude 스타일 입력 바.
파일 첨부 버튼 + 텍스트 입력 + 모델 선택 팝오버 + 전송 버튼.

모델 선택 팝오버: 모델 목록 + 확장 사고 토글을 하나의 드롭다운으로 통합.
"""

import reflex as rx

from wellbot.components.chat.attachment_chip import attachment_chip_list
from wellbot.state.chat_models import ModelInfo, PromptInfo
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, SPACING


def _model_item(model: ModelInfo) -> rx.Component:
    """팝오버 내 개별 모델 항목."""
    return rx.popover.close(
        rx.hstack(
            rx.vstack(
                rx.text(
                    model.name,
                    size="1",
                    weight="medium",
                ),
                rx.text(
                    model.description,
                    font_size="11px",
                    color=COLORS["text_secondary"],
                ),
                spacing="0",
                align_items="start",
            ),
            rx.spacer(),
            # 체크 아이콘 자리를 항상 확보하여 정렬 유지
            rx.box(
                rx.cond(
                    model.name == ChatState.selected_model,
                    rx.icon("check", size=14, color=COLORS["accent"]),
                ),
                width="16px",
                display="flex",
                align_items="center",
                justify_content="center",
                flex_shrink="0",
            ),
            width="100%",
            align="center",
            padding="0.35em 0.6em",
            border_radius=SPACING["border_radius_sm"],
            cursor="pointer",
            _hover={"bg": COLORS["sidebar_hover"]},
            on_click=ChatState.set_model(model.name),
        ),
        # popover.close에도 width 100% 적용
        width="100%",
    )


def _thinking_toggle_row() -> rx.Component:
    """확장 사고 토글 행. 미지원 모델에서는 비활성화 상태로 표시"""
    return rx.hstack(
        rx.vstack(
            rx.text(
                "확장 사고",
                size="1",
                weight="medium",
                color=rx.cond(
                    ChatState.model_supports_thinking,
                    COLORS["text_primary"],
                    COLORS["text_secondary"],
                ),
            ),
            rx.text(
                rx.cond(
                    ChatState.model_supports_thinking,
                    "복잡한 작업을 위해 더 오래 사고",
                    "확장 사고 미지원 모델",
                ),
                font_size="11px",
                color=COLORS["text_secondary"],
            ),
            spacing="0",
            align_items="start",
            flex="1",
            min_width="0",
        ),
        rx.switch(
            checked=rx.cond(
                ChatState.model_supports_thinking,
                ChatState.thinking_enabled,
                False,
            ),
            on_change=ChatState.toggle_thinking,
            disabled=~ChatState.model_supports_thinking,
            size="1",
            flex_shrink="0",
        ),
        width="100%",
        align="center",
        gap="0.5em",
        padding="0.35em 0.6em",
        opacity=rx.cond(ChatState.model_supports_thinking, "1", "0.5"),
    )


def _plus_menu_item(
    icon_name: str,
    label: str,
    on_click: rx.EventHandler | None = None,
) -> rx.Component:
    """+ 메뉴 내 개별 항목."""
    item = rx.hstack(
        rx.icon(icon_name, size=16, color=COLORS["text_secondary"]),
        rx.text(label, size="2"),
        align="center",
        gap="0.6em",
        padding="0.5em 0.75em",
        width="100%",
        border_radius=SPACING["border_radius_sm"],
        cursor="pointer",
        _hover={"bg": COLORS["sidebar_hover"]},
    )
    return rx.popover.close(
        item,
        width="100%",
        **({"on_click": on_click} if on_click else {}),
    )


def _plus_menu_popover() -> rx.Component:
    """+ 버튼 팝오버 메뉴 (파일첨부, 지식베이스, 스타일)."""
    return rx.popover.root(
        rx.popover.trigger(
            rx.icon_button(
                rx.icon("plus", size=16),
                variant="ghost",
                size="2",
                cursor="pointer",
                color=COLORS["text_secondary"],
                _hover={
                    "color": COLORS["text_primary"],
                    "bg": COLORS["tool_btn_hover"],
                },
                border_radius="50%",
                type="button",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                _plus_menu_item(
                    "paperclip",
                    "파일 추가",
                    on_click=ChatState.trigger_upload,
                ),
                _plus_menu_item("database-search", "지식베이스"),
                _plus_menu_item("paintbrush", "스타일", on_click=ChatState.toggle_style_panel),
                spacing="1",
                width="100%",
            ),
            side="top",
            align="start",
            style={
                "padding": "0.5em",
                "border_radius": SPACING["border_radius_md"],
                "bg": COLORS["sidebar_bg"],
                "border": f"1px solid {COLORS['border']}",
                "box_shadow": "0 4px 24px rgba(0,0,0,0.25)",
            },
        ),
    )


def _model_popover() -> rx.Component:
    """Claude 스타일 모델 선택 팝오버."""
    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                rx.text(
                    ChatState.trigger_label,
                    size="1",
                    weight="medium",
                ),
                rx.icon("chevron-down", size=14),
                variant="ghost",
                size="1",
                cursor="pointer",
                color=COLORS["text_secondary"],
                _hover={"color": COLORS["text_primary"]},
                type="button",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.foreach(ChatState.model_list, _model_item),
                rx.separator(size="4", color=COLORS["border"]),
                _thinking_toggle_row(),
                spacing="1",
                width="100%",
            ),
            side="top",
            align="end",
            style={
                "width": "250px",
                "padding": "0.4em",
                "border_radius": SPACING["border_radius_md"],
                "bg": COLORS["sidebar_bg"],
                "border": f"1px solid {COLORS['border']}",
                "box_shadow": "0 4px 24px rgba(0,0,0,0.25)",
            },
        ),
    )


def _style_prompt_item(prompt: PromptInfo) -> rx.Component:
    """스타일 패널 내 개별 프롬프트 항목."""
    return rx.hstack(
        rx.hstack(
            rx.text(prompt.name, size="2", weight="medium"),
            rx.text(
                prompt.description,
                size="1",
                color=COLORS["text_secondary"],
            ),
            align="baseline",
            gap="0.5em",
        ),
        rx.spacer(),
        rx.box(
            rx.cond(
                prompt.name == ChatState.selected_prompt,
                rx.icon("check", size=14, color=COLORS["accent"]),
            ),
            width="16px",
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        width="100%",
        align="center",
        padding="0.5em 0.75em",
        border_radius=SPACING["border_radius_sm"],
        cursor="pointer",
        bg=rx.cond(
            prompt.name == ChatState.selected_prompt,
            COLORS["sidebar_hover"],
            "transparent",
        ),
        _hover={"bg": COLORS["sidebar_hover"]},
        on_click=ChatState.select_prompt(prompt.name),
    )


def _style_panel() -> rx.Component:
    """스타일(시스템 프롬프트) 선택 패널."""
    return rx.cond(
        ChatState.show_style_panel,
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.icon("paintbrush", size=14, color=COLORS["text_secondary"]),
                    rx.text("스타일 선택", size="2", weight="medium"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("x", size=14),
                        variant="ghost",
                        size="1",
                        cursor="pointer",
                        color=COLORS["text_secondary"],
                        on_click=ChatState.toggle_style_panel,
                    ),
                    width="100%",
                    align="center",
                ),
                rx.separator(size="4", color=COLORS["border"]),
                rx.foreach(ChatState.prompt_list, _style_prompt_item),
                spacing="2",
                width="100%",
            ),
            bg=COLORS["sidebar_bg"],
            border=f"1px solid {COLORS['border']}",
            border_radius=SPACING["border_radius_md"],
            padding="0.75em",
            width="100%",
            max_width=SPACING["message_max_width"],
            margin_x="auto",
            margin_bottom="0.5em",
        ),
    )


def _processing_toast() -> rx.Component:
    """첨부 분석 중 안내 — 입력 박스 직상단의 persistent pill."""
    return rx.cond(
        ChatState.has_processing_attachments,
        rx.hstack(
            rx.spinner(size="1"),
            rx.text(
                "첨부 파일 처리 중... 완료 후 전송할 수 있습니다.",
                size="2",
                color=COLORS["text_primary"],
                weight="medium",
            ),
            align="center",
            spacing="2",
            padding="0.5em 0.9em",
            bg=COLORS["user_bubble"],
            border=f"1px solid {COLORS['input_border']}",
            border_radius="9999px",
            box_shadow="0 2px 8px rgba(0,0,0,0.08)",
            margin_x="auto",
            margin_bottom="0.5em",
        ),
    )


def input_bar() -> rx.Component:
    """하단 고정 메시지 입력 바."""
    return rx.box(
        rx.vstack(
            _style_panel(),
            _processing_toast(),
            rx.box(
                rx.form(
                    rx.vstack(
                        attachment_chip_list(),
                        rx.text_area(
                            value=ChatState.current_input,
                            placeholder="WellBot에게 질문하세요!",
                            on_change=ChatState.set_input,
                            enter_key_submit=True,
                            auto_height=True,
                            variant="soft",
                            style={
                                "width": "100%",
                                "background": "transparent",
                                "box_shadow": "none",
                                "color": COLORS["text_primary"],
                                "font_size": "0.9375rem",
                                "line_height": "1.5",
                                "outline": "none",
                                "resize": "none",
                                "min_height": "24px",
                                "max_height": "150px",
                                "overflow_y": "auto",
                                "padding": "0",
                                "& textarea::placeholder": {
                                    "color": COLORS["text_secondary"],
                                },
                            },
                        ),
                        rx.hstack(
                            _plus_menu_popover(),
                            rx.spacer(),
                            _model_popover(),
                            rx.cond(
                                ChatState.is_loading,
                                rx.icon_button(
                                    rx.icon("square", size=14),
                                    size="2",
                                    variant="solid",
                                    type="button",
                                    cursor="pointer",
                                    border_radius="50%",
                                    bg=COLORS["text_primary"],
                                    color=COLORS["main_bg"],
                                    _hover={"bg": COLORS["accent_hover"]},
                                    on_click=ChatState.stop_generation,
                                ),
                                rx.icon_button(
                                    rx.icon("arrow-up", size=16),
                                    size="2",
                                    variant="solid",
                                    type="submit",
                                    disabled=~ChatState.can_send,
                                    cursor=rx.cond(
                                        ChatState.can_send,
                                        "pointer",
                                        "not-allowed",
                                    ),
                                    border_radius="50%",
                                    bg=rx.cond(
                                        ChatState.can_send,
                                        COLORS["text_primary"],
                                        COLORS["tool_btn_bg"],
                                    ),
                                    color=rx.cond(
                                        ChatState.can_send,
                                        COLORS["main_bg"],
                                        COLORS["text_secondary"],
                                    ),
                                    _hover={
                                        "bg": rx.cond(
                                            ChatState.can_send,
                                            COLORS["accent_hover"],
                                            COLORS["tool_btn_bg"],
                                        ),
                                    },
                                ),
                            ),
                            width="100%",
                            align="center",
                            spacing="2",
                        ),
                        spacing="2",
                        padding="0.75em 1em",
                    ),
                    on_submit=ChatState.send_message,
                ),
                bg=COLORS["input_bg"],
                border_radius=SPACING["border_radius"],
                border=f"1px solid {COLORS['input_border']}",
                width="100%",
                max_width=SPACING["message_max_width"],
                margin_x="auto",
                _focus_within={
                    "border_color": COLORS["accent_hover"],
                },
            ),
            rx.text(
                "WellBot은 실수할 수 있습니다. WellBot의 출력 결과를 확인하고 활용하세요.",
                size="1",
                color=COLORS["text_secondary"],
                text_align="center",
            ),
            spacing="2",
            width="100%",
            align="center",
        ),
        width="100%",
        padding_x="1em",
        padding_top="0.75em",
        padding_bottom="1em",
        bg=COLORS["main_bg"],
        flex_shrink="0",
    )
