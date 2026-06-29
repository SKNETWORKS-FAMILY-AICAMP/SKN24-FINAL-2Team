/**
 * PoliCity — login.js
 * JWT 로그인 + 잠금 안내 + 챗봇 페이지 리다이렉트
 */
(function () {
  "use strict";

  const form       = document.querySelector(".login-form");
  const submitBtn  = form.querySelector(".login-submit");
  const emailInput = form.querySelector('input[name="email"]');
  const pwInput    = form.querySelector('input[name="password"]');

  /* ── 에러 메시지 ── */
function showError(msg) {
  const wrap = document.getElementById("login-error-wrap");
  wrap.innerHTML = "";
  const el = document.createElement("p");
  el.style.cssText = "margin:0; color:#e53935; font-size:13px; font-weight:500; text-align:center; white-space:pre-line;";
  el.textContent = msg;
  wrap.appendChild(el);
}

function removeError() {
  const wrap = document.getElementById("login-error-wrap");
  if (wrap) wrap.innerHTML = "";
}
  /* ── CSRF ── */
  function getCsrf() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : "";
  }

  /* ── 로그인 성공 후 이동할 경로 (next 파라미터) ── */
  function getRedirectTarget() {
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next");

    // next가 없거나, 이상한 값이면 기본 페이지(/chat/)로 보낸다.
    if (!next || !next.startsWith("/") || next.startsWith("//")) {
      return "/chat/";
    }
    if (next.startsWith("/login") || next.startsWith("/signup")) {
      return "/chat/";
    }
    return next;
  }

  /* ── 입력 시 에러 제거 ── */
  emailInput.addEventListener("input", removeError);
  pwInput.addEventListener("input", removeError);

  /* ── 폼 제출 ── */
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    removeError();

    const email    = emailInput.value.trim();
    const password = pwInput.value;

    if (!email) {
      showError("이메일을 입력해주세요.");
      return;
    }
    if (!password) {
      showError("비밀번호를 입력해 주세요.");
      return;
    }

    submitBtn.disabled = true;

    try {
      const res = await fetch("/member/login/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrf(),
        },
        body: JSON.stringify({ email, password }),
      });

      const data = await res.json();

      if (res.ok) {
        localStorage.setItem("access_token",  data.access);
        localStorage.setItem("refresh_token", data.refresh);
        localStorage.setItem("nickname",      data.nickname);
        window.location.href = getRedirectTarget();
      } else {
        if (data.locked) {
          if (data.locked_reason === "fail") {
            showError("계정이 잠겼습니다. 비밀번호 찾기를 진행해주세요.");
          } else {
            showError("혐오·욕설 표현 누적으로 계정이 제한되었습니다.\n고객센터에 문의해주세요.");
          }
        } else {
          showError("이메일 또는 비밀번호가 올바르지 않습니다.");
        }
      }
    } catch {
      showError("네트워크 오류가 발생했습니다. 다시 시도해 주세요.");
    } finally {
      submitBtn.disabled = false;
    }
  });

})();