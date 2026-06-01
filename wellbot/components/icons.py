"""커스텀 아이콘 컴포넌트.

Lucide에 없는 아이콘을 SVG로 직접 정의.
"""

import math

import reflex as rx


def north_star_icon(size: int = 20, **props) -> rx.Component:
    """8갈래 별 아이콘 (둥근 끝, 가로 강조)"""
    center = size / 2
    # 가로/세로 갈래는 길게, 대각선 갈래는 짧게 설정해 가로 강조 효과
    long = size * 0.46
    short = size * 0.32
    stroke_w = size * 0.1

    lines: list[rx.Component] = []
    for i in range(8):
        angle = math.radians(i * 45)
        length = long if i % 2 == 0 else short
        x2 = center + math.cos(angle) * length
        y2 = center - math.sin(angle) * length
        lines.append(
            rx.el.line(
                x1=str(center),
                y1=str(center),
                x2=str(round(x2, 2)),
                y2=str(round(y2, 2)),
                stroke="currentColor",
                stroke_width=str(round(stroke_w, 2)),
                stroke_linecap="round",
            )
        )

    return rx.el.svg(
        *lines,
        width=f"{size}px",
        height=f"{size}px",
        view_box=f"0 0 {size} {size}",
        **props,
    )
