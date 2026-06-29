(function () {
    var modal = document.getElementById('welcomeModal');
    if (!modal) return;

    var confirmBtn = document.getElementById('welcomeModalConfirm');

    function close() { modal.style.display = 'none'; }

    confirmBtn.addEventListener('click', close);
    modal.addEventListener('click', function (e) {
        if (e.target === modal) close();
    });

    function checkFirstChat() {
        var token = localStorage.getItem('access_token') || '';
        if (!token) return;

        fetch('/api/chatbot/is-first-chat/', {
            method: 'GET',
            headers: {
                'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json',
            },
        })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (data) {
                if (data && data.is_first_chat) {
                    modal.style.display = 'flex';
                }
            })
            .catch(function () { /* silently ignore — modal is non-critical */ });
    }

    checkFirstChat();
})();
