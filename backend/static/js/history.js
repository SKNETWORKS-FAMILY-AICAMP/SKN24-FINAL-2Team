// history.js — extracted from templates/pages/history.html
  document.querySelector('.main-nav .nav-history')?.classList.add('active');

  // 로그인 체크 (로그인 연동 완료 후 주석 해제)
  function getUserIdFromToken(token) {
    try {
      var base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      var payload = JSON.parse(atob(base64));
      return payload.user_id;
    } catch (e) { return null; }
  }
  var ACCESS_TOKEN = localStorage.getItem('access_token') || '';
  var USER_ID      = getUserIdFromToken(ACCESS_TOKEN);

  function getCsrfToken() {
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }
  var NICKNAME     = localStorage.getItem('nickname') || '나';
  if (!ACCESS_TOKEN || !USER_ID) { window.location.href = '/login/'; }

  // 날짜 포맷
  function fmtDate(iso) {
    var d = new Date(iso);
    return d.getFullYear() + '.' +
      String(d.getMonth()+1).padStart(2,'0') + '.' +
      String(d.getDate()).padStart(2,'0') + ' ' +
      String(d.getHours()).padStart(2,'0') + ':' +
      String(d.getMinutes()).padStart(2,'0');
  }

  // =====================================================================
  // 토론 히스토리
  // =====================================================================
  var debateSessions = [];

  function renderDebateRow(s) {
    var statusHtml = s.is_done
      ? '<span class="pill pill-done"><i class="bi bi-check-circle-fill"></i>완료</span>'
      : '<span class="pill pill-progress"><i class="bi bi-hourglass-split"></i>진행중</span>';
    var actionBtn = s.is_done
      ? '<button class="btn-poli-outline btn-replay-debate"><i class="bi bi-eye"></i>다시 보기</button>'
      : '<button class="btn-poli-outline btn-resume-debate"><i class="bi bi-play-fill"></i>이어하기</button>';
    var modeHtml = s.mode === 'aiuser'
      ? '<span style="font-size:0.8em;color:var(--color-primary);background:var(--bg-tag);white-space:nowrap;font-weight:700;padding:3px 8px;border-radius:999px;">AI vs User</span>'
      : '<span style="font-size:0.8em;color:var(--text-muted);background:#f0f0f0;white-space:nowrap;font-weight:700;padding:3px 8px;border-radius:999px;">AI vs AI</span>';
    return '<tr data-session-id="' + s.debate_session_id + '" data-is-done="' + s.is_done + '" data-card-id="' + (s.card_id || '') + '" data-current-round="' + (s.current_round || 0) + '" data-mode="' + (s.mode || 'aiai') + '">' +
      '<td class="sel-col"><input type="checkbox" class="row-check"></td>' +
      '<td><span class="topic">' + (s.debate_topic || s.card_title) + '</span></td>' +
      '<td class="text-center" style="white-space:nowrap;font-weight:700;color:var(--text-secondary);">' + (s.current_round || 1) + ' / 4</td>' +
      '<td class="text-center">' + modeHtml + '</td>' +
      '<td class="when">' + fmtDate(s.updated_at) + '</td>' +
      '<td class="text-center">' + statusHtml + '</td>' +
      '<td><div class="actions">' + actionBtn +
      '</div></td>' +
    '</tr>';
  }

  function loadDebateHistory() {
    fetch('/member/debates/history/?user_id=' + USER_ID)
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data) return;
      debateSessions = (data.sessions || []).slice().sort(function(a, b) { return new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at); });
      var tbody = document.getElementById('debateTbody');
      if (!debateSessions.length) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="7" class="text-center" style="padding:28px;color:var(--text-muted);">토론 히스토리가 없습니다.</td></tr>';
      } else {
        tbody.innerHTML = debateSessions.map(renderDebateRow).join('');
      }
      var card = tbody.closest('.hist-card');
      if (card && card._paginate) card._paginate();
    })
    .catch(function(e) { console.error('토론 히스토리 로드 실패', e); });
  }

  // 토론 행 클릭

  document.getElementById('debateTbody').addEventListener('click', function(e) {
    var tr = e.target.closest('tr');
    if (!tr || tr.classList.contains('empty-row')) return;
    var sessionId = tr.dataset.sessionId;
    var isDone    = tr.dataset.isDone === 'true';

    // 이어하기
    if (e.target.closest('.btn-resume-debate')) {
      var currentRound = parseInt(tr.dataset.currentRound || '0', 10);
      if (currentRound === 0) {
        // 아직 시작 안 한 세션: 카드부터 다시 시작
        var cardId = tr.dataset.cardId;
        window.location.href = '/debate/?card_id=' + cardId;
      } else {
        window.location.href = '/debate/?session=' + sessionId;
      }
      return;
    }
    // 다시 보기
    if (e.target.closest('.btn-replay-debate')) {
      window.location.href = '/debate/?session=' + sessionId + '&readonly=true';
      return;
    }
    // 삭제
    if (e.target.closest('.btn-delete-debate')) {
      showDeleteModal('토론 삭제', '정말 삭제하시겠습니까?', function() {
        fetch('/member/debates/' + sessionId + '/delete/', {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCsrfToken() }
        }).then(function(res) { if (res.ok) loadDebateHistory(); });
      });
      return;
    }
    // 행 클릭 → 미리보기
    var table = tr.closest('.hist-table');
    if (table && table.classList.contains('selecting')) return;
    document.querySelectorAll('#debateTbody tr').forEach(function(r) { r.classList.remove('selected'); });
    tr.classList.add('selected');
    var s = debateSessions.find(function(x) { return String(x.debate_session_id) === sessionId; });
    if (!s) return;
    document.getElementById('dpTitle').textContent = s.debate_topic || s.card_title;
    document.getElementById('dpDate').textContent  = fmtDate(s.created_at);
    document.getElementById('dpVs').innerHTML = '';
    var dpStatus = document.getElementById('dpStatus');
    var dpRound  = document.getElementById('dpRound');
    if (s.is_done) {
      dpStatus.className = 'pill pill-done ms-1';
      dpStatus.innerHTML = '<i class="bi bi-check-circle-fill"></i>완료';
    } else {
      dpStatus.className = 'pill pill-progress ms-1';
      dpStatus.innerHTML = '<i class="bi bi-hourglass-split"></i>진행중';
    }
    dpRound.textContent = '라운드 ' + s.current_round + ' / 4';
    document.getElementById('dpStatusRow').style.display = 'flex';
  });


  // =====================================================================
  // 채팅 히스토리
  // =====================================================================
  var chatSessions = [];

  function renderChatRow(s) {
    return '<tr data-session-id="' + s.chat_session_id + '">' +
      '<td class="sel-col"><input type="checkbox" class="row-check"></td>' +
      '<td><span class="topic">' + s.session_title + '</span></td>' +
      '<td class="when">' + fmtDate(s.updated_at) + '</td>' +
      '<td><div class="actions">' +
        '<button class="btn-poli-outline btn-resume-chat"><i class="bi bi-play-fill"></i>이어하기</button>' +
      '</div></td>' +
    '</tr>';
  }

  function loadChatHistory() {
    fetch('/member/chat-history/', {
      headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
    })
    .then(function(res) {
      if (res.status === 401) { window.location.href = '/login/'; return; }
      return res.json();
    })
    .then(function(data) {
      if (!data) return;
      chatSessions = (data.sessions || data).slice().sort(function(a, b) { return new Date(b.updated_at) - new Date(a.updated_at); });
      var tbody = document.getElementById('chatTbody');
      if (!chatSessions.length) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="4" class="text-center" style="padding:28px;color:var(--text-muted);">채팅 히스토리가 없습니다.</td></tr>';
      } else {
        tbody.innerHTML = chatSessions.map(renderChatRow).join('');
      }
      var card = tbody.closest('.hist-card');
      if (card && card._paginate) card._paginate();
    })
    .catch(function(e) { console.error('채팅 히스토리 로드 실패', e); });
  }

  // 채팅 행 클릭: 이어하기 / 삭제 / 미리보기
  document.getElementById('chatTbody').addEventListener('click', function(e) {
    var tr = e.target.closest('tr');
    if (!tr || tr.classList.contains('empty-row')) return;
    var sessionId = tr.dataset.sessionId;

    // 이어하기
    if (e.target.closest('.btn-resume-chat')) {
      window.location.href = '/chat/?session=' + sessionId;
      return;
    }
    // 삭제
    if (e.target.closest('.btn-delete-chat')) {
      showDeleteModal('채팅 삭제', '정말 삭제하시겠습니까?', function() {
        fetch('/member/chat-history/' + sessionId + '/', {
          method: 'DELETE',
          headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
        }).then(function(res) { if (res.ok) loadChatHistory(); });
      });
      return;
    }
    // 행 클릭 → 미리보기
    var table = tr.closest('.hist-table');
    if (table && table.classList.contains('selecting')) return;
    document.querySelectorAll('#chatTbody tr').forEach(function(r) { r.classList.remove('selected'); });
    tr.classList.add('selected');
    var s = chatSessions.find(function(x) { return String(x.chat_session_id) === sessionId; });
    if (!s) return;
    var typeLabel = s.card_type === 'news' ? '뉴스 카드' : '정책 카드';
    document.getElementById('cpTitle').textContent = s.session_title;
    document.getElementById('cpDate').innerHTML = typeLabel + ' · ' + fmtDate(s.updated_at);
    document.getElementById('cpBody').innerHTML =
      '<div class="chat-line"><div class="cava"><img src="/static/assets/poli_profile.png" alt=""></div>' +
      '<div class="bubble ai">이어서 대화를 시작하려면 <b>이어하기</b>를 눌러주세요.</div></div>';
  });

  // =====================================================================
  // 페이지네이션 + 선택/삭제 (공통)
  // =====================================================================
  var ROWS_PER_PAGE = 4;
  document.querySelectorAll('.hist-card').forEach(function (card) {
    var tbody     = card.querySelector('tbody');
    var table     = card.querySelector('.hist-table');
    var pager     = card.querySelector('.hist-pager');
    var tools     = card.querySelector('.hist-tools');
    var selectBtn = tools.querySelector('[data-act="select"]');
    var actions   = tools.querySelector('[data-actions]');
    var countEl   = actions.querySelector('.bm-count b');
    var allBtn    = actions.querySelector('[data-act="all"]');
    var delBtn    = actions.querySelector('[data-act="delete"]');
    var cancelBtn = actions.querySelector('[data-act="cancel"]');
    var page      = 0;
    var colspan   = table.querySelectorAll('thead th').length;
    var isChat    = card.dataset.hist === 'chat';

    function rows() {
      return Array.prototype.slice.call(tbody.querySelectorAll('tr')).filter(function(r) {
        return !r.classList.contains('empty-row');
      });
    }

    function renderPager() {
      var rs    = rows();
      var pages = Math.max(1, Math.ceil(rs.length / ROWS_PER_PAGE));
      if (page >= pages) page = pages - 1;
      rs.forEach(function(r, i) { r.classList.toggle('d-none', Math.floor(i / ROWS_PER_PAGE) !== page); });
      pager.innerHTML = '';
      if (pages <= 1) return;
      var prev = document.createElement('button');
      prev.innerHTML = '<i class="bi bi-chevron-left"></i>'; prev.disabled = page === 0;
      prev.addEventListener('click', function() { page--; renderPager(); });
      pager.appendChild(prev);
      for (var p = 0; p < pages; p++) (function(p) {
        var b = document.createElement('button');
        b.textContent = p + 1; b.className = p === page ? 'active' : '';
        b.addEventListener('click', function() { page = p; renderPager(); });
        pager.appendChild(b);
      })(p);
      var next = document.createElement('button');
      next.innerHTML = '<i class="bi bi-chevron-right"></i>'; next.disabled = page === pages - 1;
      next.addEventListener('click', function() { page++; renderPager(); });
      pager.appendChild(next);
    }
    card._paginate = renderPager;

    function updateCount() { countEl.textContent = tbody.querySelectorAll('.row-check:checked').length; }
    function setMode(on) {
      table.classList.toggle('selecting', on);
      selectBtn.classList.toggle('d-none', on);
      actions.classList.toggle('d-none', !on);
      if (!on) {
        tbody.querySelectorAll('.row-check').forEach(function(c) { c.checked = false; });
        tbody.querySelectorAll('tr').forEach(function(r) { r.classList.remove('row-checked'); });
      }
      updateCount();
    }
    selectBtn.addEventListener('click', function() { setMode(true); });
    cancelBtn.addEventListener('click', function() { setMode(false); });

    tbody.addEventListener('click', function(e) {
      if (!table.classList.contains('selecting')) return;
      if (e.target.closest('button')) return;
      var tr = e.target.closest('tr');
      if (!tr) return;
      var chk = tr.querySelector('.row-check');
      if (!chk) return;
      if (e.target !== chk) chk.checked = !chk.checked;
      tr.classList.toggle('row-checked', chk.checked);
      updateCount();
    });

    allBtn.addEventListener('click', function() {
      var visible = rows().filter(function(r) { return !r.classList.contains('d-none'); });
      var boxes   = visible.map(function(r) { return r.querySelector('.row-check'); });
      var allOn   = boxes.every(function(b) { return b.checked; });
      boxes.forEach(function(b) { b.checked = !allOn; b.closest('tr').classList.toggle('row-checked', !allOn); });
      updateCount();
    });

    // 선택 삭제
    delBtn.addEventListener('click', function() {
      var checked = tbody.querySelectorAll('.row-check:checked');
      if (!checked.length) { showAlertModal('삭제할 항목을 선택해주세요.'); return; }
      var label = isChat ? '채팅 삭제' : '토론 삭제';
      showDeleteModal(label, checked.length + '개의 히스토리를 삭제하시겠습니까?', function() {
      var trs = Array.prototype.map.call(checked, function(c) { return c.closest('tr'); });
      var promises = trs.map(function(tr) {
        var sid = tr.dataset.sessionId;
        if (isChat) {
          return fetch('/member/chat-history/' + sid + '/', {
            method: 'DELETE', headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
          });
        } else {
          return fetch('/member/debates/' + sid + '/delete/', {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCsrfToken() }
          });
        }
      });
        Promise.all(promises).then(function() {
          setMode(false);
          if (isChat) loadChatHistory(); else loadDebateHistory();
        });
      });
    });

  });

  function showDeleteModal(title, msg, onConfirm) {
    var modal = document.getElementById('deleteModal');
    document.getElementById('deleteModalTitle').textContent = title;
    document.getElementById('deleteModalMsg').textContent = msg;
    modal.style.display = 'flex';
    function cleanup() { modal.style.display = 'none'; document.getElementById('deleteModalConfirm').onclick = null; document.getElementById('deleteModalCancel').onclick = null; }
    document.getElementById('deleteModalConfirm').onclick = function() { cleanup(); onConfirm(); };
    document.getElementById('deleteModalCancel').onclick = cleanup;
    modal.onclick = function(e) { if (e.target === modal) cleanup(); };
  }
  function showAlertModal(msg) {
    var modal = document.getElementById('alertModal');
    document.getElementById('alertModalMsg').textContent = msg;
    modal.style.display = 'flex';
    function cleanup() { modal.style.display = 'none'; document.getElementById('alertModalOk').onclick = null; }
    document.getElementById('alertModalOk').onclick = cleanup;
    modal.onclick = function(e) { if (e.target === modal) cleanup(); };
  }
  // 페이지 진입 시 데이터 로드
  loadChatHistory();
  loadDebateHistory();
