/**
 * PoliCity — password_find.js
 * 비밀번호 찾기 이메일 인증 + 다음 버튼 → 비밀번호 재설정 페이지 이동
 */
(function () {
  "use strict";

  const emailInput      = document.getElementById("email");
  const sendBtn         = document.getElementById("sendFindCodeBtn");
  const codeInput       = document.getElementById("findCode");
  const checkBtn        = document.getElementById("checkFindCodeBtn");
  const nextBtn         = document.getElementById("findNextBtn");
  const messageEl       = document.getElementById("findMessage");

  let cooldownTimer = null;
  let cooldownLeft  = 0;

  /* ── CSRF ── */
  function getCsrf() {
    const el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  /* ── 메시지 ── */
  function showMsg(msg, type) {
    messageEl.textContent = msg;
    messageEl.className   = "find-message " + (type || "");
  }

  /* ── 이메일 입력 → 발송 버튼 활성화 ── */
  function isValidEmail(v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
  }

  function resetVerification() {
    codeInput.disabled = true;
    codeInput.value    = "";
    codeInput.classList.remove("has-value");
    checkBtn.disabled  = true;
    checkBtn.classList.remove("is-active");
    nextBtn.disabled   = true;
    nextBtn.classList.remove("is-active");
    showMsg("", "");
    stopCodeTimer();
  }

  emailInput.addEventListener("input", function () {
    const val = this.value.trim();
    this.classList.toggle("has-value", val.length > 0);
    if (cooldownLeft <= 0) sendBtn.disabled = !isValidEmail(val); // 쿨다운 중엔 버튼 상태 건드리지 않음 (깜빡임 방지)
    resetVerification();
  });

  /* ── 인증코드 발송 ── */
  sendBtn.addEventListener("click", async function () {
    if (this.disabled) return;

    const email = emailInput.value.trim();
    this.disabled = true;

    try {
      const res  = await fetch("/member/password/send-code/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();

      if (res.ok) {
        showMsg("인증번호가 이메일로 전송되었습니다.", "success");
        codeInput.disabled = false;
        codeInput.value    = "";
        codeInput.focus();
        startCooldown();
        startCodeTimer();
      } else {
        showMsg(data.error || "인증번호 발송에 실패했습니다.", "error");
        this.disabled = false;
        this.textContent = "인증번호 전송";
      }
    } catch {
      showMsg("네트워크 오류가 발생했습니다.", "error");
      this.disabled = false;
      this.textContent = "인증번호 전송";
    }
  });

  /* ── 30초 쿨다운 ── */
  function startCooldown() {
    cooldownLeft = 30;
    sendBtn.textContent = `재전송 (${cooldownLeft}s)`;
    sendBtn.disabled    = true;

    clearInterval(cooldownTimer);
    cooldownTimer = setInterval(function () {
      cooldownLeft--;
      if (cooldownLeft <= 0) {
        clearInterval(cooldownTimer);
        sendBtn.disabled    = false;
        sendBtn.textContent = "재전송";
      } else {
        sendBtn.textContent = `재전송 (${cooldownLeft}s)`;
      }
    }, 1000);
  }

/* ── 3분 유효시간 타이머 (인증번호 입력칸의 placeholder로 표시) ── */
  let codeTimerInterval = null;
  let codeTimeLeft = 0;
  const ORIGINAL_CODE_PLACEHOLDER = "인증번호를 입력해주세요";

  function fmtCodeTime(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  function refreshCodeTimerVisibility() {
    if (!codeTimerInterval) return;
    const clamped = Math.max(codeTimeLeft, 0);
    codeInput.placeholder = "남은시간 " + fmtCodeTime(clamped);
  }

  function startCodeTimer() {
    codeTimeLeft = 180;
    clearInterval(codeTimerInterval);

    codeTimerInterval = setInterval(function () {
      codeTimeLeft--;
      if (codeTimeLeft <= 0) {
        clearInterval(codeTimerInterval);
      }
      refreshCodeTimerVisibility();
    }, 1000);

    refreshCodeTimerVisibility();
  }

  function stopCodeTimer() {
    clearInterval(codeTimerInterval);
    codeTimerInterval = null;
    codeInput.placeholder = ORIGINAL_CODE_PLACEHOLDER;
  }

  /* ── 인증코드 입력 → 확인 버튼 활성화 ── */
  codeInput.addEventListener("input", function () {
    const hasVal = this.value.trim().length > 0;
    this.classList.toggle("has-value", hasVal);
    checkBtn.disabled = !hasVal;
    checkBtn.classList.toggle("is-active", hasVal);

    if (!hasVal) {
      nextBtn.disabled = true;
      nextBtn.classList.remove("is-active");
    }
  });

  /* ── 인증코드 확인 ── */
  checkBtn.addEventListener("click", async function () {
    if (this.disabled) return;

    const email = emailInput.value.trim();
    const code  = codeInput.value.trim();

    try {
      const res = await fetch("/member/password/verify-code/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ email, code }),
      });
      const data = await res.json();

      if (res.ok) {
        showMsg("이메일 인증이 완료되었습니다.", "success");
        // 인증 완료된 이메일 저장 (비밀번호 재설정 페이지에서 사용)
        sessionStorage.setItem("reset_email", email);
        stopCodeTimer();
        clearInterval(cooldownTimer);   // 추가
        cooldownLeft = 0;               // 추가
        checkBtn.disabled = true;
        checkBtn.classList.remove("is-active");
        codeInput.disabled = true;
        emailInput.disabled = true;     // 추가: 이메일 변경 방지
        emailInput.style.color = "#aabfb2";  // 추가: 이메일 색상
        sendBtn.disabled = true;        // 추가: 재발송 방지
        sendBtn.textContent = "인증완료";    // 추가: 버튼 텍스트
        nextBtn.disabled   = false;
        nextBtn.classList.add("is-active");
      } else {
        showMsg(data.error || "인증번호가 올바르지 않습니다.", "error");
        // 입력값을 비워서 placeholder(남은시간 타이머)가 다시 보이게 함
        codeInput.value = "";
        codeInput.classList.remove("has-value");
        checkBtn.disabled = true;
        checkBtn.classList.remove("is-active");
        refreshCodeTimerVisibility(); // ← placeholder 타이머 다시 표시
      }
    } catch {
      showMsg("네트워크 오류가 발생했습니다.", "error");
    }
  });

  /* ── 다음 버튼 → 비밀번호 재설정 페이지 이동 ── */
  nextBtn.addEventListener("click", function (e) {
    e.preventDefault();
    if (this.disabled) return;
    window.location.href = "/password/reset/";
  });

})();
