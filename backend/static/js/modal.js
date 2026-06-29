var BTN_STYLES = {
    primary: 'flex:1;padding:10px;border:1.5px solid transparent;border-radius:var(--border-radius-md);background:var(--color-primary,#4f6ef7);font-family:inherit;font-size:var(--font-size-md);font-weight:700;color:#fff;cursor:pointer;transition:background .15s;',
    danger:  'flex:1;padding:10px;border:1.5px solid transparent;border-radius:var(--border-radius-md);background:var(--color-danger,#d94f4f);font-family:inherit;font-size:var(--font-size-md);font-weight:700;color:#fff;cursor:pointer;transition:background .15s;',
    ghost:   'flex:1;padding:10px;border:1.5px solid var(--border-color);border-radius:var(--border-radius-md);background:#fff;font-family:inherit;font-size:var(--font-size-md);font-weight:700;color:var(--text-secondary);cursor:pointer;transition:background .15s,border-color .15s;',
};

function showGenericModal(options) {
    document.getElementById('genericModalIcon').textContent  = options.icon  || '';
    document.getElementById('genericModalTitle').textContent = options.title || '';
    document.getElementById('genericModalMsg').textContent   = options.msg   || '';
    var btnsEl = document.getElementById('genericModalBtns');
    btnsEl.innerHTML = '';
    (options.buttons || []).forEach(function (btn) {
        var b = document.createElement('button');
        b.textContent = btn.label;
        b.style.cssText = BTN_STYLES[btn.variant] || BTN_STYLES.ghost;
        b.addEventListener('click', function () {
            closeGenericModal();
            if (btn.onClick) btn.onClick();
        });
        btnsEl.appendChild(b);
    });
    var modal = document.getElementById('genericModal');
    modal.style.display = 'flex';
}

function closeGenericModal() {
    document.getElementById('genericModal').style.display = 'none';
}

document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('genericModal').addEventListener('click', function (e) {
        if (e.target === this) closeGenericModal();
    });
});
