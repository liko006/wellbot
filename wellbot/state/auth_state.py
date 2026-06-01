"""인증 상태 관리 - AuthState.

rx.Cookie 기반 세션 토큰 + DB 검증으로 로그인 상태를 유지.
"""

import reflex as rx

from wellbot.constants import (
    PASSWORD_MIN_LENGTH,
    REMEMBER_ME_EXPIRE_SECONDS,
    TOKEN_EXPIRE_SECONDS,
)
from wellbot.paths import NOTICE_MD
from wellbot.services.auth import auth_service


class AuthState(rx.State):
    """인증 관련 상태 관리"""

    # ── 공지사항 ──
    notice_html: str = ""

    # ── 폼 입력 ──
    login_emp_no: str = ""
    login_password: str = ""
    login_error: str = ""
    is_logging_in: bool = False

    # ── 아이디 기억하기 ──
    remember_me: bool = False
    remembered_emp_no: str = rx.Cookie(
        name="wellbot_remember",
        max_age=REMEMBER_ME_EXPIRE_SECONDS,
        same_site="lax",
    )

    # ── 세션 (쿠키 연동) ──
    auth_token: str = rx.Cookie(
        name="wellbot_auth",
        max_age=TOKEN_EXPIRE_SECONDS,
        same_site="lax",
    )

    # ── 사용자 정보 ──
    is_authenticated: bool = False
    current_emp_no: str = ""
    current_user_nm: str = ""
    current_user_role: str = ""
    current_dept_cd: str = ""

    # ── 이스터에그: 아이콘 연속 클릭 → 관리자 페이지 ──
    _easter_egg_clicks: int = 0

    def handle_easter_egg_click(self) -> rx.event.EventSpec | None:
        """로그인 페이지 아이콘 클릭 카운터. 5회 연속 클릭 시 /admin 으로 이동"""
        self._easter_egg_clicks += 1
        if self._easter_egg_clicks >= 5:
            self._easter_egg_clicks = 0
            return rx.redirect("/admin")
        return None

    # ── 폼 핸들러 ──

    def set_login_emp_no(self, value: str) -> None:
        self.login_emp_no = value
        self.login_error = ""

    def set_login_password(self, value: str) -> None:
        self.login_password = value
        self.login_error = ""

    def toggle_remember_me(self, checked: bool) -> None:
        """아이디 기억하기 체크박스 토글"""
        self.remember_me = checked

    # ── 로그인 ──

    def handle_login(self, _form_data: dict | None = None) -> rx.event.EventSpec | None:
        """로그인 처리"""
        emp_no = self.login_emp_no.strip()
        password = self.login_password.strip()

        if not emp_no or not password:
            self.login_error = "사원번호와 비밀번호를 입력해주세요."
            return None

        self.is_logging_in = True
        result = auth_service.authenticate_user(emp_no, password)

        if not result["success"]:
            self.login_error = result["error"]
            self.is_logging_in = False
            return None

        token = auth_service.create_session_token(emp_no)
        self.auth_token = token

        if self.remember_me:
            self.remembered_emp_no = emp_no
        else:
            self.remembered_emp_no = ""

        user = result["user"]
        self._set_user_info(user)

        self.login_emp_no = ""
        self.login_password = ""
        self.login_error = ""
        self.is_logging_in = False

        return rx.redirect("/")

    def _set_user_info(self, user: dict) -> None:
        """사용자 정보를 State 에 반영"""
        self.is_authenticated = True
        self.current_emp_no = user.get("emp_no", "")
        self.current_user_nm = user.get("user_nm", "")
        self.current_user_role = user.get("user_role_nm", "")
        self.current_dept_cd = user.get("pstn_dept_cd", "")

    # ── 인증 확인 (on_load) ──

    def check_auth(self) -> rx.event.EventSpec | None:
        """페이지 로드 시 인증 확인. 미인증이면 /login 으로 리다이렉트"""
        if not self.auth_token:
            self.is_authenticated = False
            return rx.redirect("/login")

        user = auth_service.validate_session_token(self.auth_token)
        if not user:
            self.auth_token = ""
            self.is_authenticated = False
            return rx.redirect("/login")

        self._set_user_info(user)
        return None

    def _load_notice(self) -> None:
        """config/notice.md 파일을 읽어 공지사항 로드"""
        if NOTICE_MD.exists():
            self.notice_html = NOTICE_MD.read_text(encoding="utf-8").strip()
        else:
            self.notice_html = ""

    def check_login_page(self) -> rx.event.EventSpec | None:
        """로그인 페이지 로드 시 인증 확인. 이미 인증된 경우 / 로 리다이렉트"""
        self._load_notice()

        if self.remembered_emp_no:
            self.login_emp_no = self.remembered_emp_no
            self.remember_me = True

        if not self.auth_token:
            return None

        user = auth_service.validate_session_token(self.auth_token)
        if user:
            self._set_user_info(user)
            return rx.redirect("/")

        self.auth_token = ""
        return None

    # ── 로그아웃 ──

    def logout(self) -> rx.event.EventSpec:
        """로그아웃. 토큰 폐기 + 쿠키 삭제"""
        if self.auth_token:
            auth_service.invalidate_session_token(self.auth_token)

        self.auth_token = ""
        self.is_authenticated = False
        self.current_emp_no = ""
        self.current_user_nm = ""
        self.current_user_role = ""
        self.current_dept_cd = ""

        return rx.redirect("/login")

    # ── 회원가입 ──

    _reg_dept_options: list[dict] = []

    def load_dept_list(self) -> None:
        """회원가입 페이지 로드 시 부서 목록 조회"""
        self._reg_dept_options = auth_service.list_dept_options()

    @rx.var
    def reg_dept_names(self) -> list[str]:
        """드롭다운에 표시할 부서명 목록"""
        return [d.get("name", "") for d in self._reg_dept_options]

    def _dept_name_to_code(self, name: str) -> str:
        """부서명 → 부서코드 변환"""
        for d in self._reg_dept_options:
            if d.get("name") == name:
                return d.get("code", "")
        return ""

    reg_emp_no: str = ""
    reg_password: str = ""
    reg_password_confirm: str = ""
    reg_user_nm: str = ""
    reg_dept_cd: str = ""
    reg_error: str = ""
    reg_success: bool = False
    is_registering: bool = False

    def set_reg_emp_no(self, value: str) -> None:
        self.reg_emp_no = value
        self.reg_error = ""

    def set_reg_password(self, value: str) -> None:
        self.reg_password = value
        self.reg_error = ""

    def set_reg_password_confirm(self, value: str) -> None:
        self.reg_password_confirm = value
        self.reg_error = ""

    def set_reg_user_nm(self, value: str) -> None:
        self.reg_user_nm = value
        self.reg_error = ""

    reg_dept_display: str = ""  # 드롭다운에 표시되는 부서명

    def set_reg_dept(self, dept_name: str) -> None:
        """부서 선택 시 부서명 → 부서코드 변환"""
        self.reg_dept_display = dept_name
        self.reg_dept_cd = self._dept_name_to_code(dept_name)
        self.reg_error = ""

    def handle_register(self, _form_data: dict | None = None) -> None:
        """회원가입 처리"""
        emp_no = self.reg_emp_no.strip()
        password = self.reg_password
        confirm = self.reg_password_confirm
        user_nm = self.reg_user_nm.strip()
        dept_cd = self.reg_dept_cd.strip()

        if not emp_no or not password or not user_nm or not dept_cd:
            self.reg_error = "사원번호, 비밀번호, 이름, 부서코드는 필수입니다."
            return

        if password != confirm:
            self.reg_error = "비밀번호가 일치하지 않습니다."
            return

        if len(password) < PASSWORD_MIN_LENGTH:
            self.reg_error = f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
            return

        self.is_registering = True
        result = auth_service.register_user(emp_no, password, user_nm, dept_cd)

        if not result["success"]:
            self.reg_error = result["error"]
            self.is_registering = False
            return

        self.reg_success = True
        self.is_registering = False

    # ── 비밀번호 변경 ──

    show_change_password: bool = False
    chpw_current: str = ""
    chpw_new: str = ""
    chpw_confirm: str = ""
    chpw_error: str = ""
    chpw_success: bool = False
    is_changing_password: bool = False

    def open_change_password(self) -> None:
        """비밀번호 변경 다이얼로그 열기"""
        self.show_change_password = True
        self.chpw_current = ""
        self.chpw_new = ""
        self.chpw_confirm = ""
        self.chpw_error = ""
        self.chpw_success = False

    def close_change_password(self) -> None:
        """비밀번호 변경 다이얼로그 닫기"""
        self.show_change_password = False
        self.chpw_current = ""
        self.chpw_new = ""
        self.chpw_confirm = ""
        self.chpw_error = ""
        self.chpw_success = False

    def set_chpw_current(self, value: str) -> None:
        self.chpw_current = value
        self.chpw_error = ""

    def set_chpw_new(self, value: str) -> None:
        self.chpw_new = value
        self.chpw_error = ""

    def set_chpw_confirm(self, value: str) -> None:
        self.chpw_confirm = value
        self.chpw_error = ""

    def handle_change_password(self, _form_data: dict | None = None) -> None:
        """비밀번호 변경 처리"""
        current = self.chpw_current
        new_pw = self.chpw_new
        confirm = self.chpw_confirm

        if not current or not new_pw or not confirm:
            self.chpw_error = "모든 필드를 입력해주세요."
            return

        if new_pw != confirm:
            self.chpw_error = "새 비밀번호가 일치하지 않습니다."
            return

        if len(new_pw) < PASSWORD_MIN_LENGTH:
            self.chpw_error = f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
            return

        if current == new_pw:
            self.chpw_error = "현재 비밀번호와 다른 비밀번호를 입력해주세요."
            return

        self.is_changing_password = True
        result = auth_service.change_password(self.current_emp_no, current, new_pw)

        if not result["success"]:
            self.chpw_error = result["error"]
            self.is_changing_password = False
            return

        self.chpw_success = True
        self.is_changing_password = False
