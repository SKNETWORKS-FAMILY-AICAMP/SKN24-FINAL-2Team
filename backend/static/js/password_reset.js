/**
 * PoliCity — password_reset.js
 * 새 비밀번호 제출 → 완료 시 로그인 페이지 이동
 */
(function () {
  "use strict";

  const newPwInput   = document.getElementById("newPassword");
  const confirmInput = document.getElementById("confirmPassword");
  const submitBtn    = document.getElementById("resetSubmitBtn");
  const messageEl    = document.getElementById("resetMessage");

  const PW_PATTERN = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$/;

  /* ── CSRF ── */
  function getCsrf() {
    const el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  /* ── 메시지 ── */
  function showMsg(msg, type) {
    messageEl.textContent = msg;
    messageEl.className   = "reset-message " + (type || "");
  }

  /* ── 유효성 검사 ── */
  function validate() {
    const pw      = newPwInput.value;
    const confirm = confirmInput.value;

    newPwInput.classList.toggle("has-value",   pw.length > 0);
    confirmInput.classList.toggle("has-value", confirm.length > 0);

    submitBtn.disabled = true;
    submitBtn.classList.remove("is-active");

    if (!pw && !confirm) { showMsg("", ""); return; }

    if (pw && !PW_PATTERN.test(pw)) {
      showMsg("비밀번호는 영문, 숫자, 특수문자를 포함한 8~16자여야 합니다.", "error");
      return;
    }

    if (PW_PATTERN.test(pw) && !confirm) {
      showMsg("사용 가능한 비밀번호입니다.", "success");
      return;
    }

    if (confirm && pw !== confirm) {
      showMsg("비밀번호가 일치하지 않습니다.", "error");
      return;
    }

    if (PW_PATTERN.test(pw) && pw === confirm) {
      showMsg("비밀번호가 일치합니다.", "success");
      submitBtn.disabled = false;
      submitBtn.classList.add("is-active");
    }
  }

  newPwInput.addEventListener("input", validate);
  confirmInput.addEventListener("input", validate);

  /* ── 폼 제출 ── */
  document.querySelector(".password-reset-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (submitBtn.disabled) return;

    const email = sessionStorage.getItem("reset_email");
    if (!email) {
      showMsg("인증 정보가 없습니다. 비밀번호 찾기를 다시 진행해 주세요.", "error");
      setTimeout(() => { window.location.href = "/password/find/"; }, 2000);
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "처리 중...";

    try {
      const res  = await fetch("/member/password/reset/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({
          email,
          new_password:     newPwInput.value,
          confirm_password: confirmInput.value,
        }),
      });
      const data = await res.json();

      if (res.ok) {
        // 완료 메시지 표시 후 로그인 페이지로 이동
        showMsg("비밀번호가 재설정되었습니다.", "success");
        sessionStorage.removeItem("reset_email");
        setTimeout(() => { window.location.href = "/login/"; }, 1500);
      } else {
        showMsg(data.error || "비밀번호 재설정에 실패했습니다.", "error");
        submitBtn.disabled = false;
        submitBtn.classList.add("is-active");
        submitBtn.textContent = "확인";
      }
    } catch {
      showMsg("네트워크 오류가 발생했습니다.", "error");
      submitBtn.disabled = false;
      submitBtn.classList.add("is-active");
      submitBtn.textContent = "확인";
    }
  });

})();
