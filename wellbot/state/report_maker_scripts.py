"""보고서 문구 작성 지원 — 브라우저 업로드 JS (페이지 레벨 window-global).

report_checker_scripts 와 동일한 backendBase 결정 규칙을 사용한다.
  - reportMakerPickAndUpload(template, kind): 단일 파일 선택 → 업로드
    → {key, filename, error} 반환(취소 시 {key:'', error:''}). (주제 첨부용)
  - reportMakerPickAndUploadMany(template, kind): 다중 파일 선택 → 순차 업로드
    → [{key, filename, error}, ...] 반환(취소 시 []). (참고 문서 학습용)
정적 스크립트로, 페이지에서 rx.script 로 1회 등록한다.
"""

from __future__ import annotations

REPORT_MAKER_SCRIPT = """
window._rmBackendBase = window._rmBackendBase || async function() {
    try {
        var envResp = await fetch('/env.json');
        var env = await envResp.json();
        var pingUrl = env.PING || '';
        if (pingUrl) {
            var u = new URL(pingUrl);
            var loc = window.location;
            var isLocalDev = (
                (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') &&
                (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
                u.port !== loc.port
            );
            if (isLocalDev) return loc.protocol + '//' + loc.hostname + ':' + u.port;
        }
    } catch (e) {}
    return '';
};

// 단일 파일 업로드(공유 헬퍼). extra(object)의 필드도 함께 전송. 결과 dict 반환.
window._rmUploadOne = window._rmUploadOne || async function(file, template, kind, extra) {
    var form = new FormData();
    form.append('file', file);
    form.append('template', template || '');
    form.append('kind', kind || 'style');
    if (extra) {
        for (var k in extra) {
            if (extra[k] != null) form.append(k, extra[k]);
        }
    }
    try {
        var base = await window._rmBackendBase();
        var resp = await fetch(base + '/api/report_maker/upload', {
            method: 'POST', body: form, credentials: 'include'
        });
        var result = await resp.json().catch(function() { return {}; });
        if (!resp.ok) {
            return {key: '', filename: file.name,
                    error: (result && result.detail) ? result.detail : ('업로드 실패 (' + resp.status + ')')};
        }
        return result;
    } catch (e) {
        return {key: '', filename: file.name, error: (e && e.message) ? e.message : String(e)};
    }
};

// 파일 선택 input 을 만들어 클릭 → onPick(files[]) 콜백 실행. 취소 시 빈 배열.
window._rmPickFiles = window._rmPickFiles || function(accept, multiple, onPick) {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    if (multiple) input.multiple = true;
    input.style.display = 'none';
    input.onchange = function() {
        var files = input.files ? Array.prototype.slice.call(input.files) : [];
        try { document.body.removeChild(input); } catch (e) {}
        onPick(files);
    };
    input.addEventListener('cancel', function() {
        try { document.body.removeChild(input); } catch (e) {}
        onPick([]);
    });
    document.body.appendChild(input);
    input.click();
};

// 단일 파일(주제 첨부). 첨부는 정식 등록되므로 session_id·msg_id 를 함께 보낸다.
// {file_no, filename, error} 반환(취소 시 {file_no:0, error:''}).
window.reportMakerPickAndUpload = function(template, kind, sessionId, msgId) {
    var accept = (kind === 'topic')
        ? '.pdf,.pptx,.png,.jpg,.jpeg,.webp,.gif'
        : '.pdf,.pptx';
    return new Promise(function(resolve) {
        window._rmPickFiles(accept, false, async function(files) {
            if (!files.length) { resolve({file_no: 0, filename: '', error: ''}); return; }
            resolve(await window._rmUploadOne(files[0], template, kind,
                {session_id: sessionId, msg_id: msgId}));
        });
    });
};

// 다중 파일(참고 문서 학습). 순차 업로드 → [{key, filename, error}, ...] 반환(취소 시 []).
window.reportMakerPickAndUploadMany = function(template, kind) {
    var accept = (kind === 'topic')
        ? '.pdf,.pptx,.png,.jpg,.jpeg,.webp,.gif'
        : '.pdf,.pptx';
    return new Promise(function(resolve) {
        window._rmPickFiles(accept, true, async function(files) {
            var results = [];
            for (var i = 0; i < files.length; i++) {
                results.push(await window._rmUploadOne(files[i], template, kind));
            }
            resolve(results);
        });
    });
};
"""

# 자동 스크롤 — 메인 챗과 동일 UX(스트리밍/새 메시지 시 하단 고정, 사용자가 위로 올리면 중단).
# 대화 컨테이너(#rm-chat-container)는 세션 시작 후에야 마운트되므로, body 변화를 감시해
# 컨테이너가 나타나는 즉시 1회 wire 한다. wire 후에는 컨테이너 자체의 MutationObserver 가
# 스트리밍 중 텍스트 변화(characterData)·새 메시지(childList)에 반응해 하단으로 스크롤한다.
REPORT_MAKER_AUTOSCROLL_SCRIPT = """
(function initRmAutoScroll() {
    var THRESHOLD = 120;  // 하단에서 이 px 이내면 '맨 아래'로 간주
    function wire(el) {
        if (el._rmAutoScroll) return;
        el._rmAutoScroll = true;
        var userUp = false;
        function dist() { return el.scrollHeight - el.scrollTop - el.clientHeight; }
        el.addEventListener('scroll', function() { userUp = dist() > THRESHOLD; });
        new MutationObserver(function() {
            if (!userUp) el.scrollTop = el.scrollHeight;
        }).observe(el, { childList: true, subtree: true, characterData: true });
        el.scrollTop = el.scrollHeight;
    }
    function tryWire() {
        var el = document.getElementById('rm-chat-container');
        if (el) wire(el);
    }
    tryWire();
    new MutationObserver(tryWire).observe(document.body, { childList: true, subtree: true });
})();
"""
