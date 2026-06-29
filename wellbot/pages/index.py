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
    KB_UPLOAD_SCRIPT,
    PASTE_UPLOAD_SCRIPT,
)


# 자동 스크롤 JavaScript (MutationObserver + setInterval 폴링 방식)
# - 사용자가 위로 스크롤하면 자동 스크롤 중단, 하단 근처에서만 재개
# - "맨 아래로" 버튼 표시/숨김 제어 포함
AUTO_SCROLL_SCRIPT = """
(function initAutoScroll() {
    var SCROLL_THRESHOLD = __SCROLL_THRESHOLD__;
    var BTN_THRESHOLD = __BTN_THRESHOLD__;
    var NAV_TOLERANCE = 8;
    var NAV_OFFSET = 12;

    var SETUP_VERSION = 4;

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

        function getMessages() {
            return Array.from(el.querySelectorAll('.chat-msg'));
        }

        function currentMsgIndex(msgs) {
            // viewport 상단(= scrollTop + NAV_OFFSET)에 앵커된 메시지 인덱스.
            // offsetTop이 anchor 이하인 마지막 메시지 = 현재 보고 있는 메시지.
            var anchor = el.scrollTop + NAV_OFFSET + NAV_TOLERANCE;
            var idx = -1;
            for (var i = 0; i < msgs.length; i++) {
                if (msgs[i].offsetTop <= anchor) idx = i;
                else break;
            }
            return idx; // -1: 첫 메시지보다 위
        }

        function navPrev() {
            var msgs = getMessages();
            if (msgs.length === 0) return;
            var idx = currentMsgIndex(msgs);
            var target = idx > 0 ? idx - 1 : 0;
            el.scrollTo({ top: Math.max(0, msgs[target].offsetTop - NAV_OFFSET), behavior: 'smooth' });
        }

        function navNext() {
            var msgs = getMessages();
            if (msgs.length === 0) return;
            var idx = currentMsgIndex(msgs);
            var target = Math.min(msgs.length - 1, idx + 1);
            el.scrollTo({ top: msgs[target].offsetTop - NAV_OFFSET, behavior: 'smooth' });
        }

        function updateBtn() {
            var bottomBtn = document.getElementById('scroll-to-bottom-btn');
            var prevBtn = document.getElementById('nav-prev-btn');
            var nextBtn = document.getElementById('nav-next-btn');

            var atBottom = distFromBottom() < BTN_THRESHOLD;
            var atTop = el.scrollTop <= NAV_TOLERANCE;
            var hasScroll = el.scrollHeight > el.clientHeight + 1;

            setBtnEnabled(bottomBtn, hasScroll && !atBottom);
            setBtnEnabled(prevBtn, hasScroll && !atTop);
            setBtnEnabled(nextBtn, hasScroll && !atBottom);
        }

        el.addEventListener('scroll', function() {
            var dist = distFromBottom();
            if (dist >= SCROLL_THRESHOLD) {
                userScrolledUp = true;
            } else if (dist < BTN_THRESHOLD) {
                userScrolledUp = false;
            }
            updateBtn();
        });

        document.addEventListener('click', function(e) {
            if (e.target.closest('#scroll-to-bottom-btn')) {
                userScrolledUp = false;
                scrollToBottom(true);
                updateBtn();
            } else if (e.target.closest('#nav-prev-btn')) {
                userScrolledUp = true;
                navPrev();
            } else if (e.target.closest('#nav-next-btn')) {
                navNext();
            }
        });

        var observer = new MutationObserver(function() {
            if (!userScrolledUp) {
                scrollToBottom();
            }
            updateBtn();
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
        };

        scrollToBottom();
        updateBtn();
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
        chat_layout(chat_main()),
    )
