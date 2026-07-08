"""Admin 상태 관리 - AdminState.

관리자 인증(env + DB 이중), 부서/사원/에이전트 CRUD 상태를 담당.
"""

import hmac
import os

import reflex as rx
from dotenv import load_dotenv

from wellbot.services.admin import admin_service

load_dotenv()


class AdminState(rx.State):
    """관리 페이지 상태 관리"""

    # ── 인증 ──
    is_authenticated: bool = False
    auth_emp_no: str = ""
    auth_password: str = ""
    auth_error: str = ""
    admin_label: str = ""  # 로그인한 관리자 표시명

    # ── 탭 ──
    active_tab: str = "dept"

    # ── 데이터 목록 ──
    depts: list[dict] = []
    employees: list[dict] = []
    agents: list[dict] = []

    # ── 모달 ──
    show_modal: bool = False
    modal_mode: str = ""  # "dept" | "employee" | "agent"
    is_editing: bool = False
    form_data: dict = {}
    error_message: str = ""
    success_message: str = ""

    # ── 인증 이벤트 ──

    def set_auth_emp_no(self, value: str) -> None:
        self.auth_emp_no = value
        self.auth_error = ""

    def set_auth_password(self, value: str) -> None:
        self.auth_password = value
        self.auth_error = ""

    def check_admin_auth(self) -> None:
        """.env 비밀번호 또는 DB ADMIN 계정으로 관리자 인증"""
        password = self.auth_password.strip()
        emp_no = self.auth_emp_no.strip()

        if not password:
            self.auth_error = "비밀번호를 입력해주세요."
            return

        if not emp_no:
            env_pw = os.environ.get("ADMIN_PASSWORD", "")
            if hmac.compare_digest(password, env_pw):
                self.is_authenticated = True
                self.admin_label = "SUPER"
                self.auth_error = ""
                self._load_all()
                return
            self.auth_error = "비밀번호가 올바르지 않습니다."
            return

        if admin_service.authenticate_admin(emp_no, password):
            self.is_authenticated = True
            self.admin_label = emp_no
            self.auth_error = ""
            self._load_all()
            return

        self.auth_error = "사원번호 또는 비밀번호가 올바르지 않습니다."

    async def admin_logout(self) -> rx.event.EventSpec:
        """로그아웃. AdminState 초기화 + DB 계정이면 AuthState 세션도 정리"""
        is_super = self.admin_label == "SUPER"

        self.is_authenticated = False
        self.admin_label = ""
        self.auth_emp_no = ""
        self.auth_password = ""
        self.auth_error = ""

        if not is_super:
            from wellbot.state.auth_state import AuthState

            auth = await self.get_state(AuthState)
            if auth.auth_token:
                from wellbot.services.auth import auth_service

                auth_service.invalidate_session_token(auth.auth_token)
            auth.auth_token = ""
            auth.is_authenticated = False
            auth.current_emp_no = ""
            auth.current_user_nm = ""
            auth.current_user_role = ""
            auth.current_dept_cd = ""

        return rx.redirect("/login")  # type: ignore[return-value]

    # ── Computed vars ──

    @rx.var
    def dept_options(self) -> list[str]:
        """부서 select 옵션 목록. '코드 - 부서명' 형식"""
        return [
            f"{d.get('dept_cd', '')} - {d.get('dept_nm', '')}"
            for d in self.depts
        ]

    @rx.var
    def dept_codes(self) -> list[str]:
        """부서 코드 목록"""
        return [d.get("dept_cd", "") for d in self.depts]

    @rx.var
    def dept_display_options(self) -> list[str]:
        """부서 select 표시 목록. '부서명(코드)' 형식"""
        return [
            f"{d.get('dept_nm', '')}({d.get('dept_cd', '')})"
            for d in self.depts
        ]

    def _dept_display_to_code(self, display: str) -> str:
        """'부서명(코드)' → 부서코드 추출"""
        start = display.rfind("(")
        end = display.rfind(")")
        if start != -1 and end != -1 and end > start:
            return display[start + 1:end]
        return display

    # ── 데이터 로드 ──

    def _load_all(self) -> None:
        self.load_depts()
        self.load_employees()
        self.load_agents()

    def load_depts(self) -> None:
        try:
            self.depts = admin_service.list_depts()
        except Exception as e:
            self.error_message = f"부서 로드 실패: {e}"

    def load_employees(self) -> None:
        try:
            self.employees = admin_service.list_employees()
        except Exception as e:
            self.error_message = f"사원 로드 실패: {e}"

    def load_agents(self) -> None:
        try:
            self.agents = admin_service.list_agents()
        except Exception as e:
            self.error_message = f"에이전트 로드 실패: {e}"

    async def on_admin_load(self) -> rx.event.EventSpec | None:
        """페이지 로드 시 인증 확인. AuthState 의 ADMIN 역할이면 자동 인증"""
        if self.is_authenticated and self.admin_label == "SUPER":
            self._load_all()
            return None

        from wellbot.state.auth_state import AuthState
        auth = await self.get_state(AuthState)
        if auth.is_authenticated and auth.current_user_role == "ADMIN":
            self.is_authenticated = True
            self.admin_label = auth.current_emp_no
            self._load_all()
            return None

        self.is_authenticated = False
        self.admin_label = ""
        return None

    def set_active_tab(self, tab: str) -> None:
        self.active_tab = tab
        self.success_message = ""

    # ── 모달 제어 ──

    def open_create_modal(self, mode: str) -> None:
        self.modal_mode = mode
        self.is_editing = False
        self.form_data = {}
        self.error_message = ""
        self.success_message = ""
        self.show_modal = True

    def open_edit_modal(self, mode: str, data: dict) -> None:
        self.modal_mode = mode
        self.is_editing = True
        self.form_data = dict(data)
        self.error_message = ""
        self.success_message = ""
        self.show_modal = True

    def close_modal(self) -> None:
        self.show_modal = False
        self.form_data = {}
        self.error_message = ""

    def set_form_field(self, field: str, value: str) -> None:
        self.form_data = {**self.form_data, field: value}

    def set_form_dept(self, display: str) -> None:
        """부서 드롭다운 선택 시 '부서명(코드)' → 코드 변환 후 form_data 에 저장"""
        code = self._dept_display_to_code(display)
        self.form_data = {**self.form_data, "pstn_dept_cd": code}

    @rx.var
    def form_dept_display(self) -> str:
        """현재 form_data 의 pstn_dept_cd 에 대응하는 '부서명(코드)' 표시값"""
        code = self.form_data.get("pstn_dept_cd", "")
        if not code:
            return ""
        for d in self.depts:
            if d.get("dept_cd", "") == code:
                return f"{d.get('dept_nm', '')}({d.get('dept_cd', '')})"
        return code

    # ── CRUD 이벤트 ──

    def save_dept(self) -> None:
        """부서 생성/수정"""
        try:
            fd = self.form_data
            if not fd.get("dept_cd") or not fd.get("dept_nm"):
                self.error_message = "부서코드와 부서명은 필수입니다."
                return
            dd = int(fd["dd_clby_tokn_ecnt"]) if fd.get("dd_clby_tokn_ecnt") else None
            mm = int(fd["mm_clby_tokn_ecnt"]) if fd.get("mm_clby_tokn_ecnt") else None
            if self.is_editing:
                admin_service.update_dept(
                    fd["dept_cd"], dept_nm=fd.get("dept_nm"),
                    dd_clby_tokn_ecnt=dd, mm_clby_tokn_ecnt=mm,
                )
            else:
                admin_service.create_dept(fd["dept_cd"], fd["dept_nm"], dd, mm)
            self.close_modal()
            self.load_depts()
            self.success_message = "부서가 저장되었습니다."
        except Exception as e:
            self.error_message = str(e)

    def delete_dept(self, dept_cd: str) -> None:
        """부서 삭제"""
        try:
            admin_service.delete_dept(dept_cd)
            self.load_depts()
            self.success_message = "부서가 삭제되었습니다."
        except Exception as e:
            self.error_message = str(e)

    def save_employee(self) -> None:
        """사원 생성/수정"""
        try:
            fd = self.form_data
            if not fd.get("emp_no") or not fd.get("user_nm"):
                self.error_message = "사원번호와 사용자명은 필수입니다."
                return
            if self.is_editing:
                kwargs: dict = {
                    "user_nm": fd.get("user_nm", ""),
                    "user_role_nm": fd.get("user_role_nm", "USER"),
                    "pstn_dept_cd": fd.get("pstn_dept_cd", ""),
                    "acnt_sts_nm": fd.get("acnt_sts_nm", "ACTIVE"),
                }
                if fd.get("password"):
                    kwargs["password"] = fd["password"]
                admin_service.update_employee(fd["emp_no"], **kwargs)
            else:
                if not fd.get("password"):
                    self.error_message = "비밀번호는 필수입니다."
                    return
                admin_service.create_employee(
                    emp_no=fd["emp_no"],
                    password=fd["password"],
                    user_nm=fd["user_nm"],
                    user_role_nm=fd.get("user_role_nm", "USER"),
                    pstn_dept_cd=fd.get("pstn_dept_cd", ""),
                    acnt_sts_nm=fd.get("acnt_sts_nm", "ACTIVE"),
                )
            self.close_modal()
            self.load_employees()
            self.success_message = "사원이 저장되었습니다."
        except Exception as e:
            self.error_message = str(e)

    def delete_employee(self, emp_no: str) -> None:
        """사원 삭제"""
        try:
            admin_service.delete_employee(emp_no)
            self.load_employees()
            self.success_message = "사원이 삭제되었습니다."
        except Exception as e:
            self.error_message = str(e)

    def approve_employee(self, emp_no: str) -> None:
        """가입 대기 사원 승인 (PENDING → ACTIVE)"""
        try:
            admin_service.approve_employee(emp_no)
            self.load_employees()
            self.success_message = f"'{emp_no}' 사원을 승인했습니다."
        except Exception as e:
            self.error_message = str(e)

    def unlock_employee(self, emp_no: str) -> None:
        """로그인 실패/잠금 해제"""
        try:
            admin_service.unlock_employee(emp_no)
            self.load_employees()
            self.success_message = f"'{emp_no}' 계정 잠금을 해제했습니다."
        except Exception as e:
            self.error_message = str(e)

    def save_agent(self) -> None:
        """에이전트 생성/수정"""
        try:
            fd = self.form_data
            if not fd.get("agnt_id") or not fd.get("agnt_nm"):
                self.error_message = "에이전트ID와 에이전트명은 필수입니다."
                return
            seq = int(fd.get("agnt_seq", 1))
            if self.is_editing:
                admin_service.update_agent(
                    fd["agnt_id"], seq,
                    agnt_nm=fd.get("agnt_nm"),
                    agnt_frwk_nm=fd.get("agnt_frwk_nm", ""),
                    agnt_path_addr=fd.get("agnt_path_addr", ""),
                    agnt_dscr_cntt=fd.get("agnt_dscr_cntt", ""),
                    use_yn=fd.get("use_yn", "Y"),
                )
            else:
                admin_service.create_agent(
                    agnt_id=fd["agnt_id"],
                    agnt_seq=seq,
                    agnt_nm=fd["agnt_nm"],
                    agnt_frwk_nm=fd.get("agnt_frwk_nm", ""),
                    agnt_path_addr=fd.get("agnt_path_addr", ""),
                    agnt_dscr_cntt=fd.get("agnt_dscr_cntt", ""),
                    use_yn=fd.get("use_yn", "Y"),
                )
            self.close_modal()
            self.load_agents()
            self.success_message = "에이전트가 저장되었습니다."
        except Exception as e:
            self.error_message = str(e)

    def delete_agent(self, key: str) -> None:
        """에이전트 삭제. key 형식은 'agnt_id|agnt_seq'"""
        try:
            parts = key.split("|")
            admin_service.delete_agent(parts[0], int(parts[1]))
            self.load_agents()
            self.success_message = "에이전트가 삭제되었습니다."
        except Exception as e:
            self.error_message = str(e)
