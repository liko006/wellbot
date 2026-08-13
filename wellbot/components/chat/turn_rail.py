"""턴 네비게이터 — 채팅 우측 레일.

질문 1개 = 짧은 가로 틱 1개. 현재 보고 있는 질문의 틱이 진해지고, 레일에 마우스를
올리면 질문 목록 팝업이 열린다. 틱이나 목록 항목을 클릭하면 그 질문이 채팅 최상단으로.

**점등·클릭·팝업 스크롤은 전부 `pages/index.py` 의 클라이언트 스크립트가 담당한다.**
이 컴포넌트는 목록을 그리기만 하고 상태 왕복을 만들지 않는다(스크롤할 때마다 서버를
때리면 타자 랙·스트리밍 끊김이 재발한다). JS 와의 접점은 다음 세 가지뿐:

  · `#turn-rail`         — 컨테이너(팝업 열림 감지용)
  · `.turn-tick[data-turn]` / `.turn-row[data-turn]` — 틱·목록 항목, 값은 질문 순번
  · `.is-active`         — JS 가 붙였다 떼는 점등 클래스

ChatState 를 직접 읽지 않고 목록을 prop 으로 받는다 — 보고서 draft 페이지처럼 다른
대화 UI 에 붙일 때 컴포넌트를 그대로 재사용하기 위함.
"""

import reflex as rx

from wellbot.state.chat_models import TurnInfo
from wellbot.styles import COLORS, SPACING

_TICK_WIDTH = "14px"
_TICK_HEIGHT = "2px"
_POPUP_WIDTH = "260px"


def _tick(item: rx.Var[TurnInfo]) -> rx.Component:
    """질문 1개에 대응하는 가로 틱.

    보이는 두께는 2px 지만 상하 padding 으로 클릭 영역을 8px 로 넓힌다
    (배경은 content-box 에만 칠해 두께는 그대로).
    """
    return rx.box(
        class_name="turn-tick",
        custom_attrs={"data-turn": item.index},
        style={
            "width": _TICK_WIDTH,
            "height": _TICK_HEIGHT,
            "padding": "3px 0",
            "background": "var(--gray-7)",
            "background_clip": "content-box",
            "border_radius": "1px",
            "cursor": "pointer",
            "flex_shrink": "0",
            "transition": "background 0.12s ease",
        },
    )


def _more_indicator() -> rx.Component:
    """틱 상한을 넘어 접힌 질문이 있음을 알리는 표시.

    넘치는 건 항상 과거 질문이므로 레일 **위쪽**에 둔다. 클릭 동작은 없다
    (팝업과 기능이 겹치고 오조작 위험만 생긴다). 사용자가 접힌 구간을 보고 있으면
    JS 가 여기에 `.is-active` 를 붙인다 — 아무 틱도 안 켜지는 상태를 막는다.
    """
    return rx.box(
        "···",
        class_name="turn-more",
        style={
            "font_size": "11px",
            "line_height": "1",
            "color": "var(--gray-7)",
            "padding": "2px 0 4px 0",
            "cursor": "default",
            "user_select": "none",
            "transition": "color 0.12s ease",
        },
    )


def _popup_row(item: rx.Var[TurnInfo]) -> rx.Component:
    """팝업의 질문 1행. 폭 고정 + ellipsis 로 자른다.

    글자 수로 자르지 않는 이유: 한글과 영문의 실제 폭이 크게 달라 목록 오른쪽 끝이
    들쭉날쭉해진다. CSS 로 자르면 언어와 무관하게 가지런하다.
    """
    return rx.box(
        item.text,
        class_name="turn-row",
        custom_attrs={"data-turn": item.index},
        style={
            "padding": "0.4em 0.8em",
            "font_size": "12px",
            "line_height": "1.4",
            "color": COLORS["text_secondary"],
            "white_space": "nowrap",
            "overflow": "hidden",
            "text_overflow": "ellipsis",
            "cursor": "pointer",
        },
    )


@rx.memo
def turn_rail(
    rail_items: rx.Var[list[TurnInfo]],
    all_items: rx.Var[list[TurnInfo]],
    has_overflow: rx.Var[bool],
) -> rx.Component:
    """턴 레일 + 호버 팝업.

    ``rx.memo`` 로 감싼 이유(성능): 팝업이 로드된 **전체** 질문을 들고 있어
    "이전 대화 더 보기" 를 반복하면 100~200행이 된다. 메모가 없으면 이 목록이
    스트리밍 중 streaming_content 갱신 주기(0.08초)마다 함께 리렌더된다.
    prop 인 질문 목록은 스트리밍 중 바뀌지 않으므로, 메모하면 리렌더가 **턴이
    추가될 때만** 발생한다.
    """
    return rx.box(
        rx.cond(has_overflow, _more_indicator(), rx.fragment()),
        rx.foreach(rail_items, _tick),
        # 호버 팝업 — 열림은 CSS 가, 활성 항목으로의 스크롤은 JS 가 담당
        rx.box(
            rx.foreach(all_items, _popup_row),
            id="turn-rail-popup",
            class_name="turn-popup",
            style={
                "position": "absolute",
                "right": "calc(100% + 10px)",
                "top": "50%",
                "transform": "translateY(-50%)",
                "width": _POPUP_WIDTH,
                "max_height": "40vh",
                "overflow_y": "auto",
                "background": COLORS["sidebar_bg"],
                "border": f"1px solid {COLORS['border']}",
                "border_radius": SPACING["border_radius_sm"],
                "padding": "0.3em 0",
                "box_shadow": "0 8px 24px rgba(0, 0, 0, 0.35)",
                "display": "none",
            },
        ),
        id="turn-rail",
        style={
            "position": "relative",
            "display": "flex",
            "flex_direction": "column",
            "align_items": "center",
            # 틱이 얇아 hover 판정이 까다로우므로 좌우 여유를 준다
            "padding": "2px 6px",
            "margin_bottom": "4px",
            # 점등(.is-active)은 JS 가 클래스로 토글한다. 색은 CSS 변수 문자열로 —
            # Radix 가 gray_color 설정에 맞춰 정의하므로 테마 톤을 따라간다.
            "& .turn-tick:hover": {"background": "var(--gray-10)"},
            "& .turn-tick.is-active": {"background": "var(--gray-12)"},
            "& .turn-more.is-active": {"color": "var(--gray-12)"},
            # 현재 위치·마우스 오버를 같은 배경으로 (마우스 위치로 구분되고,
            # 영역을 벗어나면 팝업 자체가 닫히므로 혼동이 없다)
            "& .turn-row:hover, & .turn-row.is-active": {
                "background": "var(--gray-4)",
                "color": "var(--gray-12)",
            },
            "&:hover .turn-popup": {"display": "block"},
        },
    )
