"""파일 업로드 트리거용 JS 스크립트 빌더.

ChatState.trigger_upload 가 rx.call_script 로 실행할 JS 본문 생성.
브라우저 측에서:
    1. <input type=file multiple> 으로 파일 선택
    2. fetch POST /api/upload 로 전송 (백엔드 직접/프록시 자동 판별)
    3. 완료 알림은 Python 측 polling 으로 DB 에서 감지
"""

from __future__ import annotations


def build_upload_script(
    *,
    accept: str,
    conv_id: str,
    msg_id: str,
    max_mb: int,
    max_per_msg: int,
    current_count: int,
) -> str:
    """업로드 다이얼로그 + fetch POST 를 실행하는 JS 본문 반환"""
    return f"""
(async function() {{
  try {{
    // 백엔드 URL 결정
    // 기본: 상대 경로 (Nginx/ALB 리버스 프록시 환경)
    // 로컬 개발 (localhost 포트 분리) 환경에서만 origin 을 붙인다
    let backendBase = '';
    try {{
      const envResp = await fetch('/env.json');
      const env = await envResp.json();
      const pingUrl = env.PING || '';
      if (pingUrl) {{
        const u = new URL(pingUrl);
        const loc = window.location;
        // 둘 다 localhost 이고 포트만 다른 경우 = 로컬 개발 환경
        const isLocalDev = (
          (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') &&
          (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
          u.port !== loc.port
        );
        if (isLocalDev) {{
          // 쿠키가 전달되도록 hostname 을 현재 페이지와 동일하게 맞춘다
          backendBase = loc.protocol + '//' + loc.hostname + ':' + u.port;
        }}
        // 그 외 (ALB/Nginx 프록시 등): 상대 경로 사용 (backendBase = '')
      }}
    }} catch(e) {{}}

    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '{accept}';
    input.style.display = 'none';
    document.body.appendChild(input);

    const files = await new Promise((resolve) => {{
      input.onchange = () => resolve(Array.from(input.files || []));
      input.addEventListener('cancel', () => resolve([]));
      input.click();
    }});
    document.body.removeChild(input);
    if (!files.length) return;

    const maxPerMsg = {max_per_msg};
    const current = {current_count};
    if (files.length + current > maxPerMsg) {{
      alert(`메시지당 최대 ${{maxPerMsg}}개까지 첨부 가능합니다.`);
      return;
    }}

    const maxBytes = {max_mb} * 1024 * 1024;
    const errors = [];
    for (const file of files) {{
      if (file.size > maxBytes) {{
        errors.push(`'${{file.name}}' 파일이 {max_mb}MB 를 초과합니다.`);
        continue;
      }}
      const form = new FormData();
      form.append('file', file);
      form.append('conversation_id', '{conv_id}');
      form.append('message_id', '{msg_id}');
      try {{
        const resp = await fetch(backendBase + '/api/upload', {{
          method: 'POST',
          body: form,
          credentials: 'include',
        }});
        if (!resp.ok) {{
          const data = await resp.json().catch(() => ({{}}));
          errors.push(`'${{file.name}}': ${{data.detail || resp.status}}`);
        }}
      }} catch (err) {{
        errors.push(`'${{file.name}}': ${{err && err.message ? err.message : err}}`);
      }}
    }}
    if (errors.length) alert(errors.join('\\n'));
  }} catch (err) {{
    console.error('[wellbot upload] ', err);
  }}
}})();
"""


# ── KB 파일 업로드 JS (페이지 레벨 window-global 정의) ──────────────
# openKbFilePicker: 브라우저 파일 선택 다이얼로그 → _kbPendingMeta 저장
# uploadKbFilesToApi: _kbSelectedFiles 를 /api/upload_kb_files 로 fetch 전송
#
# build_upload_script 와 달리 파라미터 없는 정적 스크립트로, 컴포넌트 mount/unmount
# 타이밍에 따른 ReferenceError 를 피하려고 pages/index.py 에서 rx.script 로
# 페이지 레벨에 1회만 등록 (window 전역 함수로 항상 사용 가능).
KB_UPLOAD_SCRIPT = """
window._kbFileInput = null;
window._kbSelectedFiles = [];
window._kbPendingMeta = [];
window._kbPickerCanceled = false;

// 백엔드 base URL 결정 (build_upload_script 와 동일 규칙).
// 기본: 상대 경로(Nginx/ALB 프록시). 로컬 포트 분리 개발환경에서만 origin 부착.
window._kbBackendBase = async function() {
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
            if (isLocalDev) {
                return loc.protocol + '//' + loc.hostname + ':' + u.port;
            }
        }
    } catch (e) {}
    return '';
};

// 패널에서 파일을 제거하면 누적 선택 배열에서도 빼야 같은 파일을 다시 고를 수 있다
// (안 빼면 change 핸들러의 이름 dedup 에 걸려 재선택분이 유실되고 picker 가 멈춘 듯 보임).
window.removeKbSelectedFile = function(name) {
    window._kbSelectedFiles = (window._kbSelectedFiles || []).filter(function(f) {
        return f.name !== name;
    });
};

window.clearKbSelectedFiles = function() {
    window._kbSelectedFiles = [];
    window._kbPendingMeta = [];
};

window.openKbFilePicker = function() {
    if (!window._kbFileInput) {
        var input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = '.pdf,.docx,.pptx,.xlsx,.csv,.md,.txt,.json,.html,.htm';
        input.style.display = 'none';
        input.addEventListener('change', function() {
            window._kbPickerCanceled = false;
            if (input.files.length > 0) {
                // 기존 선택분에 누적 (이름 기준 중복 제거) — 두 번 이상 선택해도 유지
                var existing = window._kbSelectedFiles || [];
                var existingNames = existing.map(function(f) { return f.name; });
                var added = Array.from(input.files).filter(function(f) {
                    return existingNames.indexOf(f.name) === -1;
                });
                window._kbSelectedFiles = existing.concat(added);
                // 이번에 새로 추가된 파일들의 메타만 콜백으로 (패널에 추가)
                window._kbPendingMeta = added.map(function(f) {
                    return { name: f.name, size: f.size };
                });
            }
            input.value = '';
        });
        // 'cancel' 이벤트로 다이얼로그 취소 감지 (Chrome 113+, Firefox 91+, Safari 16.4+)
        input.addEventListener('cancel', function() {
            window._kbPickerCanceled = true;
        });
        document.body.appendChild(input);
        window._kbFileInput = input;
    }
    window._kbPickerCanceled = false;  // 호출 시점에 플래그 리셋
    window._kbFileInput.click();
};

window.uploadKbFilesToApi = async function(empNo, uploadTarget, deptCd, allowedNames) {
    var files = window._kbSelectedFiles || [];
    // 패널에 남아있는 파일명만 업로드 (패널에서 제거한 파일은 제외).
    // 파일명은 NFC 로 정규화해 비교 — 일부 OS(예: macOS 의 NFD)에서 같은 이름이
    // 다른 코드포인트로 와 매칭 실패("No files selected")하는 것을 방지(방어적).
    if (allowedNames && allowedNames.length >= 0) {
        var _norm = function(s) { return (s && s.normalize) ? s.normalize('NFC') : s; };
        var allowed = (allowedNames || []).map(_norm);
        files = files.filter(function(f) { return allowed.indexOf(_norm(f.name)) !== -1; });
    }
    if (files.length === 0) {
        // 패널엔 파일이 있는데 실제 선택(_kbSelectedFiles)이 비/어긋난 디싱크 상태.
        // stale 선택을 비워 같은 파일을 다시 골라도 dedup 에 걸리지 않게 한다(깨끗한 재선택).
        window._kbSelectedFiles = [];
        window._kbPendingMeta = [];
        return {uploaded: [], error: 'No files selected'};
    }

    var formData = new FormData();
    for (var i = 0; i < files.length; i++) formData.append('files', files[i]);
    formData.append('emp_no', empNo);
    formData.append('upload_target', uploadTarget);
    if (deptCd) formData.append('dept_cd', deptCd);

    try {
        // 로컬 포트 분리 개발환경에서는 백엔드(:8000)로 직접 보내야 라우트가 존재
        // (상대 경로면 프론트 :3000 으로 가서 404). 프록시 환경에선 상대 경로.
        var backendBase = await window._kbBackendBase();
        var resp = await fetch(backendBase + '/api/upload_kb_files', {
            method: 'POST',
            body: formData,
            credentials: 'include',
        });
        var result = await resp.json().catch(function() { return {}; });
        window._kbSelectedFiles = [];
        if (!resp.ok) {
            return {uploaded: [], error: (result && result.detail) ? result.detail : ('업로드 실패 (' + resp.status + ')')};
        }
        return result;
    } catch (e) {
        return {uploaded: [], error: e.message};
    }
};
"""


# ── 클립보드 이미지 붙여넣기 업로드 JS (페이지 레벨 window-global 정의) ──
# 채팅 입력(textarea)에서 캡쳐 이미지를 Ctrl+V 로 붙여넣으면 첨부로 업로드.
#
# 동작:
#   1. document paste 리스너가 textarea 대상 이미지 File 을 window._pastedFiles 에 적재
#      (클립보드 이미지는 이름이 없을 수 있어 'pasted-*.png' 식 파일명을 부여)
#   2. 숨김 버튼(#wellbot-paste-trigger) 클릭 → ChatState.handle_paste_upload 가
#      대화 영속화 + conv_id/msg_id 발급 후 wellbotUploadPasted 를 call_script 로 호출
#   3. wellbotUploadPasted 가 window._pastedFiles 를 /api/upload 로 전송
#      (build_upload_script 와 동일한 backendBase/한도/에러 규칙)
PASTE_UPLOAD_SCRIPT = """
window._pastedFiles = window._pastedFiles || [];

// 백엔드 base URL 결정 (build_upload_script / _kbBackendBase 와 동일 규칙).
window._wellbotBackendBase = async function() {
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
            if (isLocalDev) {
                return loc.protocol + '//' + loc.hostname + ':' + u.port;
            }
        }
    } catch (e) {}
    return '';
};

// 붙여넣은 이미지들(window._pastedFiles)을 /api/upload 로 전송.
// 백엔드(ChatState.handle_paste_upload)가 conv_id/msg_id 발급 후 호출한다.
window.wellbotUploadPasted = async function(convId, msgId, maxMb, maxPerMsg, currentCount) {
    var files = window._pastedFiles || [];
    window._pastedFiles = [];
    if (!files.length) return;
    if (files.length + currentCount > maxPerMsg) {
        alert('메시지당 최대 ' + maxPerMsg + '개까지 첨부 가능합니다.');
        return;
    }
    var maxBytes = maxMb * 1024 * 1024;
    var backendBase = await window._wellbotBackendBase();
    var errors = [];
    for (var i = 0; i < files.length; i++) {
        var file = files[i];
        if (file.size > maxBytes) {
            errors.push("'" + file.name + "' 파일이 " + maxMb + 'MB 를 초과합니다.');
            continue;
        }
        var form = new FormData();
        form.append('file', file);
        form.append('conversation_id', convId);
        form.append('message_id', msgId);
        try {
            var resp = await fetch(backendBase + '/api/upload', {
                method: 'POST',
                body: form,
                credentials: 'include',
            });
            if (!resp.ok) {
                var data = await resp.json().catch(function() { return {}; });
                errors.push("'" + file.name + "': " + (data.detail || resp.status));
            }
        } catch (err) {
            errors.push("'" + file.name + "': " + (err && err.message ? err.message : err));
        }
    }
    if (errors.length) alert(errors.join('\\n'));
};

// SPA 라우팅으로 스크립트가 재실행돼도 리스너 중복 등록 방지
if (!window._wellbotPasteBound) {
    window._wellbotPasteBound = true;
    document.addEventListener('paste', function(e) {
        var target = e.target;
        // 채팅 입력(textarea)에 포커스가 있을 때만 가로챈다
        if (!target || target.tagName !== 'TEXTAREA') return;
        var items = (e.clipboardData && e.clipboardData.items) || [];
        var files = [];
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            if (it.kind === 'file' && it.type && it.type.indexOf('image/') === 0) {
                var f = it.getAsFile();
                if (!f) continue;
                // 클립보드 이미지는 이름이 없거나 모두 'image.png' 로 와서 서로 구분이 안 된다.
                // 의미있는 원본 이름이 아니면 시각 + 전역 시퀀스로 고유 이름을 부여한다
                // (서버는 확장자로 검증하므로 확장자도 보장).
                var nm = f.name || '';
                var dot = nm.lastIndexOf('.');
                var stem = dot > 0 ? nm.slice(0, dot) : nm;
                if (dot <= 0 || !stem || stem.toLowerCase() === 'image') {
                    var ext = (it.type.split('/')[1] || 'png').split('+')[0];
                    var d = new Date();
                    var pad = function(n) { return ('0' + n).slice(-2); };
                    var ts = '' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate())
                        + '_' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
                    window._pasteSeq = (window._pasteSeq || 0) + 1;
                    f = new File([f], 'pasted_' + ts + '_' + window._pasteSeq + '.' + ext, {type: it.type});
                }
                files.push(f);
            }
        }
        if (!files.length) return;
        e.preventDefault();
        window._pastedFiles = files;
        var trigger = document.getElementById('wellbot-paste-trigger');
        if (trigger) trigger.click();
    });
}
"""


# ── 클라이언트 오류 비콘 ────────────────────────────────────────────
# 브라우저에서만 발생해 서버 로그에 안 잡히는 실패(JS 예외/unhandled rejection 등 —
# 웹/Reflex/websocket 계층)를 /api/client_log 로 전송한다. 백엔드는 기존 미들웨어로
# request_id 를 부여해 구조화 로그(dev=콘솔/prod=wellbot.log)에 남긴다.
# - 백엔드 base 는 기존 _wellbotBackendBase 재사용(로컬 포트 분리 환경 대응).
# - 실패만 전송(성공 비콘 없음). 동일 메시지는 5초 throttle, 비콘 자체 오류는 무시(재귀 방지).
CLIENT_LOG_SCRIPT = """
(function() {
    if (window.__wellbotClientLogInstalled) return;  // 1회만 설치
    window.__wellbotClientLogInstalled = true;

    var lastSent = {};
    function send(kind, message, detail) {
        try {
            if (!message) return;
            var key = kind + '|' + message;
            var now = Date.now();
            if (lastSent[key] && (now - lastSent[key]) < 5000) return;  // 동일건 throttle
            lastSent[key] = now;
            var payload = {
                kind: kind,
                message: String(message).slice(0, 500),
                detail: detail ? String(detail).slice(0, 2000) : '',
                url: (window.location ? window.location.href : '')
            };
            var doSend = function(base) {
                try {
                    fetch((base || '') + '/api/client_log', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload),
                        credentials: 'include',
                        keepalive: true
                    }).catch(function() {});  // 전송 실패는 무시(재귀 방지)
                } catch (e) {}
            };
            if (typeof window._wellbotBackendBase === 'function') {
                window._wellbotBackendBase().then(doSend).catch(function() { doSend(''); });
            } else {
                doSend('');
            }
        } catch (e) { /* 비콘 자체 오류는 삼킨다 */ }
    }

    window.addEventListener('error', function(e) {
        var msg = (e && e.message) ? e.message : 'script error';
        var detail = '';
        if (e && e.error && e.error.stack) detail = e.error.stack;
        else if (e && e.filename) detail = e.filename + ':' + (e.lineno || '') + ':' + (e.colno || '');
        send('error', msg, detail);
    });
    window.addEventListener('unhandledrejection', function(e) {
        var r = e ? e.reason : null;
        var msg = (r && r.message) ? r.message : (typeof r === 'string' ? r : 'unhandled rejection');
        var detail = (r && r.stack) ? r.stack : '';
        send('unhandledrejection', msg, detail);
    });
})();
"""

