// mypage.js — extracted from templates/pages/mypage.html
// 페이지 로드 시 헤더의 '마이페이지' 메뉴 활성화

// region cascading selects (전국 시/도 → 시/군/구)
var _savedSido = localStorage.getItem('policity_sido');
var _savedSigungu = localStorage.getItem('policity_sigungu');
if (window.initRegionSelectors) initRegionSelectors('sidoSelect', 'sigunguSelect',
  (_savedSido ? { sido: _savedSido, sigungu: _savedSigungu } : undefined));

// regions 전체 목록 캐시
fetch('/member/regions/', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || '') } })
  .then(function (r) { return r.json(); })
  .then(function (data) { window._allRegions = Array.isArray(data) ? data : []; })
  .catch(function () { window._allRegions = []; });

// 지역 변경 시 자동 저장
// _allRegions에서 못 찾으면 POST로 get_or_create 후 저장
function autoSaveRegion() {
  var sido = document.getElementById('sidoSelect').value;
  var sigungu = document.getElementById('sigunguSelect').value;
  if (!sido || !sigungu) return;

  function patchRegion(regionId) {
    fetch('/member/' + USER_ID + '/', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + ACCESS_TOKEN },
      body: JSON.stringify({ region: regionId })
    }).catch(function (e) { console.error('지역 저장 실패', e); });
  }

  var reg = (window._allRegions || []).find(function (r) { return r.sido === sido && r.sigungu === sigungu; });
  if (reg) {
    patchRegion(reg.region_id);
  } else {
    // DB에 없는 지역이면 생성 후 저장
    fetch('/member/regions/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + ACCESS_TOKEN },
      body: JSON.stringify({ sido: sido, sigungu: sigungu })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.region_id) {
          if (window._allRegions) window._allRegions.push(data);
          patchRegion(data.region_id);
        }
      }).catch(function (e) { console.error('지역 생성 실패', e); });
  }
}

// inline nickname edit on the profile name (click name or pencil)
function hasBanned(v) { return false; }
(function () {
  var wrap = document.getElementById('nameEdit');
  if (!wrap) return;
  var text = document.getElementById('displayName');
  var input = wrap.querySelector('.name-input');
  var pencil = wrap.querySelector('.name-pencil');
  var err = wrap.querySelector('.name-err');
  var icon = pencil.querySelector('i');

  try { var saved = localStorage.getItem('policity_nickname'); if (saved) { text.textContent = saved; input.value = saved; } } catch (e) { }

  function validate(v) {
    v = v.trim();
    if (v.length < 2 || v.length > 12) return '닉네임은 2~12자여야 합니다.';
    if (/\s/.test(v)) return '공백은 사용할 수 없습니다.';
    if (!/^[가-힣a-zA-Z0-9]+$/.test(v)) return '한글, 영문, 숫자만 사용할 수 있습니다. (특수문자 불가)';
    if (hasBanned(v)) return '사용할 수 없는 닉네임입니다.';
    return '';
  }
  function start() {
    wrap.classList.add('editing'); wrap.classList.remove('invalid');
    text.classList.add('d-none'); input.classList.remove('d-none');
    input.value = text.textContent.trim();
    input.focus(); input.select();
    icon.className = 'bi bi-check-lg';
  }
  function stop() {
    wrap.classList.remove('editing', 'invalid');
    text.classList.remove('d-none'); input.classList.add('d-none');
    icon.className = 'bi bi-pencil';
  }

  // 닉네임 금칙어 서버 검증
  async function checkBanned(nick) {
    try {
      const res = await fetch('/member/nickname-check/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname: nick })
      });
      const data = await res.json();
      if (!data.available) {
        wrap.classList.add('invalid');
        err.textContent = data.error || '사용할 수 없는 닉네임입니다.';
        return false;
      }
      return true;
    } catch (e) { return true; }
  }

  input.addEventListener('blur', async function () {
    const val = this.value.trim();
    if (!val || validate(val)) return;
    await checkBanned(val);
  });

  async function save() {
    var msg = validate(input.value);
    if (msg) { wrap.classList.add('invalid'); err.textContent = msg; input.focus(); return; }
    var nick = input.value.trim();
    var ok = await checkBanned(nick);
    if (!ok) { input.focus(); return; }
    text.textContent = nick;
    try { localStorage.setItem('policity_nickname', nick); localStorage.setItem('nickname', nick); } catch (e) { }
    stop();
    // 엔터/체크 즉시 서버 반영
    if (typeof USER_ID !== 'undefined' && USER_ID && ACCESS_TOKEN) {
      fetch('/member/' + USER_ID + '/', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + ACCESS_TOKEN },
        body: JSON.stringify({ nickname: nick })
      }).then(function (res) {
        if (!res.ok) throw new Error();
        var headerNick = document.getElementById('header-nickname');
        if (headerNick) headerNick.textContent = nick + '님';
      }).catch(function () {
        wrap.classList.add('invalid'); err.textContent = '저장에 실패했어요. 다시 시도해주세요.';
        start();
      });
    }
  }
  pencil.addEventListener('click', function () { wrap.classList.contains('editing') ? save() : start(); });
  text.addEventListener('click', start);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { stop(); }
  });
  input.addEventListener('input', function () {
    if (wrap.classList.contains('invalid') && !validate(input.value)) wrap.classList.remove('invalid');
  });
})();

// gender-aware profile avatar
(function () {
  var tg = document.getElementById('genderToggle');
  var avatar = document.getElementById('profileAvatar');
  // 경로 인식을 위해 절대 경로로 안전하게 교체
  var srcByGender = { male: '/static/assets/boy.png', female: '/static/assets/girl.png', other: '' };
  if (!tg) return;
  tg.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-gender]');
    if (!b) return;
    tg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('active', x === b); });
    if (b.dataset.gender === 'other') {
      avatar.style.display = 'none';
      avatar.closest('.profile-avatar').style.background = '#d4ede0';
    } else {
      avatar.style.display = '';
      avatar.closest('.profile-avatar').style.background = '';
      avatar.src = srcByGender[b.dataset.gender];
    }
  });
})();

// sidebar view switching
(function () {
  var nav = document.getElementById('mpNav');
  var views = document.querySelectorAll('.mp-view');

  function switchView(v) {
    nav.querySelectorAll('a').forEach(function (n) { n.classList.toggle('active', n.dataset.view === v); });
    views.forEach(function (s) { s.classList.toggle('d-none', s.dataset.view !== v); });
    if (v === 'bookmark') loadBookmarks();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // URL path로 초기 탭 결정
  var pathView = location.pathname.replace(/\/$/, '').split('/').pop();
  if (['profile', 'bookmark', 'withdraw'].includes(pathView)) switchView(pathView);

  nav.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-view]');
    if (!a) return;
    e.preventDefault();
    var v = a.dataset.view;
    history.pushState(null, '', '/mypage/' + v + '/');
    switchView(v);
  });
})();

// interest chips (max 3)
(function () {
  var grid = document.getElementById('kwGrid');
  grid.addEventListener('click', function (e) {
    var chip = e.target.closest('.kw-chip');
    if (!chip) return;
    var on = grid.querySelectorAll('.kw-chip.on');
    if (!chip.classList.contains('on') && on.length >= 3) { chip.animate([{ transform: 'translateX(-3px)' }, { transform: 'translateX(3px)' }, { transform: 'translateX(0)' }], { duration: 160 }); return; }
    chip.classList.toggle('on');
  });
})();

// password eye toggles
document.querySelectorAll('.input-with-eye .eye').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var input = btn.parentElement.querySelector('input');
    var icon = btn.querySelector('i');
    if (input.type === 'password') { input.type = 'text'; icon.className = 'bi bi-eye'; }
    else { input.type = 'password'; icon.className = 'bi bi-eye-slash'; }
  });
});

// =====================================================================
// 북마크 API 연동
// =====================================================================

function getUserIdFromToken(token) {
  try {
    var base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    var payload = JSON.parse(atob(base64));
    return payload.user_id;
  } catch (e) { return null; }
}
var ACCESS_TOKEN = localStorage.getItem('access_token') || '';
var USER_ID = ACCESS_TOKEN ? getUserIdFromToken(ACCESS_TOKEN) : null;
if (!ACCESS_TOKEN || !USER_ID) { window.location.href = '/login/'; }

// 카테고리 → 이미지/레이블 매핑
var CAT_MAP = {
  '일자리': { img: '/static/assets/job.png', key: 'job' },
  '주거': { img: '/static/assets/housing.png', key: 'housing' },
  '교육': { img: '/static/assets/education.png', key: 'education' },
  '문화': { img: '/static/assets/culture.png', key: 'culture' },
  '금융': { img: '/static/assets/finance.png', key: 'finance' },
  '생활복지': { img: '/static/assets/welfare.png', key: 'welfare' },
};

function getCatImg(categoryName) {
  return (CAT_MAP[categoryName] || {}).img || '/static/assets/job.png';
}
function getCatKey(categoryName) {
  return (CAT_MAP[categoryName] || {}).key || 'job';
}

// 날짜 포맷
function fmtDate(iso) {
  var d = new Date(iso);
  return d.getFullYear() + '.' +
    String(d.getMonth() + 1).padStart(2, '0') + '.' +
    String(d.getDate()).padStart(2, '0') + ' 추가';
}

// bm-item HTML 생성
function makeBmItem(bm) {
  var card = bm.card;
  var img = getCatImg(card.category_name);
  var catKey = getCatKey(card.category_name);
  var label = new Date(bm.created_at).toLocaleDateString('ko-KR').replace(/\. /g, '.').replace('.', '년 ').replace('.', '월 ') + '일';
  return '<label class="bm-item" data-cat="' + catKey + '" data-card-id="' + card.card_id + '" data-card-type="' + card.type + '" data-bm-id="' + bm.id + '">' +
    '<input type="checkbox" class="bm-check">' +
    '<div class="thumb"><img src="' + img + '" alt=""></div>' +
    '<div class="bm-body">' +
    '<div class="bm-title">' + card.card_title + '</div>' +
    '<div class="bm-desc">' + card.intro + '</div>' +
    '<div class="bm-date"><i class="bi bi-calendar3"></i>' + fmtDate(bm.created_at) + '</div>' +
    '</div>' +
    '</label>';
}

// 북마크 목록 불러오기
var allBookmarks = { policy: [], news: [] };

function loadBookmarks() {
  var token = localStorage.getItem('access_token');
  var userId = token ? getUserIdFromToken(token) : null;
  if (!token || !userId) { window.location.href = '/login/'; return; }
  fetch('/cards/api/bookmarks/' + userId + '/', {
    headers: { 'Authorization': 'Bearer ' + token }
  })
    .then(function (res) {
      if (res.status === 401) { window.location.href = '/login/'; return; }
      return res.json();
    })
    .then(function (bookmarks) {
      if (!bookmarks || !bookmarks.length) {
        allBookmarks.policy = [];
        allBookmarks.news = [];
        renderGrid('policy');
        renderGrid('news');
        updateCluster();
        return;
      }
      // card가 ID만 올 경우 bulk API로 카드 정보 가져오기
      var firstCard = bookmarks[0].card;
      if (typeof firstCard === 'number' || typeof firstCard === 'string') {
        var ids = bookmarks.map(function (b) { return b.card; }).join(',');
        fetch('/cards/api/bulk/?ids=' + ids, {
          headers: { 'Authorization': 'Bearer ' + token }
        })
          .then(function (r) { return r.json(); })
          .then(function (cards) {
            var cardMap = {};
            cards.forEach(function (c) { cardMap[c.card_id] = c; });
            bookmarks.forEach(function (b) { b.card = cardMap[b.card] || b.card; });
            allBookmarks.policy = bookmarks.filter(function (b) { return b.card && b.card.type === 'policy'; });
            allBookmarks.news = bookmarks.filter(function (b) { return b.card && b.card.type === 'news'; });
            renderGrid('policy');
            renderGrid('news');
            updateCluster();
          });
      } else {
        allBookmarks.policy = bookmarks.filter(function (b) { return b.card.type === 'policy'; });
        allBookmarks.news = bookmarks.filter(function (b) { return b.card.type === 'news'; });
        renderGrid('policy');
        renderGrid('news');
        updateCluster();
      }
    })
    .catch(function (e) { console.error('북마크 로드 실패', e); });
}

function renderGrid(type) {
  var grid = document.getElementById('bmGrid' + (type === 'policy' ? 'Policy' : 'News'));
  var list = grid.closest('.bm-list');
  if (!allBookmarks[type].length) {
    grid.innerHTML = '<p class="text-center" style="color:var(--text-muted);padding:24px 0;">북마크한 카드가 없습니다.</p>';
    list._renderPager && list._renderPager();
    return;
  }
  grid.innerHTML = allBookmarks[type].map(makeBmItem).join('');
  if (typeof selectedCardId !== 'undefined' && selectedCardId) {
    var activeItem = grid.querySelector('.bm-item[data-card-id="' + selectedCardId + '"]');
    if (activeItem) activeItem.classList.add('previewing');
  }
  paginate(list);
}

// bookmark tabs
(function () {
  var tabs = document.getElementById('bmTabs');
  if (!tabs) return;
  tabs.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-tab]');
    if (!b) return;
    var t = b.dataset.tab;
    tabs.querySelectorAll('button').forEach(function (x) { x.classList.toggle('active', x === b); });
    document.querySelectorAll('.bm-list').forEach(function (l) { l.classList.toggle('d-none', l.dataset.tab !== t); });
    // 탭 전환 시 선택 모드 초기화
    var selectBtn = document.getElementById('bmSelectBtn');
    var actions = document.getElementById('bmSelectActions');
    var countEl = document.getElementById('bmSelCount');
    if (actions && !actions.classList.contains('d-none')) {
      document.querySelectorAll('.bm-check').forEach(function (c) { c.checked = false; });
      document.querySelectorAll('.bm-item').forEach(function (it) { it.classList.remove('checked'); });
      if (countEl) countEl.textContent = '0';
    }
  });
})();

// 클러스터 업데이트
function updateCluster() {
  var all = allBookmarks.policy.concat(allBookmarks.news);
  document.querySelectorAll('.cluster .node').forEach(function (node) {
    var catKey = node.dataset.cat;
    var catName = Object.keys(CAT_MAP).find(function (k) { return CAT_MAP[k].key === catKey; });
    var cnt = all.filter(function (b) { return b.card.category_name === catName; }).length;
    var cntEl = node.querySelector('.cnt');
    if (cntEl) cntEl.textContent = cnt;
  });
}

// bookmark pagination (per list, 4 per page)
var PAGE_SIZE = 4;
function paginate(list) {
  var grid = list.querySelector('.bm-grid');
  var items = Array.prototype.slice.call(grid.querySelectorAll('.bm-item'));
  var pager = list.querySelector('.bm-pager');
  var pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  if (list._page == null || list._page >= pages) list._page = 0;

  function render() {
    items.forEach(function (it, i) {
      var p = Math.floor(i / PAGE_SIZE);
      it.classList.toggle('d-none', p !== list._page);
    });
    pager.innerHTML = '';
    if (pages <= 1) return;
    var prev = document.createElement('button');
    prev.innerHTML = '<i class="bi bi-chevron-left"></i>';
    prev.disabled = list._page === 0;
    prev.addEventListener('click', function () { list._page--; render(); });
    pager.appendChild(prev);
    for (var p = 0; p < pages; p++) (function (p) {
      var b = document.createElement('button');
      b.textContent = (p + 1);
      b.className = p === list._page ? 'active' : '';
      b.addEventListener('click', function () { list._page = p; render(); });
      pager.appendChild(b);
    })(p);
    var next = document.createElement('button');
    next.innerHTML = '<i class="bi bi-chevron-right"></i>';
    next.disabled = list._page === pages - 1;
    next.addEventListener('click', function () { list._page++; render(); });
    pager.appendChild(next);
  }
  list._renderPager = render;
  render();
}

// 카드 클릭 → 미리보기 업데이트 + 채팅 버튼 활성화
var selectedCardId = null;
var selectedCardType = null;

document.querySelectorAll('.bm-list').forEach(function (list) {
  list.addEventListener('click', function (e) {
    if (list.classList.contains('selecting')) return;
    if (e.target.closest('.bm-check')) return;
    var item = e.target.closest('.bm-item');
    if (!item) return;
    e.preventDefault();

    selectedCardId = item.dataset.cardId;
    selectedCardType = item.dataset.cardType;

    var img = item.querySelector('.thumb img');
    var title = item.querySelector('.bm-title');
    var desc = item.querySelector('.bm-desc');
    var pvImg = document.getElementById('pvCardImg');
    var pvTitle = document.getElementById('pvCardTitle');
    var pvDesc = document.getElementById('pvCardDesc');
    var btnChat = document.getElementById('btnStartChat');

    if (img) { pvImg.src = img.src; pvImg.style.display = ''; }
    if (title) pvTitle.textContent = title.textContent;
    if (desc) pvDesc.textContent = desc.textContent;

    btnChat.style.pointerEvents = '';
    btnChat.style.opacity = '1';
    btnChat.href = '#';
    btnChat.onclick = function(e) {
      e.preventDefault();
      if (window.CardPanel) {
        CardPanel.open(selectedCardId);
      }
    };

    // 카드 상세 클릭 시 해당 카드 페이지로 이동 (더블클릭)
    list.querySelectorAll('.bm-item').forEach(function (it) { it.classList.remove('previewing'); });
    item.classList.add('previewing');
  });
});

// bookmark cluster nodes
(function () {
  var rows = document.querySelectorAll('.cluster .node');
  var cap = document.getElementById('clusterCap');
  if (!rows.length || !cap) return;
  var LABEL = { job: '일자리', housing: '주거', education: '교육', finance: '금융', culture: '문화', welfare: '생활복지' };
  rows.forEach(function (row) {
    row.addEventListener('click', function () {
      var catKey = row.dataset.cat;
      var catName = Object.keys(CAT_MAP).find(function (k) { return CAT_MAP[k].key === catKey; });
      rows.forEach(function (r) { r.classList.toggle('active', r === row); });
      var all = allBookmarks.policy.concat(allBookmarks.news);
      var titles = all
        .filter(function (b) { return b.card.category_name === catName; })
        .map(function (b) { return b.card.card_title; });
      if (!titles.length) {
        cap.innerHTML = '<span class="cap-empty">이 분야에 담긴 카드가 없어요.</span>';
        return;
      }
      cap.innerHTML = '<div class="cap-head"><b>' + (LABEL[catKey] || '') + '</b> · ' + titles.length + '건</div>' +
        '<ul class="cap-list">' + titles.map(function (t) {
          return '<li><i class="bi bi-dot"></i>' + t + '</li>';
        }).join('') + '</ul>';
    });
  });
})();

// bookmark select & delete (API 연동)
(function () {
  var selectBtn = document.getElementById('bmSelectBtn');
  var actions = document.getElementById('bmSelectActions');
  var countEl = document.getElementById('bmSelCount');
  var allBtn = document.getElementById('bmAllBtn');
  var delBtn = document.getElementById('bmDeleteBtn');
  var cancelBtn = document.getElementById('bmCancelBtn');
  if (!selectBtn) return;
  var selecting = false;

  function visibleList() {
    return document.querySelector('.bm-list:not(.d-none)');
  }

  // 한 쪽 카드만 체크돼도 양쪽 모두 체크되는 것 없이 하기 위함
  function updateCount() {
    var n = document.querySelectorAll('.bm-list:not(.d-none) .bm-check:checked').length;
    countEl.textContent = n;
  }
  function setMode(on) {
    selecting = on;
    selectBtn.classList.toggle('d-none', on);
    actions.classList.toggle('d-none', !on);
    document.querySelectorAll('.bm-list').forEach(function (l) { l.classList.toggle('selecting', on); });
    if (!on) {
      document.querySelectorAll('.bm-check').forEach(function (c) { c.checked = false; });
      document.querySelectorAll('.bm-item').forEach(function (it) { it.classList.remove('checked'); });
    }
    updateCount();
  }
  selectBtn.addEventListener('click', function () { setMode(true); });
  cancelBtn.addEventListener('click', function () { setMode(false); });

  document.addEventListener('click', function (e) {
    if (!selecting) return;
    var item = e.target.closest('.bm-item');
    if (!item || item.closest('.bm-list').classList.contains('d-none')) return;
    var chk = item.querySelector('.bm-check');
    if (e.target !== chk) { e.preventDefault(); chk.checked = !chk.checked; }
    item.classList.toggle('checked', chk.checked);
    updateCount();
  });

  allBtn.addEventListener('click', function () {
    var list = visibleList();
    var boxes = list.querySelectorAll('.bm-item:not(.d-none) .bm-check');
    var allOn = Array.prototype.every.call(boxes, function (b) { return b.checked; });
    boxes.forEach(function (b) { b.checked = !allOn; b.closest('.bm-item').classList.toggle('checked', !allOn); });
    updateCount();
  });

  // 삭제 — API 호출
  delBtn.addEventListener('click', function () {
    var checked = document.querySelectorAll('.bm-list:not(.d-none) .bm-check:checked');
    if (!checked.length) { showAlertModal('북마크 삭제', '삭제할 카드를 선택해주세요.'); return; }
    var items = Array.prototype.map.call(checked, function (c) { return c.closest('.bm-item'); });
    showDeleteModal('북마크 삭제', checked.length + '개의 북마크를 삭제하시겠습니까?', function () {
      var token = localStorage.getItem('access_token');
      var userId = token ? getUserIdFromToken(token) : null;
      var deletePromises = items.map(function (item) {
        var cardId = item.dataset.cardId;
        return fetch('/cards/api/bookmarks/' + userId + '/cards/' + cardId + '/', {
          method: 'DELETE',
          headers: { 'Authorization': 'Bearer ' + token }
        });
      });
      Promise.all(deletePromises).then(function () {
        setMode(false);
        loadBookmarks();
      }).catch(function (e) { console.error('삭제 실패', e); });
    });
  });
})();

// 페이지 진입 시 북마크 로드
loadBookmarks();

// =====================================================================
// 프로필 조회 API 연동
// =====================================================================

// 관심사 키 → category_id 매핑 (DB 기준)
var KW_TO_CAT_ID = { job: 1, education: 2, housing: 3, finance: 4, welfare: 5, culture: 6 };
var CAT_ID_TO_KW = { 1: 'job', 2: 'education', 3: 'housing', 4: 'finance', 5: 'welfare', 6: 'culture' };

function loadProfile() {
  fetch('/member/' + USER_ID + '/', {
    headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
  })
    .then(function (res) { return res.json(); })
    .then(function (u) {
      // 닉네임
      var displayName = document.getElementById('displayName');
      var nameInput = document.querySelector('.name-input');
      if (displayName) displayName.textContent = u.nickname;
      if (nameInput) nameInput.value = u.nickname;
      localStorage.setItem('policity_nickname', u.nickname);

      // 이메일
      var emailEl = document.querySelector('.form-row-card[data-field="email"] .val');
      if (emailEl) emailEl.textContent = u.email;

      // 가입일
      var tagEl = document.querySelector('.tag');
      if (tagEl && u.created_at) {
        var d = new Date(u.created_at);
        tagEl.innerHTML = '<i class="bi bi-calendar-check"></i>회원 가입일 ' +
          d.getFullYear() + '.' + String(d.getMonth() + 1).padStart(2, '0') + '.' + String(d.getDate()).padStart(2, '0');
      }

      // 성별 토글
      if (u.gender) {
        var genderMap = { MALE: 'male', FEMALE: 'female', OTHER: 'other' };
        var gKey = genderMap[u.gender] || 'male';
        document.querySelectorAll('#genderToggle button').forEach(function (b) {
          b.classList.toggle('active', b.dataset.gender === gKey);
        });
        var avatar = document.getElementById('profileAvatar');
        if (avatar) {
          var srcMap = { male: '/static/assets/boy.png', female: '/static/assets/girl.png', other: '' };
          if (gKey === 'other') {
            avatar.style.display = 'none';
            avatar.closest('.profile-avatar').style.background = '#d4ede0';
          } else {
            avatar.style.display = '';
            avatar.closest('.profile-avatar').style.background = '';
            avatar.src = srcMap[gKey];
          }
        }
        localStorage.setItem('policity_gender', gKey);
      }
      // 데이터 채운 뒤 콘텐츠 표시 (빈/하드코딩 상태 깜빡임 방지)
      document.querySelector('.mp-content')?.classList.add('loaded');
      // 주소
      if (u.region) {
        fetch('/member/regions/' + u.region + '/', { headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN } })
          .then(function (r) { return r.json(); })
          .then(function (reg) {
            if (reg && window.updateRegionSelectors) {
              updateRegionSelectors('sidoSelect', 'sigunguSelect', reg.sido, reg.sigungu);
              localStorage.setItem('policity_sido', reg.sido);
              localStorage.setItem('policity_sigungu', reg.sigungu);
            }
          }).catch(function () { });
      }
    })
    .catch(function (e) { console.error('프로필 로드 실패', e); document.querySelector('.mp-content')?.classList.add('loaded'); });

  // 관심사 불러오기
  fetch('/member/' + USER_ID + '/interests/', {
    headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
  })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      var chips = document.querySelectorAll('.kw-chip');
      chips.forEach(function (c) { c.classList.remove('on'); });
      data.forEach(function (interest) {
        var kw = CAT_ID_TO_KW[interest.category];
        if (kw) {
          var chip = document.querySelector('.kw-chip[data-kw="' + kw + '"]');
          if (chip) chip.classList.add('on');
        }
      });
    })
    .catch(function (e) { console.error('관심사 로드 실패', e); });
}

// =====================================================================
// 변경사항 저장 버튼
// =====================================================================
var btnSave = document.getElementById('btnSave');
btnSave.addEventListener('click', function () {
  if (btnSave.disabled) return;

  // 1. 관심사 체크
  var selectedChips = document.querySelectorAll('.kw-chip.on');
  if (!selectedChips.length) {
    showAlertModal('관심사 선택', '관심사를 최소 1개 이상 선택해주세요.');
    return;
  }

  // 2. 비번 변경 입력값 확인
  var pwCurrent = document.getElementById('pwCurrent').value.trim();
  var pwNew = document.getElementById('pwNew').value.trim();
  var pwConfirm = document.getElementById('pwConfirm').value.trim();
  var pwError = document.getElementById('pwError');
  var pwSuccess = document.getElementById('pwSuccess');

  if (pwNew || pwConfirm || pwCurrent) {
    if (!pwCurrent) {
      pwError.textContent = '현재 비밀번호를 입력해주세요.';
      pwError.style.display = 'block';
      pwSuccess.style.display = 'none';
      return;
    }

    var pwFormatOk = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$/.test(pwNew);
    if (!pwFormatOk) {
      pwError.textContent = '새 비밀번호는 8~16자이며, 영문/숫자/특수문자를 포함해야 합니다.';
      pwError.style.display = 'block';
      pwSuccess.style.display = 'none';
      return;
    }

    // 추가: 기존 비밀번호와 새 비밀번호 동일
    if (pwCurrent === pwNew) {
      pwError.textContent = '기존 비밀번호와 새 비밀번호가 동일합니다.';
      pwError.style.display = 'block';
      pwSuccess.style.display = 'none';
      return;
    }

    if (pwNew !== pwConfirm) {
      pwError.textContent = '새 비밀번호가 일치하지 않습니다.';
      pwError.style.display = 'block';
      pwSuccess.style.display = 'none';
      return;
    }
  }
  pwError.style.display = 'none';

  btnSave.disabled = true;

  // 3. 닉네임/성별 저장
  // 별명 인라인 편집 중(체크 ✓ 미확정)에도 입력값을 반영 — 체크 안 눌러도 저장되게
  var nameWrap = document.getElementById('nameEdit');
  var nameInputEl = nameWrap && nameWrap.querySelector('.name-input');
  var displayNameEl = document.getElementById('displayName');
  if (nameWrap && nameWrap.classList.contains('editing') && nameInputEl) {
    var pending = nameInputEl.value.trim();
    // 간단 검증: 2~12자, 한글/영문/숫자, 공백·특수문자 불가
    if (pending.length < 2 || pending.length > 12 || /\s/.test(pending) || !/^[가-힣a-zA-Z0-9]+$/.test(pending)) {
      showAlertModal('별명 확인', '별명은 공백·특수문자 없이 2~12자(한글/영문/숫자)로 입력해주세요.');
      return;
    }
    displayNameEl.textContent = pending;
    nameWrap.classList.remove('editing', 'invalid');
    displayNameEl.classList.remove('d-none');
    nameInputEl.classList.add('d-none');
    var pi = nameWrap.querySelector('.name-pencil i');
    if (pi) pi.className = 'bi bi-pencil';
  }
  var nickname = displayNameEl.textContent.trim();
  var activeGender = document.querySelector('#genderToggle button.active');
  var genderMap = { male: 'MALE', female: 'FEMALE', other: 'OTHER' };
  var gender = activeGender ? (genderMap[activeGender.dataset.gender] || 'MALE') : 'MALE';

  var sido = document.getElementById('sidoSelect').value;
  var sigungu = document.getElementById('sigunguSelect').value;
  var allRegions = window._allRegions || [];
  var existingReg = allRegions.find(function (r) { return r.sido === sido && r.sigungu === sigungu; });

  var regionPromise = existingReg
    ? Promise.resolve(existingReg.region_id)
    : fetch('/member/regions/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + ACCESS_TOKEN },
      body: JSON.stringify({ sido: sido, sigungu: sigungu })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.region_id && window._allRegions) window._allRegions.push(data);
        return data.region_id || null;
      }).catch(function () { return null; });

  var profilePromise = regionPromise.then(function (regionId) {
    var body = { nickname: nickname, gender: gender };
    if (regionId) body.region = regionId;
    return fetch('/member/' + USER_ID + '/', {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + ACCESS_TOKEN
      },
      body: JSON.stringify(body)
    });
  });

  // 4. 비번 변경
  var pwPromise = Promise.resolve();
  if (pwCurrent && pwNew) {
    pwPromise = fetch('/member/' + USER_ID + '/password/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + ACCESS_TOKEN
      },
      body: JSON.stringify({ current_password: pwCurrent, new_password: pwNew })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          pwError.textContent = data.detail || '비밀번호 변경에 실패했습니다.';
          pwError.style.display = 'block';
          throw new Error('pw_error');
        }
        pwSuccess.textContent = '비밀번호가 변경되었습니다.';
        pwSuccess.style.display = 'block';
        document.getElementById('pwCurrent').value = '';
        document.getElementById('pwNew').value = '';
        document.getElementById('pwConfirm').value = '';
      });
    });
  }

  // 5. 관심사 저장 — 기존 전체 삭제 후 재등록
  var selectedCatIds = Array.prototype.map.call(selectedChips, function (c) {
    return KW_TO_CAT_ID[c.dataset.kw];
  }).filter(Boolean);

  var interestPromise = fetch('/member/' + USER_ID + '/interests/', {
    headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
  })
    .then(function (res) { return res.json(); })
    .then(function (existing) {
      // 기존 삭제
      var delPromises = existing.map(function (i) {
        return fetch('/member/interests/' + i.interest_id + '/', {
          method: 'DELETE',
          headers: { 'Authorization': 'Bearer ' + ACCESS_TOKEN }
        }).then(function (res) {
          if (!res.ok) throw new Error('interest_delete_failed');
          return res;
        });
      });
      return Promise.all(delPromises);
    })
    .then(function () {
      // 새로 등록
      var addPromises = selectedCatIds.map(function (catId) {
        return fetch('/member/' + USER_ID + '/interests/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + ACCESS_TOKEN
          },
          body: JSON.stringify({ user: USER_ID, category: catId })
        }).then(function (res) {
          if (!res.ok) throw new Error('interest_create_failed');
          return res;
        });
      });
      return Promise.all(addPromises);
    });

  Promise.all([profilePromise, pwPromise, interestPromise])
    .then(function () {
      localStorage.setItem('policity_nickname', nickname);
      localStorage.setItem('nickname', nickname);
      localStorage.setItem('policity_gender', activeGender ? activeGender.dataset.gender : 'male');
      var headerNick = document.getElementById('header-nickname');
      if (headerNick) headerNick.textContent = nickname + '님';
      showAlertModal('변경사항 저장', '변경사항이 저장되었습니다.');
    })
    .catch(function (e) {
      if (e.message !== 'pw_error') {
        showAlertModal('오류', '저장 중 오류가 발생했습니다. 다시 시도해주세요.');
      }
    })
    .finally(function () {
      btnSave.disabled = false;
    });
});

// =====================================================================
// 회원 탈퇴
// =====================================================================
document.querySelector('.btn-big-danger').addEventListener('click', function () {
  var pwInput = document.querySelector('[data-view="withdraw"] .poli-input');
  var agreeChk = document.getElementById('agreeWithdraw');
  var pw = pwInput ? pwInput.value.trim() : '';

  if (!pw) {
    showAlertModal('본인 확인', '현재 비밀번호를 입력해주세요.');
    return;
  }
  if (!agreeChk || !agreeChk.checked) {
    showAlertModal('탈퇴 확인', '탈퇴 동의 체크박스를 선택해주세요.');
    return;
  }

  // 1. 비밀번호 먼저 검증
  fetch('/member/' + USER_ID + '/withdraw/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + ACCESS_TOKEN
    },
    body: JSON.stringify({ current_password: pw, check_only: true })
  })
    .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
    .then(function (r) {
      if (!r.ok) {
        showAlertModal('오류', r.data.detail || '비밀번호가 일치하지 않습니다.');
        return;
      }
      // 2. 비밀번호 맞으면 탈퇴 확인 모달
      showDeleteModal('계정 탈퇴', '정말 탈퇴하시겠습니까?', function () {
        fetch('/member/' + USER_ID + '/withdraw/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + ACCESS_TOKEN
          },
          body: JSON.stringify({ current_password: pw })
        })
          .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
          .then(function (r) {
            if (!r.ok) {
              showAlertModal('오류', r.data.detail || '탈퇴 처리 중 오류가 발생했습니다.');
              return;
            }
            localStorage.clear();
            window.location.href = '/';
          })
          .catch(function (e) { console.error('탈퇴 실패', e); });
      });
    })
    .catch(function (e) { console.error('탈퇴 실패', e); });
});


function showDeleteModal(title, msg, onConfirm) {
  var modal = document.getElementById('deleteModal');
  document.getElementById('deleteModalTitle').textContent = title;
  document.getElementById('deleteModalMsg').textContent = msg;
  modal.style.display = 'flex';
  function cleanup() { modal.style.display = 'none'; document.getElementById('deleteModalConfirm').onclick = null; document.getElementById('deleteModalCancel').onclick = null; }
  document.getElementById('deleteModalConfirm').onclick = function () { cleanup(); onConfirm(); };
  document.getElementById('deleteModalCancel').onclick = cleanup;
  modal.onclick = function (e) { if (e.target === modal) cleanup(); };
}
function showAlertModal(title, msg, onOk) {
  var modal = document.getElementById('alertModal');
  document.getElementById('alertModalTitle').textContent = title;
  document.getElementById('alertModalMsg').textContent = msg;
  modal.style.display = 'flex';
  function cleanup() { modal.style.display = 'none'; document.getElementById('alertModalOk').onclick = null; if (onOk) onOk(); }
  document.getElementById('alertModalOk').onclick = cleanup;
  modal.onclick = function (e) { if (e.target === modal) cleanup(); };
}

// 페이지 진입 시 프로필 로드
loadProfile();
// 안전장치: 로드가 늦어도 콘텐츠가 계속 숨겨지지 않도록 1.2초 후 강제 표시
setTimeout(function () { document.querySelector('.mp-content')?.classList.add('loaded'); }, 1200);

document.addEventListener('DOMContentLoaded', function() {
  var btnClose = document.getElementById('detailClose');
  var overlay = document.getElementById('detailOverlay');
  
  if (btnClose) btnClose.addEventListener('click', CardPanel.close);
  if (overlay) overlay.addEventListener('click', CardPanel.close);

  // 탭 클릭 이벤트
  document.querySelectorAll('#tabs .tab').forEach(function (tab, i) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('#tabs .tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var panel = document.getElementById('tabPanel' + i);
      if (panel) { panel.style.display = ''; panel.classList.add('active'); }
    });
  });

  // 연락처 토글 이벤트
  var contactToggle = document.getElementById('contactToggle');
  if (contactToggle) {
    contactToggle.addEventListener('click', function () {
      this.classList.toggle('open');
      document.getElementById('contactList').classList.toggle('open');
    });
  }
});