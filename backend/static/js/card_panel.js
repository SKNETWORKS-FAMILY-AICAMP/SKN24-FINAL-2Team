/* PoliCity — card_panel.js
   카드 상세 패널 공통 로직 (card_list.html, bus_detail.html 공용)
   card.html 컴포넌트의 ID 체계 기준:
     #detailCol, #detailClose, #detailHeroImg, #detailTitle, #detailBookmark
     #cardPanelTitleText, #debateShortcutBtn
     #tabs, #tabPanel0~4, #perspectivesList, #contactList
*/
(function () {

  var CATEGORY_IMAGES = {
    '일자리': '/static/assets/category_cards/일자리.png',
    '교육': '/static/assets/category_cards/교육.png',
    '주거': '/static/assets/category_cards/주거.png',
    '금융': '/static/assets/category_cards/금융.png',
    '생활복지': '/static/assets/category_cards/생활복지.png',
    '문화': '/static/assets/category_cards/문화.png',
  };

  var STANCE_KEYWORDS = {
    positive: ['긍정', '기대', '평가', '성과', '기여', '혜택', '지원', '강조', '제공', '유연성', '실효성'],
    negative: ['비판', '경고', '의문', '문제', '반복', '해지율', '부진', '빈번', '부족', '감액', '폐지']
  };

  function detectStanceBadge(text) {
    var pos = 0, neg = 0;
    STANCE_KEYWORDS.positive.forEach(function (k) { if (text.includes(k)) pos++; });
    STANCE_KEYWORDS.negative.forEach(function (k) { if (text.includes(k)) neg++; });
    if (neg > pos) return { label: '비판적', color: '#d94f4f', bg: '#fff0f0' };
    if (pos > neg) return { label: '긍정적', color: '#2a9d5c', bg: '#f0faf5' };
    return { label: '중립적', color: '#888', bg: '#f5f5f5' };
  }

  /* ── 탭 0: 요약 ── */
  function renderSummary(summary, type) {
    var p0 = document.getElementById('tabPanel0');
    if (!p0 || !summary) return;

    if (typeof summary === 'string') {
      var pointsEl = p0.querySelector('.sum-points');
      if (pointsEl) {
        pointsEl.innerHTML = summary.split('\n').filter(function (s) { return s.trim(); }).map(function (t, i) {
          return '<div class="sum-point"><div class="sum-point-dot">' + (i + 1) + '</div><span>' + t.trim() + '</span></div>';
        }).join('');
      }
      var detailGrid = p0.querySelector('.sum-detail-grid');
      if (detailGrid) detailGrid.style.display = 'none';
      return;
    }

    var pointsEl = p0.querySelector('.sum-points');
    if (pointsEl && summary.summary_points) {
      pointsEl.innerHTML = summary.summary_points.map(function (text, i) {
        return '<div class="sum-point">'
          + '<div class="sum-point-dot">' + (i + 1) + '</div>'
          + '<span>' + text + '</span>'
          + '</div>';
      }).join('');
    }

    var youthEl = p0.querySelector('.sum-youth-text');
    if (youthEl) youthEl.textContent = summary.youth_connection || '';

    var detailGrid = p0.querySelector('.sum-detail-grid');
    if (type === 'policy' && summary.policy_details) {
      if (detailGrid) detailGrid.style.display = '';
      var pd = summary.policy_details;
      var values = p0.querySelectorAll('.sum-detail-value');
      var fields = [pd.target, pd.content, pd.period, pd.method];
      values.forEach(function (el, i) { if (fields[i] != null) el.textContent = fields[i]; });

      var contactList = document.getElementById('contactList');
      if (contactList && pd.contact) {
        contactList.innerHTML = '<div class="sum-contact-item">📞 ' + pd.contact + '</div>';
      }
    } else {
      if (detailGrid) detailGrid.style.display = 'none';
    }
  }

  /* ── 탭 1: 핵심 내용 ── */
  function renderCoreContent(coreContent) {
    var p1 = document.getElementById('tabPanel1');
    if (!p1 || !coreContent) return;

    var infoList = p1.querySelector('.info-list');
    if (!infoList) return;

    var text = coreContent.replace(/\\n/g, '\n');
    text = text.trim().replace(/^"|"$/g, '');

    var sections = text.split(/\n(?=■)/);

    if (Array.isArray(coreContent)) {
      infoList.innerHTML = coreContent.map(function (item) {
        return '<div class="info"><div class="ii">📌</div>'
          + '<div><div class="iv">' + item + '</div></div></div>';
      }).join('');
      return;
    }

    if (sections.length === 1 && !text.includes('■')) {
      infoList.innerHTML = '<div class="info"><div class="ii">📋</div>'
        + '<div><div class="iv">' + text.replace(/\n/g, '<br>') + '</div></div></div>';
      return;
    }

    var iconMap = {
      '이슈 배경': '💡', '무슨 일': '📰',
      '정책이 왜': '💡', '정책 내용': '📋',
      '관련 기관': '🏛️', '언론사': '📰', '청년': '🌱'
    };

    infoList.innerHTML = sections.filter(function(s) { return s.trim(); }).map(function(section) {
        var firstNewline = section.replace(/^\s*■\s*/, '').indexOf('\n');
        var title = section.replace(/^\s*■\s*/, '').slice(0, firstNewline).trim();
        var bodyRaw = section.replace(/^\s*■\s*/, '').slice(firstNewline + 1).trim();
    
        var body = bodyRaw
            .split(/\n\n+/)
            .filter(function(p) { return p.trim(); })
            .map(function(p) {
                return '<p style="margin-bottom:14px;margin-top:0;line-height:1.75;">'
                    + p.trim().replace(/\n/g, ' ')
                    + '</p>';
            }).join('');
          
        var icon = '📌';
        Object.keys(iconMap).forEach(function(k) { if (title.includes(k)) icon = iconMap[k]; });
        return '<div class="info"><div class="ii">' + icon + '</div>'
            + '<div><div class="il">' + title + '</div>'
            + '<div class="iv">' + body + '</div></div></div>';
    }).join('');
  }

  /* ── 탭 2: 찬반의견 (정책 카드) ── */
  function renderPerspectivesPolicy(perspectives) {
    var p2 = document.getElementById('tabPanel2');
    if (!p2 || !perspectives) return;
    var list = p2.querySelector('.debate-list');
    if (!list) return;

    if (!Array.isArray(perspectives) && typeof perspectives === 'object') {
      list.innerHTML = '';
      if (perspectives.pro) {
        list.innerHTML += '<div class="debate-card for">'
          + '<div class="debate-card-head"><span class="debate-badge">✔ 찬성</span></div>'
          + '<div class="debate-card-body">' + perspectives.pro + '</div>'
          + '</div>';
      }
      if (perspectives.con) {
        list.innerHTML += '<div class="debate-card against">'
          + '<div class="debate-card-head"><span class="debate-badge">✖ 반대</span></div>'
          + '<div class="debate-card-body">' + perspectives.con + '</div>'
          + '</div>';
      }
      return;
    }

    if (!Array.isArray(perspectives)) return;
    list.innerHTML = perspectives.map(function (o) {
      var isPro = o.stance === '찬성';
      return '<div class="debate-card ' + (isPro ? 'for' : 'against') + '">'
        + '<div class="debate-card-head">'
        + '<span class="debate-badge">' + (isPro ? '✔ 찬성' : '✖ 반대') + '</span>'
        + '</div>'
        + '<div class="debate-card-body">' + o.argument + '</div>'
        + '</div>';
    }).join('');
  }

  /* ── 탭 3: 언론사별 시각 (뉴스 카드) ── */
  function renderPerspectivesNews(perspectives, sources) {
    var list = document.getElementById('perspectivesList');
    if (!list || !perspectives) return;

    var pressToUrl = {};
    (sources || []).forEach(function (s) {
      if (s.press) pressToUrl[s.press] = s.source_url;
    });

    list.innerHTML = perspectives.map(function (item) {
      var badge = detectStanceBadge(item.stance);
      var url   = pressToUrl[item.media];
      var mediaEl = url
        ? '<a href="' + url + '" target="_blank" rel="noopener" '
          + 'style="color:var(--color-primary);font-weight:700;text-decoration:underline;">'
          + item.media + '</a>'
        : '<span>' + item.media + '</span>';

      return '<div class="info" style="align-items:flex-start;">'
        + '<div class="ii">📰</div>'
        + '<div style="flex:1;min-width:0;">'
        + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
        + '<div class="il" style="margin:0;">' + mediaEl + '</div>'
        + '<span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;'
        + 'color:' + badge.color + ';background:' + badge.bg + ';">' + badge.label + '</span>'
        + '</div>'
        + '<div class="iv" style="line-height:1.65;">' + item.stance + '</div>'
        + '</div></div>';
    }).join('');
  }

  /* ── 탭 4: 출처 정보 ── */
  function renderSources(sources, createdAt) {
    var p4 = document.getElementById('tabPanel4');
    if (!p4) return;
    var infoList = p4.querySelector('.info-list');
    if (!infoList) return;

    var sourceLinks = (sources || []).map(function (s) {
      var displayText = s.title ? s.title : (s.press ? '[' + s.press + '] 관련 기사 원문 보기' : '관련 원문 링크 바로가기');

      return '<a href="' + s.source_url + '" target="_blank" rel="noopener" '
        + 'style="color:var(--color-primary); font-weight: 600; text-decoration: underline; text-underline-offset: 4px; display: block; margin-bottom: 8px;">'
        + displayText + '</a>'
        // URL은 부가 정보로 작고 옅게 표시 (선택 사항)
        + '<div style="font-size: 12px; color: var(--text-muted); word-break: break-all; margin-bottom: 16px;">'
        + s.source_url + '</div>';
    }).join('');

    infoList.innerHTML =
      '<div class="info"><div class="ii">🔗</div>'
      + '<div style="width: 100%;"><div class="il">원문 출처</div>'
      + '<div class="iv" style="margin-top: 8px;">' + (sourceLinks || '출처 정보 없음') + '</div></div></div>'
      + '<div class="info"><div class="ii">📅</div>'
      + '<div><div class="il">정보 기준일</div>'
      + '<div class="iv">' + (createdAt ? createdAt.slice(0, 10) : '') + ' 기준</div></div></div>';
  }

  /* ── 패널 전체 렌더링 ── */
  function renderPanel(card) {
    var isNews = card.type === 'news';

    var titleText = document.getElementById('cardPanelTitleText');
    if (titleText) titleText.textContent = isNews ? '뉴스 카드' : '정책 카드';

    var heroImg = document.getElementById('detailHeroImg');
    if (heroImg && CATEGORY_IMAGES[card.category_name]) {
      heroImg.src = CATEGORY_IMAGES[card.category_name];
    }

    var detailTitle = document.getElementById('detailTitle');
    if (detailTitle) detailTitle.firstChild.textContent = card.card_title + ' ';

    var  detailSub = document.getElementById('detailSub');
    if (detailSub) detailSub.textContent = card.intro || '';

    var sumCat = document.getElementById('detailCategory');
    if (sumCat) sumCat.textContent = '🏷️ ' + card.category_name;

    // 💬 채팅하기 버튼 링크
    var chatLink = document.getElementById('chatAboutLink');
    if (chatLink) chatLink.href = '/chat/?card=' + card.card_id;

    // 🗣️ 토론하기 버튼 링크 및 툴팁 파싱 로직
    var debateLink = document.getElementById('debateShortcutLink');
    if (debateLink) {
      debateLink.href = '/debate/?card_id=' + card.card_id;

      var tooltipBox = debateLink.querySelector('.debate-tooltip');
      if (tooltipBox) {
        if (card.debate_topic) {
          // 정규식: 대소문자 구분 없이 'vs'를 기준으로 양옆 공백까지 자름
          var parts = card.debate_topic.split(/\s*vs\.?\s*/i);
          var sides = tooltipBox.querySelectorAll('.tooltip-side');
          var vsText = tooltipBox.querySelector('.tooltip-vs');

          if (sides.length >= 2) {
            sides[0].textContent = parts[0] ? parts[0].trim() : '';
            sides[1].textContent = parts[1] ? parts[1].trim() : '';
          }

          // 만약 데이터에 vs가 없어서 분할이 안 됐다면 중간의 'VS' 글자 숨김
          if (vsText) {
            vsText.style.display = parts.length > 1 ? 'block' : 'none';
          }
          tooltipBox.style.display = ''; // 툴팁 활성화
        } else {
          // debate_topic 데이터가 없으면 빈 박스가 뜨지 않도록 숨김 처리
          tooltipBox.style.display = 'none';
        }
      }
    }

    var newsTab = document.querySelector('#tabs .news-tab');
    var debateTab = document.querySelector('#tabs .tab:nth-child(3)');
    var newsPanel = document.getElementById('tabPanel3');
    if (newsTab) newsTab.style.display = isNews ? '' : 'none';
    if (debateTab) debateTab.style.display = isNews ? 'none' : '';
    if (newsPanel) newsPanel.style.display = 'none';

    document.querySelectorAll('#tabs .tab').forEach(function (t) { t.classList.remove('active'); });
    document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });

    var firstTab = document.querySelector('#tabs .tab:first-child');
    var firstPanel = document.getElementById('tabPanel0');
    if (firstTab) firstTab.classList.add('active');
    if (firstPanel) firstPanel.classList.add('active');

    renderSummary(card.summary, card.type);
    renderCoreContent(card.core_content);
    if (isNews) {
      renderPerspectivesNews(card.perspectives, card.sources);
    } else {
      renderPerspectivesPolicy(card.perspectives);
    }
    renderSources(card.sources, card.created_at);
  }

  /* ── JWT에서 user_id 추출 ── */
  function getUserIdFromToken(token) {
    try {
      var payload = JSON.parse(atob(token.split('.')[1]));
      return payload.user_id || payload.sub || null;
    } catch (e) { return null; }
  }

  /* ── 북마크 버튼 초기화 ── */
  function initBookmarkBtn(cardId, isBookmarked) {
    var btn = document.getElementById('detailBookmark');
    if (!btn) return;

    var newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn._bookmarked = !!isBookmarked;

    function setBookmarked(on) {
      var icon = newBtn.querySelector('i');
      if (on) {
        icon.className = 'bi bi-bookmark-fill';
        icon.style.color = '#2a9d5c';
      } else {
        icon.className = 'bi bi-bookmark';
        icon.style.color = '';
      }
      newBtn._bookmarked = on;
      var thumbBtn = document.querySelector('.card-bkmk[data-card-id="' + cardId + '"]');
      if (thumbBtn) {
        if (on) {
          thumbBtn.classList.add('on');
          thumbBtn.querySelector('i').className = 'bi bi-bookmark-fill';
        } else {
          thumbBtn.classList.remove('on');
          thumbBtn.querySelector('i').className = 'bi bi-bookmark';
        }
      }
      if (typeof window.loadBookmarks === 'function') {
        window.loadBookmarks();
      }
    }

    setBookmarked(!!isBookmarked);

    newBtn.addEventListener('click', function () {
      var token = localStorage.getItem('access_token');
      var userId = token ? getUserIdFromToken(token) : null;
      if (!userId) return;

      var headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token
      };
      var csrfEl = document.cookie.split(';').find(function (c) { return c.trim().startsWith('csrftoken='); });
      if (csrfEl) headers['X-CSRFToken'] = decodeURIComponent(csrfEl.trim().slice('csrftoken='.length));

      if (!newBtn._bookmarked) {
        fetch('/cards/api/bookmarks/' + userId + '/', {
          method: 'POST',
          headers: headers,
          body: JSON.stringify({ user: userId, card: parseInt(cardId, 10) || cardId })
        })
          .then(function (res) { if (res.ok || res.status === 409) setBookmarked(true); })
          .catch(function (err) { console.error('북마크 추가 실패', err); });
      } else {
        fetch('/cards/api/bookmarks/' + userId + '/cards/' + cardId + '/', {
          method: 'DELETE',
          headers: headers
        })
          .then(function (res) { if (res.ok || res.status === 404) setBookmarked(false); })
          .catch(function (err) { console.error('북마크 삭제 실패', err); });
      }
    });
  }

  /* ── API 호출 + 패널 열기 ── */
  function openCardPanel(cardId) {
    fetch('/cards/api/' + cardId + '/', {
      headers: (function () {
        var h = { 'Content-Type': 'application/json' };
        var token = localStorage.getItem('access_token');
        if (token) h['Authorization'] = 'Bearer ' + token;
        return h;
      })()
    })
      .then(function (res) {
        if (!res.ok) throw new Error('카드 정보를 불러오지 못했습니다.');
        return res.json();
      })
      .then(function (card) {
        renderPanel(card);
        initBookmarkBtn(card.card_id, card.is_bookmarked);
        var col = document.getElementById('detailCol');
        if (col) {
          col.classList.remove('d-none');
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              col.classList.add('panel-open');
            });
          });
        }
        var overlay = document.getElementById('detailOverlay');
        if (overlay) overlay.classList.add('open');
      })
      .catch(function (err) {
        console.error(err);
        alert('카드 정보를 불러오지 못했습니다.');
      });
  }

  /* ── 패널 닫기 ── */
  function closeCardPanel() {
    var col = document.getElementById('detailCol');
    if (col) {
      col.classList.remove('panel-open');
      col.addEventListener('transitionend', function handler() {
        col.classList.add('d-none');
        col.removeEventListener('transitionend', handler);
      });
    }
    var overlay = document.getElementById('detailOverlay');
    if (overlay) overlay.classList.remove('open');
  }

  /* ── 썸네일 북마크 토글 ── */
  function toggleBookmark(cardId, btn) {
    if (btn._pending) return; 
    var token = localStorage.getItem('access_token');
    var userId = token ? getUserIdFromToken(token) : null;
    if (!userId) return;

    var isOn = btn.classList.contains('on');
    btn._pending = true;
    var headers = {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + token
    };
    var csrfEl = document.cookie.split(';').find(function (c) { return c.trim().startsWith('csrftoken='); });
    if (csrfEl) headers['X-CSRFToken'] = decodeURIComponent(csrfEl.trim().slice('csrftoken='.length));

    if (!isOn) {
      fetch('/cards/api/bookmarks/' + userId + '/', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({ user: userId, card: parseInt(cardId, 10) || cardId })
      }).then(function (res) {
        if (res.ok || res.status === 409) {
          btn.classList.add('on');
          btn.querySelector('i').className = 'bi bi-bookmark-fill';
        }
      }).catch(function (err) { console.error('북마크 추가 실패', err); })
        .finally(function () { btn._pending = false; });
    } else {
      fetch('/cards/api/bookmarks/' + userId + '/cards/' + cardId + '/', {
        method: 'DELETE',
        headers: headers
      }).then(function (res) {
        if (res.ok || res.status === 404) {
          btn.classList.remove('on');
          btn.querySelector('i').className = 'bi bi-bookmark';
        }
      }).catch(function (err) { console.error('북마크 삭제 실패', err); })
        .finally(function () { btn._pending = false; });
    }
  }

  /* ── 패널 드래그 리사이즈 ── */
  (function initResizable() {
    var MIN_WIDTH = 500;
    var MAX_WIDTH = Math.min(1200, window.innerWidth - 80);

    var handle = document.createElement('div');
    handle.id = 'panelResizeHandle';
    handle.style.cssText = [
      'position:fixed',
      'top:0',
      'bottom:0',
      'width:6px',
      'cursor:ew-resize',
      'z-index:1051',
      'background:transparent',
      'transition:background .15s',
    ].join(';');
    handle.addEventListener('mouseenter', function () {
      handle.style.background = 'rgba(0,0,0,.08)';
    });
    handle.addEventListener('mouseleave', function () {
      if (!isDragging) handle.style.background = 'transparent';
    });
    document.body.appendChild(handle);

    var isDragging = false;
    var startX     = 0;
    var startWidth = 0;

    function getCol() { return document.getElementById('detailCol'); }

    function positionHandle() {
      var col = getCol();
      if (!col || !col.classList.contains('panel-open')) {
        handle.style.display = 'none';
        return;
      }
      var rect = col.getBoundingClientRect();
      handle.style.display = 'block';
      handle.style.left    = rect.left + 'px';
    }

    handle.addEventListener('mousedown', function (e) {
      var col = getCol();
      if (!col) return;
      isDragging = true;
      startX     = e.clientX;
      startWidth = col.offsetWidth;
      handle.style.background = 'rgba(0,0,0,.15)';
      document.body.style.userSelect  = 'none';
      document.body.style.cursor      = 'ew-resize';
      e.preventDefault();
    });

    document.addEventListener('mousemove', function (e) {
      if (!isDragging) return;
      var col = getCol();
      if (!col) return;
      MAX_WIDTH = Math.min(1200, window.innerWidth - 80);
      var newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth - (e.clientX - startX)));
      col.style.width = newWidth + 'px';
      positionHandle();
    });

    document.addEventListener('mouseup', function () {
      if (!isDragging) return;
      isDragging = false;
      handle.style.background    = 'transparent';
      document.body.style.userSelect = '';
      document.body.style.cursor     = '';
      var col = getCol();
      if (col) localStorage.setItem('cardPanelWidth', col.offsetWidth);
    });

    var savedWidth = parseInt(localStorage.getItem('cardPanelWidth'), 10);
    if (savedWidth && savedWidth >= MIN_WIDTH) {
      var col = getCol();
      if (col) col.style.width = savedWidth + 'px';
    }

    var col0 = document.getElementById('detailCol');
    if (col0) {
      col0.addEventListener('transitionend', positionHandle);
    }

    window.addEventListener('resize', positionHandle);
  })();

  /* ── 전역 공개 ── */
  window.CardPanel = {
    open: openCardPanel,
    close: closeCardPanel,
    toggleBookmark: toggleBookmark,
  };

})();