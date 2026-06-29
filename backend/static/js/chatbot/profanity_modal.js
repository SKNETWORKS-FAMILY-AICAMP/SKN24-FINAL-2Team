(function () {
    var modal = document.getElementById('profanityModal');
    if (!modal) return;

    var confirmBtn = document.getElementById('profanityModalConfirm');

    function close() { modal.style.display = 'none'; }

    confirmBtn.addEventListener('click', close);
    modal.addEventListener('click', function (e) {
        if (e.target === modal) close();
    });

    window.showProfanityModal = function (foulCount) {
        var foulCountEl = document.getElementById('profanityFoulCount');
        var titleEl = document.getElementById('profanityModalTitle');
        var msgEl = document.getElementById('profanityModalMsg');
        var iconEl = document.getElementById('warning_icon');

        if (foulCountEl) foulCountEl.textContent = foulCount;

        if (parseInt(foulCount) >= 3) {
            if (iconEl) iconEl.textContent = '❌';
            if (titleEl) titleEl.textContent = '서비스 이용 영구 제한 안내';
            if (msgEl) msgEl.textContent =
                `이용약관 제10조 "이용자의 금지 행위" 위반 누적으로 인해 해당 계정은 더 이상 서비스를 이용하실 수 없습니다.

                사유: 지속적인 부적절한 표현(욕설, 모욕, 혐오 및 차별) 사용
                조치: 계정 영구 차단

                그동안 서비스를 이용해 주셔서 감사합니다.`;
            modal.style.display = 'flex';
            confirmBtn.addEventListener('click', function () {
                localStorage.removeItem('access_token');
                localStorage.removeItem('refresh_token');
                window.location.href = '/';
            }, { once: true });
        } else {
            if (iconEl) iconEl.textContent = '⚠️';
            if (titleEl) titleEl.textContent = '경고';
            if (msgEl) msgEl.textContent =
                `욕설, 모욕, 혐오 및 차별적 표현이 감지되었습니다.

부적절한 이용 행위가 지속될 경우 운영 정책에 의해 사전 통보 없이 서비스 이용이 영구 차단될 수 있습니다.`;
            modal.style.display = 'flex';
        }
    };
})();
