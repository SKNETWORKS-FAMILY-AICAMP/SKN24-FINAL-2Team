/**
 * PoliCity — signup.js
 * 요구사항명세서 + 화면설계서(SCR-SIGN-01) 기준 회원가입 기능 전체 구현
 */

(function () {
  "use strict";

  /* ══════════════════════════════════════════════
     DOM 참조
  ══════════════════════════════════════════════ */
  const form          = document.querySelector(".signup-form");

  const nicknameInput = document.getElementById("nickname");
  const emailInput    = document.getElementById("email");
  const emailCodeInput= document.getElementById("emailCode");
  const passwordInput = document.getElementById("password");
  const pwConfirmInput= document.getElementById("passwordConfirm");
  const birthYear     = document.getElementById("birthYear");
  const birthMonth    = document.getElementById("birthMonth");
  const birthDay      = document.getElementById("birthDay");

  const sendBtn       = document.querySelector(".signup-send-btn");
  const checkBtn      = document.querySelector(".signup-check-btn");
  const submitBtn     = document.querySelector(".signup-submit");

  /* ══════════════════════════════════════════════
     상태
  ══════════════════════════════════════════════ */
  let emailVerified   = false;   // 이메일 인증 완료 여부
  let cooldownTimer   = null;    // 재발송 30초 쿨다운 interval
  let cooldownLeft    = 0;
  let codeTimerInterval = null;  // 인증코드 3분 유효시간 interval

  /* ══════════════════════════════════════════════
     에러 메시지 헬퍼
  ══════════════════════════════════════════════ */
  function showError(input, msg) {
      removeError(input);
      const el = document.createElement("p");
      el.className = "signup-error-msg";
      el.style.cssText = "color:#e53935; font-size:12px; font-weight:500;";
      el.textContent = msg;

      const field = input.closest(".signup-field");
      if (field) {
        field.appendChild(el);
      } else {
        input.closest(".signup-password-wrap")
          ? input.closest(".signup-password-wrap").after(el)
          : input.after(el);
      }

      input.style.borderColor = "#e53935";
    }

  function removeError(input) {
    const field = input.closest(".signup-field");
    if (field) {
      field.querySelectorAll(".signup-error-msg").forEach(e => e.remove());
    } else {
      const wrap = input.closest(".signup-password-wrap");
      if (wrap) {
        const sib = wrap.nextElementSibling;
        if (sib && sib.classList.contains("signup-error-msg")) sib.remove();
      }
    }
    input.style.borderColor = "";
  }

  function showBlockError(selector, msg) {
    removeBlockError(selector);
    const target = document.querySelector(selector);
    if (!target) return;
    const el = document.createElement("p");
    el.className = "signup-block-error";
    el.style.cssText = "margin:4px 0 0 0; color:#e53935; font-size:12px; font-weight:500;";
    el.textContent = msg;
    target.after(el);
  }

  function removeBlockError(selector) {
    const target = document.querySelector(selector);
    if (!target) return;
    const sib = target.nextElementSibling;
    if (sib && sib.classList.contains("signup-block-error")) sib.remove();
  }

  function showSuccess(input, msg) {
      removeError(input);
      const field = input.closest(".signup-field");
      if (!field) return;
      const el = document.createElement("p");
      el.className = "signup-error-msg";
      el.style.cssText = "color:#2e7d32; font-size:12px; font-weight:500;";
      el.textContent = msg;
      field.appendChild(el);
      input.style.borderColor = "#2e7d32";
    }

  /* ══════════════════════════════════════════════
     제출 버튼 활성화 조건 확인
  ══════════════════════════════════════════════ */
  function updateSubmitBtn() {
    const nicknameOk  = /^[가-힣a-zA-Z0-9]{2,12}$/.test(nicknameInput.value.trim());
    const emailOk     = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailInput.value.trim());
    const verifiedOk  = emailVerified;
    const pwOk        = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$/.test(passwordInput.value);
    const pwMatchOk   = passwordInput.value === pwConfirmInput.value && pwConfirmInput.value !== "";
    const genderOk    = !!document.querySelector('input[name="gender"]:checked');
    const birthOk     = birthYear.value && birthMonth.value && birthDay.value;
    const interestOk  = document.querySelectorAll('input[name="interests"]:checked').length >= 1;
    const termsOk     = (
      document.querySelector('input[name="agree_service"]').checked &&
      document.querySelector('input[name="agree_privacy"]').checked &&
      document.querySelector('input[name="agree_age"]').checked
    );

    const allOk = nicknameOk && emailOk && verifiedOk && pwOk && pwMatchOk
               && genderOk && birthOk && interestOk && termsOk;

    submitBtn.disabled = !allOk;
    submitBtn.style.background = allOk ? "" : "";
    submitBtn.style.opacity    = allOk ? "1" : "0.5";
    submitBtn.style.cursor     = allOk ? "pointer" : "default";
  }

  /* ══════════════════════════════════════════════
     닉네임 유효성 검사
  ══════════════════════════════════════════════ */
  nicknameInput.addEventListener("input", function () {
    const val = this.value.trim();
    if (!val) { removeError(this); updateSubmitBtn(); return; }

    if (!/^[가-힣a-zA-Z0-9]{2,12}$/.test(val)) {
      showError(this, "닉네임은 한글, 영문 대소문자, 숫자만 입력할 수 있습니다. (2~12자)");
    } else {
      removeError(this);
    }
    updateSubmitBtn();
  });
  
// ── 닉네임 금칙어 검사 (포커스 아웃 시 서버에 요청, 결과를 필드에 즉시 표시)
// ── 형식 오류(input 이벤트)와 별개로 동작하며, 형식 통과한 경우에만 호출됨
  nicknameInput.addEventListener("blur", async function () {
    const val = this.value.trim();
    if (!val || !/^[가-힣a-zA-Z0-9]{2,12}$/.test(val)) return;

    try {
        const res = await fetch("/member/nickname-check/", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ nickname: val })
        });
        const data = await res.json();
        if (!data.available) {
            showError(nicknameInput, data.error);
        } else {
            removeError(nicknameInput);
        }
    } catch (e) {}
  });

  /* ══════════════════════════════════════════════
     이메일 입력 → 인증코드 발송 버튼 활성화
  ══════════════════════════════════════════════ */
  emailInput.addEventListener("input", function () {
    const val = this.value.trim();
    const valid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(val);

    // 이메일 바뀌면 인증 초기화
    emailVerified = false;
    emailCodeInput.disabled = true;
    checkBtn.classList.remove("is-active");
    removeError(emailCodeInput);

    if (val && !valid) {
      showError(this, "이메일 형식이 맞지 않습니다.");
      if (cooldownLeft <= 0) sendBtn.disabled = true; // 쿨다운 중엔 버튼 상태 건드리지 않음 (깜빡임 방지)
    } else {
      removeError(this);
      if (cooldownLeft <= 0) sendBtn.disabled = !valid;  // 쿨다운 중엔 버튼 상태 건드리지 않음 (깜빡임 방지)
    }
    updateSubmitBtn();
  });

  /* ══════════════════════════════════════════════
     인증코드 발송 버튼
  ══════════════════════════════════════════════ */
  sendBtn.addEventListener("click", async function () {
    const email = emailInput.value.trim();

    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      showError(emailInput, "이메일 형식이 맞지 않습니다.");
      return;
    }

    sendBtn.disabled = true;

    try {
      const res = await fetch("/member/email/send-code/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();

      if (res.ok) {
        removeError(emailInput);
        // 인증코드 입력창 활성화
        emailCodeInput.disabled = false;
        emailCodeInput.value = "";
        emailCodeInput.focus();
        startCooldown();
        startCodeTimer();
      } else {
        showError(emailInput, data.error || "인증코드 발송에 실패했습니다.");
        sendBtn.disabled = false;
        sendBtn.textContent = "인증코드 발송";
      }
    } catch {
      showError(emailInput, "네트워크 오류가 발생했습니다. 다시 시도해 주세요.");
      sendBtn.disabled = false;
      sendBtn.textContent = "인증코드 발송";
    }
  });

  /* 30초 쿨다운 타이머 */
  function startCooldown() {
    cooldownLeft = 30;
    sendBtn.textContent = `재발송 (${cooldownLeft}s)`;
    sendBtn.disabled = true;

    clearInterval(cooldownTimer);
    cooldownTimer = setInterval(function () {
      cooldownLeft--;
      if (cooldownLeft <= 0) {
        clearInterval(cooldownTimer);
        sendBtn.disabled = false;
        sendBtn.textContent = "재발송";
      } else {
        sendBtn.textContent = `재발송 (${cooldownLeft}s)`;
      }
    }, 1000);
  }

/* 3분 유효시간 타이머 (인증코드 입력칸의 placeholder로 표시) */
  let codeTimeLeft = 0;
  const ORIGINAL_CODE_PLACEHOLDER = "인증코드를 입력하세요";

  function fmtCodeTime(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  function refreshCodeTimerVisibility() {
    if (!codeTimerInterval) return;
    const clamped = Math.max(codeTimeLeft, 0);
    emailCodeInput.placeholder = "남은시간 " + fmtCodeTime(clamped);
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
    emailCodeInput.placeholder = ORIGINAL_CODE_PLACEHOLDER;
  }

  /* ══════════════════════════════════════════════
     인증코드 입력 → 확인 버튼 활성화
  ══════════════════════════════════════════════ */
  emailCodeInput.addEventListener("input", function () {
    const hasCode = this.value.trim().length > 0;
    checkBtn.classList.toggle("is-active", hasCode);
    removeError(this);
    updateSubmitBtn();
  });

  /* ══════════════════════════════════════════════
     인증코드 확인 버튼
  ══════════════════════════════════════════════ */
  checkBtn.addEventListener("click", async function () {
    if (!this.classList.contains("is-active")) return;

    const email = emailInput.value.trim();
    const code  = emailCodeInput.value.trim();

    // stopCodeTimer(); ← 이 줄 삭제

    if (!code) {
      showError(emailCodeInput, "인증코드를 입력해 주세요.");
      return;
    }
    try {
      const res = await fetch("/member/email/verify-code/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ email, code }),
      });
      const data = await res.json();

      if (res.ok) {
        stopCodeTimer();  // ← 성공했을 때만 여기로 이동
        clearInterval(cooldownTimer);  // 추가
        cooldownLeft = 0;              // 추가
        emailVerified = true;
        showSuccess(emailCodeInput, "이메일 인증에 성공하였습니다.");
        checkBtn.classList.remove("is-active");
        checkBtn.disabled = true;
        sendBtn.disabled = true; // 추가: 인증 성공 후 재발송 방지
        sendBtn.textContent = "인증완료";  // 추가
        emailInput.disabled = true; // 추가: 이메일 변경 방지
        emailCodeInput.disabled = true;
        emailInput.style.color =  " #aabfb2"; // 추가: 이메일 입력창 비활성화 시 색상 변경
      } else {
        emailVerified = false;
        showError(emailCodeInput, data.error || "인증번호가 올바르지 않습니다.");
        emailCodeInput.value = "";
        checkBtn.classList.remove("is-active");
        refreshCodeTimerVisibility(); // ← placeholder 타이머 다시 표시
      }
    } catch {
      showError(emailCodeInput, "네트워크 오류가 발생했습니다. 다시 시도해 주세요.");
    }
    updateSubmitBtn();
  });

  /* ══════════════════════════════════════════════
     비밀번호 유효성 검사
  ══════════════════════════════════════════════ */
  const PW_PATTERN = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$/;

  passwordInput.addEventListener("input", function () {
    const val = this.value;
    if (!val) { removeError(this); updateSubmitBtn(); return; }

    if (!PW_PATTERN.test(val)) {
      showError(this, "영문, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요.");
    } else {
      showSuccess(this, "사용 가능한 비밀번호입니다.");
    }

    // 확인란도 같이 체크
    if (pwConfirmInput.value && PW_PATTERN.test(this.value)) {
      if (this.value !== pwConfirmInput.value) {
        showError(pwConfirmInput, "비밀번호가 일치하지 않습니다.");
      } else {
        showSuccess(pwConfirmInput, "비밀번호가 일치합니다.");
      }
    }
    updateSubmitBtn();
  });

  pwConfirmInput.addEventListener("input", function () {
    const val = this.value;
    if (!val) { removeError(this); updateSubmitBtn(); return; }

    if (passwordInput.value !== val) {
      showError(this, "비밀번호가 일치하지 않습니다.");
    } else {
      showSuccess(this, "비밀번호가 일치합니다.");
    }
    updateSubmitBtn();
  });

  pwConfirmInput.addEventListener("blur", function () {
    const pwVal = passwordInput.value;
    if (pwVal && !PW_PATTERN.test(pwVal)) {
      showError(passwordInput, "영문, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요.");
    }
  });

  passwordInput.addEventListener("blur", function () {
    const val = this.value;
    if (!val) { removeError(this); return; }
    if (!PW_PATTERN.test(val)) {
      showError(this, "영문, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요.");
    }
  });



  /* ══════════════════════════════════════════════
     생년월일 유효성
  ══════════════════════════════════════════════ */
  [birthYear, birthMonth, birthDay].forEach(function (el) {
    el.addEventListener("input", function () {
      updateSubmitBtn();
    });
  });

  birthYear.addEventListener("blur", function () {
    const y = parseInt(this.value);
    if (this.value && (y < 1900 || y > new Date().getFullYear())) {
      showError(this, "올바른 연도를 입력해 주세요.");
    } else {
      removeError(this);
    }
  });

  birthMonth.addEventListener("blur", function () {
    const m = parseInt(this.value);
    if (this.value && (m < 1 || m > 12)) {
      showError(this, "1~12 사이의 월을 입력해 주세요.");
    } else {
      removeError(this);
    }
  });

birthDay.addEventListener("blur", function () {
    const d = parseInt(this.value);
    const year = parseInt(birthYear.value);
    const month = parseInt(birthMonth.value);
    
    if (!this.value) { removeError(this); return; }
    
    if (d < 1 || d > 31) {
      showError(this, "1~31 사이의 일을 입력해 주세요.");
      return;
    }
    
    if (year && month >= 1 && month <= 12) {
      const max = new Date(year, month, 0).getDate();
      if (d > max) {
        showError(this, `${month}월은 최대 ${max}일까지 입력할 수 있습니다.`);
        return;
      }
    }
    
    removeError(this);
  });

  // ↓ 여기 추가
  function getMaxDay(year, month) {
    return new Date(year, month, 0).getDate();
  }

  birthMonth.addEventListener("input", function () {
    const year = parseInt(birthYear.value) || new Date().getFullYear();
    const month = parseInt(this.value);
    if (month >= 1 && month <= 12) {
      const max = getMaxDay(year, month);
      birthDay.max = max;
      if (parseInt(birthDay.value) > max) birthDay.value = max;
    }
  });

  birthYear.addEventListener("input", function () {
    const year = parseInt(this.value);
    const month = parseInt(birthMonth.value);
    if (year && month >= 1 && month <= 12) {
      const max = getMaxDay(year, month);
      birthDay.max = max;
      if (parseInt(birthDay.value) > max) birthDay.value = max;
    }
  });

  /* ══════════════════════════════════════════════
     관심사 최대 3개 제한
  ══════════════════════════════════════════════ */
  document.querySelectorAll('input[name="interests"]').forEach(function (cb) {
    cb.addEventListener("change", function () {
      const checked = document.querySelectorAll('input[name="interests"]:checked');
      const all     = document.querySelectorAll('input[name="interests"]');

      if (checked.length >= 3) {
        all.forEach(function (item) {
          if (!item.checked) {
            item.disabled = true;
            item.closest(".interest-item").style.opacity = "0.4";
            item.closest(".interest-item").style.cursor  = "default";
          }
        });
      } else {
        all.forEach(function (item) {
          item.disabled = false;
          item.closest(".interest-item").style.opacity = "1";
          item.closest(".interest-item").style.cursor  = "pointer";
        });
      }

      removeBlockError(".signup-interest-grid");
      updateSubmitBtn();
    });
  });

  /* ══════════════════════════════════════════════
     성별 / 약관 변경 시 버튼 상태 갱신
  ══════════════════════════════════════════════ */
  document.querySelectorAll('input[name="gender"]').forEach(function (r) {
    r.addEventListener("change", updateSubmitBtn);
  });

  document.querySelectorAll('.signup-terms input[type="checkbox"]').forEach(function (cb) {
    cb.addEventListener("change", updateSubmitBtn);
  });

  /* ══════════════════════════════════════════════
     거주지 시/도 → 시/군/구 연동 (regions.js 활용)
  ══════════════════════════════════════════════ */
  document.addEventListener("DOMContentLoaded", function () {
    if (window.initRegionSelectors) {
      window.initRegionSelectors("sido-select", "sigungu-select");
    }

    // HTML의 select name 속성을 id로도 참조할 수 있도록 id 추가
    const sidoEl    = document.querySelector('select[name="sido"]');
    const sigunguEl = document.querySelector('select[name="sigungu"]');
    if (sidoEl)    sidoEl.id    = "sido-select";
    if (sigunguEl) sigunguEl.id = "sigungu-select";

    if (window.KR_REGIONS && sidoEl && sigunguEl) {
      // 초기 옵션 설정
      sidoEl.innerHTML = '<option value="">시/도 선택</option>';
      Object.keys(window.KR_REGIONS).forEach(function (sido) {
        const o = document.createElement("option");
        o.value = sido; o.textContent = sido;
        sidoEl.appendChild(o);
      });

      function fillSigungu(sido) {
        sigunguEl.innerHTML = '<option value="">시/군/구 선택</option>';
        const list = window.KR_REGIONS[sido] || [];
        list.forEach(function (g) {
          const o = document.createElement("option");
          o.value = g; o.textContent = g;
          sigunguEl.appendChild(o);
        });
      }

      sidoEl.addEventListener("change", function () {
        fillSigungu(this.value);
      });
    }

    // 초기 제출 버튼 비활성화
    updateSubmitBtn();
  });

  /* ══════════════════════════════════════════════
     비밀번호 눈 아이콘 (signup.html 인라인 스크립트 대체)
     — html에 이미 있으면 중복 동작하지만 무해함
  ══════════════════════════════════════════════ */
  document.querySelectorAll(".signup-eye").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const input = document.getElementById(btn.dataset.target);
      const icon  = btn.querySelector("i");
      if (!input || !icon) return;
      const isPw = input.type === "password";
      input.type = isPw ? "text" : "password";
      icon.className = isPw ? "bi bi-eye" : "bi bi-eye-slash";
    });
  });

  /* ══════════════════════════════════════════════
     폼 제출 (회원가입 API 호출)
  ══════════════════════════════════════════════ */
  form.addEventListener("submit", async function (e) {
    e.preventDefault();

    // 버튼이 disabled면 무시
    if (submitBtn.disabled) return;

    const interests = Array.from(
      document.querySelectorAll('input[name="interests"]:checked')
    ).map(cb => cb.value);

    const payload = {
      nickname:         nicknameInput.value.trim(),
      email:            emailInput.value.trim(),
      password:         passwordInput.value,
      password_confirm: pwConfirmInput.value,
      gender:           (document.querySelector('input[name="gender"]:checked') || {}).value || "",
      birth_year:       parseInt(birthYear.value)  || null,
      birth_month:      parseInt(birthMonth.value) || null,
      birth_day:        parseInt(birthDay.value)   || null,
      interests:        interests,
      sido:             (document.querySelector('select[name="sido"]') || {}).value || "",
      sigungu:          (document.querySelector('select[name="sigungu"]') || {}).value || "",
    };

    submitBtn.disabled = true;

    try {
      const res = await fetch("/member/signup/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.status === 201) {
        // 회원가입 성공 → 로그인 페이지로 이동
        window.location.href = "/login/";
      } else if (data.errors) {
        // 필드별 에러 표시
        const map = {
          nickname:         nicknameInput,
          email:            emailInput,
          password:         passwordInput,
          password_confirm: pwConfirmInput,
        };
        Object.entries(data.errors).forEach(([key, msg]) => {
          if (map[key]) showError(map[key], msg);
        });
        if (data.errors.interests) {
          showBlockError(".signup-interest-grid", data.errors.interests);
        }
        if (data.errors.birth) {
          showError(birthYear, data.errors.birth);
        }
        if (data.errors.gender) {
          showBlockError(".signup-radio-group", data.errors.gender);
        }
      } else {
        alert(data.message || data.error || "회원가입 중 오류가 발생했습니다.");
      }
    } catch {
      alert("네트워크 오류가 발생했습니다. 다시 시도해 주세요.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '회원가입 완료 <i class="bi bi-chevron-right"></i>';
      updateSubmitBtn();
    }
  });

  /* ══════════════════════════════════════════════
     CSRF 토큰 헬퍼
  ══════════════════════════════════════════════ */
  function getCsrf() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : "";
  }

})();

// 약관 모달
const termsOverlay = document.getElementById("terms-modal");
const termsIframe  = termsOverlay.querySelector(".terms-modal-iframe");

document.querySelectorAll(".terms-modal-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    termsIframe.src = btn.dataset.src + "?modal=1";
    termsOverlay.style.display = "flex";
  });
});

termsOverlay.addEventListener("click", (e) => {
  if (e.target === termsOverlay) {
    termsOverlay.style.display = "none";
    termsIframe.src = "";
  }
});

termsOverlay.querySelector(".terms-modal-close").addEventListener("click", () => {
  termsOverlay.style.display = "none";
  termsIframe.src = "";
});