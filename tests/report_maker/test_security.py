"""report_maker 보안 회귀 테스트 — 읽기 경로 IDOR 방지 + 업로드 매직바이트 검증."""

import pytest

from wellbot.services.report_maker import storage
from wellbot.services.report_maker.parsing import magic_bytes_ok


class TestOwnsKey:
    """storage.owns_key — 클라이언트가 되돌려준 S3 key 의 소유권 재검증."""

    def test_own_key_accepted(self):
        key = f"{storage.template_prefix('1001', '주간보고')}input/x.pdf"
        assert storage.owns_key(key, "1001", "주간보고") is True

    def test_other_employee_key_rejected(self):
        victim_key = f"{storage.template_prefix('2002', '주간보고')}input/style_docs/secret.pptx"
        # 공격자(1001)가 피해자(2002)의 key 로 소비 시도
        assert storage.owns_key(victim_key, "1001", "주간보고") is False

    def test_other_template_key_rejected(self):
        key = f"{storage.template_prefix('1001', '월간보고')}input/x.pdf"
        assert storage.owns_key(key, "1001", "주간보고") is False

    @pytest.mark.parametrize("emp_no,template", [("", "t"), ("1001", ""), ("", "")])
    def test_empty_identity_rejected(self, emp_no, template):
        key = f"{storage.template_prefix('1001', 't')}input/x.pdf"
        assert storage.owns_key(key, emp_no, template) is False

    def test_empty_key_rejected(self):
        assert storage.owns_key("", "1001", "주간보고") is False


class TestMagicBytes:
    """API 업로드 매직바이트 검증 — 확장자 위조 차단."""

    def test_valid_signatures(self):
        assert magic_bytes_ok(".pdf", b"%PDF-1.7\n...")
        assert magic_bytes_ok(".pptx", b"PK\x03\x04rest")
        assert magic_bytes_ok(".png", b"\x89PNG\r\n\x1a\nrest")
        assert magic_bytes_ok(".jpg", b"\xff\xd8\xff\xe0rest")
        assert magic_bytes_ok(".gif", b"GIF89arest")
        assert magic_bytes_ok(".webp", b"RIFF\x00\x00\x00\x00WEBPrest")

    def test_extension_spoofing_rejected(self):
        # 실행파일을 .pdf 로 위장
        assert magic_bytes_ok(".pdf", b"MZ\x90\x00executable") is False
        # RIFF 지만 WEBP 아님(예: WAV)
        assert magic_bytes_ok(".webp", b"RIFF\x00\x00\x00\x00WAVEfmt") is False
        # 너무 짧은 webp
        assert magic_bytes_ok(".webp", b"RIFF") is False

    def test_undefined_extension_passes(self):
        # 시그니처 미정의 확장자는 확장자 검증에만 의존(통과)
        assert magic_bytes_ok(".txt", b"anything")
