"""파일 확장자 기반 아이콘 helper.

KB 업로드 패널, 문서 목록, 출처 chip 등 여러 곳에서 동일한 아이콘 셋을 사용해
시각적 일관성을 유지하도록 한 곳에서 매핑.

attachment_chip 의 MIME 기반 아이콘과 같은 lucide 아이콘 셋 사용.
"""

import reflex as rx

from wellbot.styles import COLORS


def file_icon_by_name(name, size: int = 14) -> rx.Component:
    """파일명 확장자에 따른 lucide 아이콘 반환.

    Args:
        name: 파일명 (rx.Var[str] 또는 str)
        size: 아이콘 크기 (기본 14)

    Note:
        message_bubble 의 source_docs 처럼 list[dict] 항목 접근(doc["title"])으로
        들어오는 Var 는 untyped 라서 .lower() 등 string 메서드 호출 시 Reflex
        타입 추론이 깨짐. 명시적으로 .to(str) 변환하여 typed string Var 로
        만들고 호출.
    """
    color = COLORS["text_secondary"]
    lname = name.to(str).lower()
    return rx.cond(
        lname.endswith(".pdf"),
        rx.icon("file-text", size=size, color=color),
        rx.cond(
            lname.endswith(".docx"),
            rx.icon("file-pen-line", size=size, color=color),
            rx.cond(
                lname.endswith(".pptx"),
                rx.icon("file-chart-pie", size=size, color=color),
                rx.cond(
                    lname.endswith(".xlsx") | lname.endswith(".csv"),
                    rx.icon("table-2", size=size, color=color),
                    rx.cond(
                        lname.endswith(".md")
                        | lname.endswith(".json")
                        | lname.endswith(".html")
                        | lname.endswith(".htm"),
                        rx.icon("file-code", size=size, color=color),
                        rx.cond(
                            lname.endswith(".txt"),
                            rx.icon("file-type", size=size, color=color),
                            rx.icon("file", size=size, color=color),
                        ),
                    ),
                ),
            ),
        ),
    )
