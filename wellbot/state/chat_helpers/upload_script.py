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
    // 패널에 남아있는 파일명만 업로드 (패널에서 제거한 파일은 제외)
    if (allowedNames && allowedNames.length >= 0) {
        files = files.filter(function(f) { return allowedNames.indexOf(f.name) !== -1; });
    }
    if (files.length === 0) return {uploaded: [], error: 'No files selected'};

    var formData = new FormData();
    for (var i = 0; i < files.length; i++) formData.append('files', files[i]);
    formData.append('emp_no', empNo);
    formData.append('upload_target', uploadTarget);
    if (deptCd) formData.append('dept_cd', deptCd);

    try {
        var resp = await fetch('/api/upload_kb_files', {
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
