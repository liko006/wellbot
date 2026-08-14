"""채팅 메인 페이지.

2단 레이아웃(Sidebar + 메시지 영역 + 입력 바) 채택.
자동 스크롤 스크립트를 페이지 레벨에서 초기화.
"""

import reflex as rx

from wellbot.components.chat.gnb import chat_gnb
from wellbot.components.chat.input_bar import input_bar
from wellbot.components.chat.message_area import message_area, navigation_rail
from wellbot.components.layout import chat_layout
from wellbot.constants import BTN_THRESHOLD, SCROLL_THRESHOLD
from wellbot.state.chat_helpers.upload_script import (
    CLIENT_LOG_SCRIPT,
    KB_UPLOAD_SCRIPT,
    PASTE_UPLOAD_SCRIPT,
)


# 자동 스크롤 + 턴 네비게이터 JavaScript (MutationObserver + setInterval 폴링 방식)
# - 사용자가 위로 스크롤하면 자동 스크롤 중단, 하단 근처에서만 재개
# - "맨 아래로" 버튼 표시/숨김 제어
# - 턴 레일(components/chat/turn_rail.py) 점등·클릭 이동·팝업 위치 동기화
#
# 레일 관련 처리를 전부 여기(클라이언트)에 두는 이유: 스크롤 위치를 서버 state 로
# 올리면 스크롤할 때마다 왕복이 생겨 타자 랙·스트리밍 끊김이 재발한다.
AUTO_SCROLL_SCRIPT = """
(function initAutoScroll() {
    var SCROLL_THRESHOLD = __SCROLL_THRESHOLD__;
    var BTN_THRESHOLD = __BTN_THRESHOLD__;
    var NAV_TOLERANCE = 8;
    var NAV_OFFSET = 12;
    var MUTATION_DEBOUNCE_MS = 200;
    // 점등 판정선의 위치(화면 높이 대비). 이 선에 걸린 질문의 틱이 켜진다.
    // 값이 크면 다음 질문으로 일찍 넘어가고(= 이전 답변이 조금만 남아도 전환),
    // 작으면 늦게 넘어간다. 단 이 값이 '한 턴의 평균 높이'보다 커지면, 틱/목록을
    // 클릭해 질문을 맨 위로 올렸을 때 곧바로 다음 틱이 켜지는 역전이 생긴다.
    // 답변이 짧은 대화일수록 그 경계가 낮아지므로 화면의 1/4 정도로 둔다.
    var TURN_ANCHOR_RATIO = 0.25;

    var SETUP_VERSION = 6;

    // 팝업 열림 시 현재 질문이 보이도록 스크롤. el 이나 setup 상태에 의존하지 않아
    // setup() 바깥에 둔다(설정 재시도로 리스너가 중복 등록되지 않게).
    document.addEventListener('mouseover', function(e) {
        if (!e.target.closest) return;
        var rail = e.target.closest('#turn-rail');
        if (!rail || rail._popupSynced) return;
        rail._popupSynced = true;
        var popup = rail.querySelector('.turn-popup');
        if (!popup) return;
        var active = popup.querySelector('.turn-row.is-active');
        if (!active || popup.scrollHeight <= popup.clientHeight) return;
        popup.scrollTop = active.offsetTop - (popup.clientHeight - active.offsetHeight) / 2;
    });

    document.addEventListener('mouseout', function(e) {
        if (!e.target.closest) return;
        var rail = e.target.closest('#turn-rail');
        if (rail && !rail.contains(e.relatedTarget)) rail._popupSynced = false;
    });

    function setup() {
        var el = document.getElementById('message-area');
        if (!el) return false;

        // 이미 동일 버전으로 설정된 경우 스킵 (버전 다르면 재설정)
        if (el._asReadyVersion === SETUP_VERSION) return true;
        el._asReadyVersion = SETUP_VERSION;

        var userScrolledUp = false;

        function distFromBottom() {
            return el.scrollHeight - el.scrollTop - el.clientHeight;
        }

        function scrollToBottom(smooth) {
            if (smooth) {
                el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
            } else {
                el.scrollTop = el.scrollHeight;
            }
        }

        function setBtnEnabled(btn, enabled) {
            if (!btn) return;
            btn.style.opacity = enabled ? '1' : '0.35';
            btn.style.pointerEvents = enabled ? 'auto' : 'none';
            btn.style.cursor = enabled ? 'pointer' : 'default';
        }

        // ── 턴 네비게이터 ──
        // 질문 버블에만 data-role="user" 가 붙어 있어, DOM 순서 = 턴 순번이 된다.
        // 레일 틱/목록 행의 data-turn 도 같은 순번이라 별도 매핑이 필요 없다.
        function getTurns() {
            return Array.from(el.querySelectorAll('.chat-msg[data-role="user"]'));
        }

        function currentTurnIndex(turns) {
            // 바닥에 붙어 있으면 무조건 최신 질문으로 본다.
            // 마지막 질문+답변이 한 화면을 못 채우면 viewport 상단에는 '직전 답변'이
            // 걸리므로, 앵커 규칙만 쓰면 맨 아래에 있는데 바로 위 틱이 켜져 어색하다.
            if (distFromBottom() < BTN_THRESHOLD) return turns.length - 1;

            // 판정선(화면 위에서 TURN_ANCHOR_RATIO 만큼 내려온 지점)에 걸린 질문.
            // offsetTop 이 anchor 이하인 마지막 질문 = 그 선을 소유한 턴.
            // 선을 맨 위(NAV_OFFSET)에 두면 다음 질문이 화면 한가운데 보이는데도
            // 이전 답변 꼬리가 남아 있는 한 이전 틱이 켜져 있어 어색하다.
            var anchor = el.scrollTop
                + Math.max(NAV_OFFSET + NAV_TOLERANCE,
                           el.clientHeight * TURN_ANCHOR_RATIO);
            var idx = -1;
            for (var i = 0; i < turns.length; i++) {
                if (turns[i].offsetTop <= anchor) idx = i;
                else break;
            }
            return idx; // -1: 첫 질문보다 위
        }

        function scrollToTurn(idx) {
            var turns = getTurns();
            if (!turns[idx]) return;
            // 진행 중인 답변으로 다시 끌려가지 않도록 자동 추종을 끊는다.
            userScrolledUp = true;
            el.scrollTo({
                top: Math.max(0, turns[idx].offsetTop - NAV_OFFSET),
                behavior: 'smooth'
            });
        }

        var lastActiveTurn = -2;  // -1(첫 질문 위)과 구분되는 초기값
        var activeRaf = 0;

        function applyActiveTurn(idx) {
            var rail = document.getElementById('turn-rail');
            if (!rail) return;
            var ticks = rail.querySelectorAll('.turn-tick');
            var matched = false;
            for (var i = 0; i < ticks.length; i++) {
                var on = parseInt(ticks[i].dataset.turn, 10) === idx;
                ticks[i].classList.toggle('is-active', on);
                if (on) matched = true;
            }
            // 상한을 넘어 접힌 구간을 보고 있으면 '···' 을 대신 켠다(무점등 방지).
            var more = rail.querySelector('.turn-more');
            if (more) more.classList.toggle('is-active', !matched && idx >= 0);
            var rows = rail.querySelectorAll('.turn-row');
            for (var j = 0; j < rows.length; j++) {
                rows[j].classList.toggle(
                    'is-active', parseInt(rows[j].dataset.turn, 10) === idx
                );
            }
        }

        function updateActiveTurn() {
            if (!document.getElementById('turn-rail')) return;  // 질문이 적으면 레일 없음
            var turns = getTurns();
            if (turns.length === 0) return;
            var idx = currentTurnIndex(turns);
            if (idx === lastActiveTurn) return;  // 변화 없으면 DOM 을 건드리지 않는다
            lastActiveTurn = idx;
            applyActiveTurn(idx);
        }

        // offsetTop 읽기는 레이아웃을 강제하므로 프레임당 1회로 묶는다.
        function scheduleActiveTurn() {
            if (activeRaf) return;
            activeRaf = requestAnimationFrame(function() {
                activeRaf = 0;
                updateActiveTurn();
            });
        }

        function updateBtn() {
            var bottomBtn = document.getElementById('scroll-to-bottom-btn');
            var atBottom = distFromBottom() < BTN_THRESHOLD;
            var hasScroll = el.scrollHeight > el.clientHeight + 1;
            setBtnEnabled(bottomBtn, hasScroll && !atBottom);
        }

        el.addEventListener('scroll', function() {
            var dist = distFromBottom();
            if (dist >= SCROLL_THRESHOLD) {
                userScrolledUp = true;
            } else if (dist < BTN_THRESHOLD) {
                userScrolledUp = false;
            }
            updateBtn();
            scheduleActiveTurn();
        });

        document.addEventListener('click', function(e) {
            if (!e.target.closest) return;
            if (e.target.closest('#scroll-to-bottom-btn')) {
                userScrolledUp = false;
                scrollToBottom(true);
                updateBtn();
                return;
            }
            var hit = e.target.closest('.turn-tick, .turn-row');
            if (hit && hit.dataset.turn !== undefined) {
                scrollToTurn(parseInt(hit.dataset.turn, 10));
            }
        });

        var mutTimer = 0;
        var observer = new MutationObserver(function() {
            if (!userScrolledUp) {
                scrollToBottom();
            }
            updateBtn();
            // 점등은 여기서 즉시 하지 않는다 — 스트리밍 중 토큰마다 발화하는데
            // offsetTop 순회는 레이아웃을 강제해 비싸다. 디바운스 후 1회만.
            clearTimeout(mutTimer);
            mutTimer = setTimeout(function() {
                lastActiveTurn = -2;  // 리렌더로 클래스가 날아갔을 수 있어 캐시 무효화
                updateActiveTurn();
            }, MUTATION_DEBOUNCE_MS);
        });

        observer.observe(el, {
            childList: true,
            subtree: true,
            characterData: true,
        });

        // 대화 전환 시 호출: userScrolledUp 리셋 + 스크롤
        window.__resetAutoScroll = function() {
            userScrolledUp = false;
            scrollToBottom();
            updateBtn();
            lastActiveTurn = -2;
            updateActiveTurn();
        };

        scrollToBottom();
        updateBtn();
        updateActiveTurn();
        return true;
    }

    // DOM이 준비될 때까지 폴링
    if (!setup()) {
        var attempts = 0;
        var timer = setInterval(function() {
            attempts++;
            if (setup() || attempts > 50) {
                clearInterval(timer);
            }
        }, 100);
    }
})();
""".replace("__SCROLL_THRESHOLD__", str(SCROLL_THRESHOLD)).replace("__BTN_THRESHOLD__", str(BTN_THRESHOLD))


def chat_main() -> rx.Component:
    """메인 대화 영역: GNB + 메시지 표시 + 입력 바 + 우측 네비 레일."""
    return rx.box(
        rx.vstack(
            chat_gnb(),
            message_area(),
            input_bar(),
            height="100%",
            width="100%",
            spacing="0",
        ),
        navigation_rail(),
        height="100%",
        width="100%",
        position="relative",
    )


def index_page() -> rx.Component:
    """채팅 메인 페이지."""
    return rx.fragment(
        rx.script(AUTO_SCROLL_SCRIPT),
        # KB 업로드 관련 JS — 컴포넌트 mount 타이밍 이슈 회피를 위해 페이지 레벨에서 한 번만 정의
        rx.script(KB_UPLOAD_SCRIPT),
        # 클립보드 이미지 붙여넣기 업로드 JS (paste 리스너 + window-global)
        rx.script(PASTE_UPLOAD_SCRIPT),
        # 클라이언트 오류 비콘 — 브라우저 측 실패를 /api/client_log 로 전송.
        # PASTE 스크립트의 _wellbotBackendBase 를 재사용하므로 그 뒤에 등록.
        rx.script(CLIENT_LOG_SCRIPT),
        chat_layout(chat_main()),
    )
