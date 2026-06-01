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
