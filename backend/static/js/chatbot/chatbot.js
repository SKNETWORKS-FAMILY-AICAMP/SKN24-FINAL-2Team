function getAuthToken() { return localStorage.getItem('access_token') || ''; }

// Refreshes the access token using the stored refresh token.
// Returns the new access token on success, or null if there is no
// refresh token or the refresh request itself fails (expired/invalid).
async function refreshAuthToken() {
    var refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return null;
    try {
        var res = await fetch('/api/token/refresh/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh: refreshToken }),
        });
        if (!res.ok) return null;
        var data = await res.json();
        if (!data.access) return null;
        localStorage.setItem('access_token', data.access);
        return data.access;
    } catch (_) {
        return null;
    }
}

// Wraps fetch() with automatic access-token refresh on 401.
// `buildHeaders(token)` must return the headers object for a given token,
// so the Authorization header can be rebuilt with the refreshed token on retry.
async function authFetch(url, options, buildHeaders) {
    options = options || {};
    options.headers = buildHeaders(getAuthToken());
    var res = await fetch(url, options);
    if (res.status !== 401) return res;

    var newToken = await refreshAuthToken();
    if (!newToken) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        window.location.href = '/member/login/?next=' + encodeURIComponent(window.location.pathname);
        throw new Error('session_expired');
    }
    options.headers = buildHeaders(newToken);
    return fetch(url, options);
}

function getUserId() {
    var token = getAuthToken();
    if (!token) return null;
    try {
        var payload = JSON.parse(atob(token.split('.')[1]));
        return payload.user_id || payload.sub || null;
    } catch (_) { return null; }
}

var cardIdParam = new URLSearchParams(window.location.search).get('card');
var sessionIdParam = new URLSearchParams(window.location.search).get('session');

var lastSentMessage = '';

// ---- text selection → floating copy button ----
var selCopyBtn = document.getElementById('selCopyBtn');
var quoteBar = document.getElementById('quoteBar');
var quoteBarText = document.getElementById('quoteBarText');
var quotedText = '';

document.addEventListener('mouseup', function (e) {
    if (e.target.closest('#selCopyBtn')) return;
    setTimeout(function () {
        var sel = window.getSelection();
        var text = sel ? sel.toString().trim() : '';
        if (text.length > 0) {
            var range = sel.getRangeAt(0).getBoundingClientRect();
            selCopyBtn.style.top = (range.top + window.scrollY - selCopyBtn.offsetHeight - 8) + 'px';
            selCopyBtn.style.left = (range.left + range.width / 2 - selCopyBtn.offsetWidth / 2) + 'px';
            selCopyBtn.classList.add('visible');
            selCopyBtn._pendingText = text;
        } else {
            selCopyBtn.classList.remove('visible');
        }
    }, 10);
});

document.addEventListener('mousedown', function (e) {
    if (!e.target.closest('#selCopyBtn')) {
        selCopyBtn.classList.remove('visible');
    }
});

selCopyBtn.addEventListener('click', function () {
    quotedText = selCopyBtn._pendingText || '';
    if (!quotedText) return;
    quoteBarText.textContent = quotedText;
    quoteBar.classList.add('visible');
    selCopyBtn.classList.remove('visible');
    window.getSelection().removeAllRanges();
    document.getElementById('chatInput').focus();
});

document.getElementById('quoteBarDismiss').addEventListener('click', function () {
    quotedText = '';
    quoteBarText.textContent = '';
    quoteBar.classList.remove('visible');
});

// ---- card context indicator (chip above input + badge in chat header) ----
var cardContextBar = document.getElementById('cardContextBar');
var cardContextTitle = document.getElementById('cardContextTitle');
var cardContextDismissBtn = document.getElementById('cardContextDismiss');
var chCardBadge = document.getElementById('chCardBadge');
var chCardBadgeTitle = document.getElementById('chCardBadgeTitle');
var cardContextDismissed = false;
var lastCardContextId = null;

function updateCardContextUI() {
    var col = document.getElementById('detailCol');
    var activeId = col ? col.getAttribute('data-card-id') : null;
    var isOpen = !!(col && activeId && !col.classList.contains('d-none'));

    if (activeId !== lastCardContextId) {
        cardContextDismissed = false;
        lastCardContextId = activeId;
    }

    if (isOpen && !cardContextDismissed) {
        var detailTitle = document.getElementById('detailTitle');
        var titleText = detailTitle ? (detailTitle.firstChild ? detailTitle.firstChild.textContent.trim() : '') : '';
        cardContextTitle.textContent = titleText;
        cardContextBar.classList.add('visible');
        chCardBadgeTitle.textContent = titleText;
        chCardBadge.classList.add('visible');
    } else {
        cardContextBar.classList.remove('visible');
        chCardBadge.classList.remove('visible');
        quotedText = '';
        quoteBarText.textContent = '';
        quoteBar.classList.remove('visible');
    }
}

cardContextDismissBtn.addEventListener('click', function () {
    cardContextDismissed = true;
    updateCardContextUI();
});

new MutationObserver(updateCardContextUI).observe(document.getElementById('detailCol'), {
    attributes: true,
    attributeFilter: ['class', 'data-card-id']
});
new MutationObserver(updateCardContextUI).observe(document.getElementById('detailTitle'), {
    childList: true, characterData: true, subtree: true
});


// ---- shared new-chat reset ----
async function startNewChat() {
    window.history.replaceState({}, document.title, window.location.pathname);
    cardIdParam = null;
    document.querySelectorAll('#historyList .history-item').forEach(function (x) { x.classList.remove('active'); });
    CardPanel.close();
    detailCol.classList.add('d-none');
    updateLayout();
    document.getElementById('chatBody').innerHTML = '';
    currentSessionId = null;
    isNewChat = true;
    recoCardIds = [];
    recoMessage = '';
    dailyLifeCount = 0;
    clarifyingQuestionCount = 0;
    recoGeneration++;
    await fetchRecommendations();
}

// ---- delete modal ----
function openDeleteModal(item, sessionId) {
    showGenericModal({
        icon: '🗑️',
        title: '대화 삭제',
        msg: '정말 삭제하시겠습니까?',
        buttons: [
            { label: '취소', variant: 'ghost' },
            {
                label: '삭제', variant: 'danger', onClick: async function () {
                    if (!sessionId) { if (item) item.remove(); return; }
                    try {
                        await fetch('/api/chatbot/history/' + sessionId + '/', {
                            method: 'DELETE',
                            headers: {
                                'X-CSRFToken': getCsrfToken(),
                                'Authorization': 'Bearer ' + getAuthToken()
                            },
                        });
                        if (item) item.remove();
                        startNewChat();
                    } catch (err) {
                        console.error('대화를 삭제하지 못했어요.', err);
                    }
                }
            },
        ],
    });
}


// ---- history list ----
var currentSessionId = null;
var recoGeneration = 0; // incremented whenever we navigate away from a new-chat session

function formatHistoryDate(isoString) {
    var d = new Date(isoString);
    var now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    var yest = new Date(now); yest.setDate(now.getDate() - 1);
    var hh = d.getHours(); var mm = String(d.getMinutes()).padStart(2, '0');
    var ampm = hh < 12 ? '오전' : '오후'; var h12 = hh % 12 || 12;
    var timeStr = ampm + ' ' + h12 + ':' + mm;
    if (sameDay) return '오늘 ' + timeStr;
    if (d.toDateString() === yest.toDateString()) return '어제 ' + timeStr;
    return (d.getMonth() + 1) + '월 ' + d.getDate() + '일';
}

function renderHistoryList(sessions) {
    var list = document.getElementById('historyList');
    list.innerHTML = '';
    sessions.forEach(function (s) {
        var item = document.createElement('div');
        item.className = 'history-item' + (s.chat_session_id === currentSessionId ? ' active' : '');
        item.setAttribute('data-session-id', s.chat_session_id);
        item.innerHTML =
            '<div class="history-info">' +
            '<div class="history-name"></div>' +
            '<div class="history-date">' + formatHistoryDate(s.updated_at) + '</div>' +
            '</div>' +
            '<div class="history-actions">' +
            '<button class="history-edit" title="제목 수정" aria-label="대화 제목 수정"><i class="bi bi-pencil"></i></button>' +
            '<button class="history-del" title="삭제" aria-label="대화 삭제"><i class="bi bi-trash3"></i></button>' +
            '</div>';
        item.querySelector('.history-name').textContent = s.session_title;
        list.appendChild(item);
    });
}

async function fetchHistoryList() {
    try {
        var res = await authFetch('/api/chatbot/history/', { method: 'GET' }, function (token) {
            return { 'Authorization': 'Bearer ' + token };
        });
        var data = await res.json();
        renderHistoryList(data.sessions || []);
    } catch (err) {
        console.error('대화 목록을 불러오지 못했어요.', err);
    }
}

// ---- load session messages (Plan C: context-card detection, phase-aware rendering) ----
async function loadSessionMessages(sessionId, user_id) {
    var url = '/api/chatbot/history/' + sessionId + '/';
    if (user_id) url += '?user_id=' + encodeURIComponent(user_id);
    try {
        var res = await authFetch(url, { method: 'GET' }, function (token) {
            return { 'Authorization': 'Bearer ' + token };
        });
        if (!res.ok) throw new Error('session_not_found');
        var data = await res.json();

        currentSessionId = data.chat_session_id;
        isNewChat = false;
        recoGeneration++;
        var body = document.getElementById('chatBody');
        body.innerHTML = '';

        var messages = data.messages || [];
        if (!messages.length) {
            appendBotMsg('이전 대화 내용을 불러올 수 없어요. 새로운 질문을 입력해 주세요 😊');
            return;
        }

        // Track seen card_ids to avoid repeating the same card across multiple messages.
        // Cards from the ?card= flow get saved to every bot message; only show them once.
        var seenCardIds = {};

        // Render each message
        messages.forEach(function (msg) {
            var msgCards = (msg.cards || []);

            if (!msg.input && msg.output) {
                // System reco message saved when first message was sent (input="")
                // Render welcome reco message (input="") in history
                appendHistoryRecoMsg(msg.output, msgCards);
                msgCards.forEach(function (c) { seenCardIds[c.card_id] = true; });
            } else {
                if (msg.input) appendUserMsg(msg.input);
                if (msg.output) appendBotMsg(msg.output);
                // Inline cards: skip any already shown (context cards from ?card= flow)
                var freshCards = msgCards.filter(function (c) { return !seenCardIds[c.card_id]; });
                if (freshCards.length) {
                    appendInlineCardGroup(freshCards);
                    freshCards.forEach(function (c) { seenCardIds[c.card_id] = true; });
                }
            }
        });

        body.scrollTop = body.scrollHeight;

        document.querySelectorAll('#historyList .history-item').forEach(function (x) {
            x.classList.toggle('active', x.getAttribute('data-session-id') === String(sessionId));
        });
    } catch (err) {
        console.error('대화 내용을 불러오지 못했어요.', err);
        showNotFoundModal(
            '대화를 찾을 수 없어요',
            '요청하신 대화 세션이 존재하지 않거나\n삭제된 세션이에요.\n\n새로운 대화를 시작해 주세요!'
        );
    }
}

function startRenameSession(item) {
    var nameEl = item.querySelector('.history-name');
    var currentTitle = nameEl.textContent;
    var input = document.createElement('input');
    input.type = 'text';
    input.value = currentTitle;
    input.maxLength = 100;
    input.className = 'history-rename-input';
    input.addEventListener('click', function (e) { e.stopPropagation(); });
    nameEl.replaceWith(input);
    input.focus();
    input.select();

    async function commitRename() {
        var newTitle = input.value.trim();
        var newNameEl = document.createElement('div');
        newNameEl.className = 'history-name';
        newNameEl.textContent = newTitle || currentTitle;
        input.replaceWith(newNameEl);
        if (newTitle && newTitle !== currentTitle) {
            var sessionId = item.getAttribute('data-session-id');
            try {
                await fetch('/api/chatbot/history/' + sessionId + '/', {
                    method: 'PATCH',
                    headers: {
                        'Authorization': 'Bearer ' + getAuthToken(),
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    body: JSON.stringify({ session_title: newTitle }),
                });
            } catch (err) {
                console.error('제목 수정 실패', err);
                newNameEl.textContent = currentTitle;
            }
        }
    }

    input.addEventListener('blur', commitRename);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { input.blur(); }
        if (e.key === 'Escape') { input.value = currentTitle; input.blur(); }
    });
}

document.getElementById('historyList').addEventListener('click', async function (e) {
    var editBtn = e.target.closest('.history-edit');
    var delBtn = e.target.closest('.history-del');
    var item = e.target.closest('.history-item');
    if (editBtn) {
        e.stopPropagation();
        if (item) startRenameSession(item);
        return;
    }
    if (delBtn) {
        e.stopPropagation();
        openDeleteModal(item, item ? item.getAttribute('data-session-id') : null);
        return;
    }
    if (item) {
        var sessionId = item.getAttribute('data-session-id');
        window.history.replaceState({}, document.title, window.location.pathname);
        cardIdParam = null;
        document.querySelectorAll('#historyList .history-item').forEach(function (x) { x.classList.remove('active'); });
        item.classList.add('active');
        CardPanel.close();
        detailCol.classList.add('d-none');
        updateLayout();
        await loadSessionMessages(sessionId, getUserId());
    }
});

(async function () { await fetchHistoryList(); })();

// ---- history column toggle ----
var historyCol = document.getElementById('historyCol');
var chatCol = document.getElementById('chatCol');
var detailCol = document.getElementById('detailCol');

function updateLayout() {
    var historyVisible = !historyCol.classList.contains('collapsed');
    var detailVisible = !detailCol.classList.contains('d-none');

    chatCol.classList.remove('col-lg-12', 'col-lg-10', 'col-lg-8', 'col-lg-7', 'col-lg-6');
    detailCol.classList.remove('col-lg-5', 'col-lg-4');

    if (historyVisible && !detailVisible) {
        chatCol.classList.add('col-lg-10');
    } else if (historyVisible && detailVisible) {
        chatCol.classList.add('col-lg-6');
        detailCol.classList.add('col-lg-4');
    } else if (!historyVisible && !detailVisible) {
        chatCol.classList.add('col-lg-12');
    } else {
        chatCol.classList.add('col-lg-7');
        detailCol.classList.add('col-lg-5');
    }
}

document.getElementById('historyToggleBtn').addEventListener('click', function () {
    historyCol.classList.toggle('collapsed');
    updateLayout();
});

updateLayout();

// ---- new chat button ----
var isNewChat = false;

// document.getElementById('newChatBtn').addEventListener('click', () => { window.location.href = '/chat'; });  //startNewChat
document.getElementById('newChatBtn').addEventListener('click', startNewChat);

// ---- nav dropdown ----
document.querySelectorAll('.nav-dropdown').forEach(function (dd) {
    dd.addEventListener('click', function () { dd.classList.toggle('open'); });
});

// ---- nav toggle (mobile) ----
document.querySelector('.nav-toggle').addEventListener('click', function () {
    document.querySelector('.main-nav').classList.toggle('open');
});

// ---- category select ----
document.querySelectorAll('#catList .cat').forEach(function (c) {
    c.addEventListener('click', function () {
        document.querySelectorAll('#catList .cat').forEach(function (x) { x.classList.remove('active'); });
        c.classList.add('active');
    });
});

// ---- contact list toggle ----
var contactToggleBtn = document.getElementById('contactToggle');
if (contactToggleBtn) {
    contactToggleBtn.addEventListener('click', function () {
        this.classList.toggle('open');
        document.getElementById('contactList').classList.toggle('open');
    });
}

// ---- detail tabs ----
document.querySelectorAll('#tabs .tab').forEach(function (t, i) {
    t.addEventListener('click', function () {
        document.querySelectorAll('#tabs .tab').forEach(function (x) { x.classList.remove('active'); });
        document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
        t.classList.add('active');
        var panel = document.getElementById('tabPanel' + i);
        if (panel) { panel.style.display = ''; panel.classList.add('active'); }
    });
});


// ============================================================
// CARD FUNCTIONS
// ============================================================

var RECO_CARD_STORE = {};
var recoCardIds = [];
var recoMessage = '';
var dailyLifeCount = 0;
var clarifyingQuestionCount = 0;

// Build a single .reco-item element from card data
function buildRecoItemEl(card, isSelected, isOpen, isSeen) {
    var item = document.createElement('div');
    item.className = 'reco-item' + (isSelected ? ' past-selected' : '') + (isOpen ? ' is-selected' : '');
    item.setAttribute('data-card-id', card.card_id);
    item.setAttribute('data-chat-msg-card-id', card.chat_msg_card_id || '');
    item.setAttribute('data-policy', card.card_title || '');
    item.setAttribute('data-category', card.category_id || '');
    item.setAttribute('data-type', card.type || 'policy');
    RECO_CARD_STORE[card.card_id] = card;
    var typeLabel = isSeen ? '이전' : (card.type === 'policy' ? '정책' : card.type === 'news' ? '뉴스' : (card.type || ''));
    item.innerHTML =
        '<div class="num">' + typeLabel + '</div>' +
        '<div class="sum-category m-0">' + (card.category_name || '') + '</div>' +
        '<div><div class="rt">' + (card.card_title || '') + '</div><div class="rd">' + (card.intro || '') + '</div>' +
        (isSelected ? '<div class="reco-item-selected-badge">✓ 선택됨</div>' : '') +
        '</div>';
    return item;
}

// Persist is-selected state to DB for every currently selected reco-item.
// Called at message-send time (not click time) so the DB only reflects
// selections the user actually acted on.
async function persistSelectedRecoCards() {
    var items = Array.from(document.querySelectorAll('.reco-item.is-selected'));
    await Promise.all(items.map(async function (item) {
        var chatMsgCardId = item.getAttribute('data-chat-msg-card-id');
        if (!chatMsgCardId) return;
        try {
            await fetch('/api/chatbot/cards/' + chatMsgCardId + '/select/', {
                method: 'PATCH',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'Authorization': 'Bearer ' + getAuthToken()
                },
            });
        } catch (err) {
            console.error('카드 선택을 저장하지 못했어요.', err);
        }
    }));
}

// Bind click → toggle is-selected locally, open card panel.
// DB persistence is deferred to persistSelectedRecoCards(), fired on send.
function bindRecoItem(item) {
    item.addEventListener('click', function () {
        var cardId = item.getAttribute('data-card-id');
        var card = RECO_CARD_STORE[cardId];
        if (!card) return;

        var alreadySelected = item.classList.contains('is-selected');
        var wrap = item.closest('.reco-cards-wrap');
        (wrap || document).querySelectorAll('.reco-item').forEach(function (x) { x.classList.remove('is-selected'); });

        if (alreadySelected) {
            // Clicking an already-selected card deselects it and closes the detail panel.
            cardIdParam = null;
            CardPanel.close();
            detailCol.classList.add('d-none');
            updateLayout();
            return;
        }

        item.classList.add('is-selected');
        cardIdParam = cardId;
        CardPanel.open(cardId, getAuthToken());
        detailCol.classList.remove('d-none');
        updateLayout();
    });
}

// ---- Welcome phase: bot message containing reco cards ----
// Creates #welcomeMsg — a full-width bot message with the recommendation panel inside.
function appendWelcomeMsg(message, cards, seenCards) {
    var body = document.getElementById('chatBody');
    var existing = document.getElementById('welcomeMsg');
    if (existing) existing.remove();

    // Fill to 3 using seen cards when fresh cards are fewer than 3
    var freshCards = cards || [];
    var fillCards = (seenCards || []).slice(0, Math.max(0, 3 - freshCards.length));
    var allCards = freshCards.concat(fillCards);

    var titleText = freshCards.length === 0 ? '폴리의 카드' : '폴리의 카드';
    var subtitle = '';
    if (freshCards.length === 0 && fillCards.length > 0) {
        subtitle = '지금은 새로운 추천이 없어요';
    } else if (freshCards.length > 0 && fillCards.length > 0) {
        subtitle = '새 추천 + 이전에 본 카드도 함께 보여드려요';
    }

    var msg = document.createElement('div');
    msg.className = 'msg bot welcome-msg-group';
    msg.id = 'welcomeMsg';
    msg.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;">' +
        '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
        '</div>' +
        '<div class="end welcome-end">' +
        '<div class="name">폴리</div>' +
        '<div class="bubble"></div>' +
        '<article class="panel-card reco p-1 reco-cards-panel" id="recoCardsPanel">' +
        '<div class="reco-in">' +
        // '<button class="reco-refresh-btn" id="recoRefreshBtn" type="button" title="새 카드 추천받기" aria-label="새 카드 추천받기">' +
        // '<i class="bi bi-arrow-clockwise"></i>' +
        // '</button>' +
        '<div class="reco-title"><span class="spark">✦</span>' + titleText + '<span class="spark">✦</span></div>' +
        (subtitle ? '<div class="reco-subtitle">' + subtitle + '</div>' : '') +
        '<div class="reco-cards-wrap"></div>' +
        '</div>' +
        '</article>' +
        '<div class="meta">' + getTime() + '</div>' +
        '</div>';

    msg.querySelector('.bubble').textContent = message || '';

    var wrap = msg.querySelector('.reco-cards-wrap');
    recoCardIds = [];
    allCards.forEach(function (card) {
        var isSeen = freshCards.indexOf(card) === -1;
        var item = buildRecoItemEl(card, false, isSeen);
        bindRecoItem(item);
        wrap.appendChild(item);
        recoCardIds.push(card.card_id);
        RECO_CARD_STORE[card.card_id] = card;
    });

    // msg.querySelector('.reco-refresh-btn').addEventListener('click', handleRecoRefreshClick);

    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
    return msg;
}

// Refresh button on the welcome reco panel: re-fetch a fresh batch of recommended cards.
async function handleRecoRefreshClick(e) {
    e.stopPropagation();
    var btn = e.currentTarget;
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add('spinning');
    await fetchRecommendations();
}

// Show welcome skeleton while fetchRecommendations is in flight
function showWelcomeSkeleton() {
    var body = document.getElementById('chatBody');
    var existing = document.getElementById('welcomeMsg');
    if (existing) existing.remove();

    var msg = document.createElement('div');
    msg.className = 'msg bot welcome-msg-group';
    msg.id = 'welcomeMsg';
    msg.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;">' +
        '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
        '</div>' +
        '<div class="end welcome-end">' +
        '<div class="name">폴리</div>' +
        '<div class="bubble-skeleton"><div class="sk sk-line-t"></div><div class="sk sk-line-d"></div></div>' +
        '<article class="panel-card reco p-1 reco-cards-panel">' +
        '<div class="reco-in">' +
        '<div class="reco-title"><span class="spark">✦</span>폴리의 카드<span class="spark">✦</span></div>' +
        '<div class="reco-cards-wrap">' +
        recoSkeletonHTML()// + recoSkeletonHTML() + recoSkeletonHTML() +
    '</div>' +
        '</div>' +
        '</article>' +
        '</div>';

    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
}

// Phase transition: collapse the welcome reco panel when user sends first message
function collapseWelcomeCards() {
    var panel = document.getElementById('recoCardsPanel');
    if (panel) panel.classList.add('collapsed');
}

// ---- History rendering helpers ----

// History: reco message (input="") shown expanded in history view
function appendHistoryRecoMsg(text, cards) {
    if (!cards || !cards.length) return;
    var body = document.getElementById('chatBody');

    var msg = document.createElement('div');
    msg.className = 'msg bot welcome-msg-group';
    msg.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;">' +
        '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
        '</div>' +
        '<div class="end welcome-end">' +
        '<div class="name">폴리</div>' +
        '<div class="bubble"></div>' +
        '<article class="panel-card reco p-1 reco-cards-panel">' +
        '<div class="reco-in">' +
        '<div class="reco-title"><span class="spark">✦</span>폴리의 카드<span class="spark">✦</span></div>' +
        '<div class="reco-cards-wrap"></div>' +
        '</div>' +
        '</article>' +
        '</div>';

    msg.querySelector('.bubble').textContent = text || '';
    var wrap = msg.querySelector('.reco-cards-wrap');
    cards.forEach(function (card) {
        RECO_CARD_STORE[card.card_id] = card;
        var item = buildRecoItemEl(card, !!card.is_selected);
        item.setAttribute('data-chat-msg-card-id', card.chat_msg_card_id || '');
        bindRecoItem(item);
        wrap.appendChild(item);
    });

    body.appendChild(msg);
}

// History / live mid-chat: inline card group after a bot message (expanded)
function appendInlineCardGroup(cards) {
    if (!cards || !cards.length) return;
    var body = document.getElementById('chatBody');

    var msg = document.createElement('div');
    msg.className = 'msg bot welcome-msg-group';
    msg.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;">' +
        '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
        '</div>' +
        '<div class="end welcome-end">' +
        '<div class="name">폴리</div>' +
        '<article class="panel-card reco p-1 reco-cards-panel">' +
        '<div class="reco-in">' +
        '<div class="reco-title"><span class="spark">✦</span>폴리의 카드<span class="spark">✦</span></div>' +
        '<div class="reco-cards-wrap"></div>' +
        '</div>' +
        '</article>' +
        '<div class="meta">' + getTime() + '</div>' +
        '</div>';

    var wrap = msg.querySelector('.reco-cards-wrap');
    cards.forEach(function (card) {
        RECO_CARD_STORE[card.card_id] = card;
        var item = buildRecoItemEl(card, !!card.is_selected);
        item.setAttribute('data-chat-msg-card-id', card.chat_msg_card_id || '');
        bindRecoItem(item);
        wrap.appendChild(item);
    });

    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
}

// ---- Fetch personalized recommendations (welcome phase) ----
async function fetchRecommendations() {
    showWelcomeSkeleton();
    setSendDisabled(true);
    var gen = recoGeneration;
    try {
        var res = await authFetch('/api/chatbot/recommendations/', { method: 'POST' }, function (token) {
            return {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'Authorization': 'Bearer ' + token
            };
        });
        var data = await res.json();
        if (gen !== recoGeneration) return; // user navigated away; discard stale result
        currentSessionId = null;
        isNewChat = true;
        recoMessage = data.message || '오늘의 카드 추천이야. 뭐든지 물어봐! 😊';
        appendWelcomeMsg(recoMessage, data.cards || [], data.seen_cards || []);
    } catch (err) {
        if (gen !== recoGeneration) return;
        console.error('추천 카드를 불러오지 못했어요.', err);
        var existing = document.getElementById('welcomeMsg');
        if (existing) existing.remove();
        appendBotMsg('추천 카드를 불러오지 못했어요. 다시 시도해줘! 😅');
    } finally {
        if (gen === recoGeneration) setSendDisabled(false);
    }
}

// Mid-chat recommend routing: fetch full card data and render as an inline card group.
// chatMsgCardMap: { "card_id": chat_msg_card_id } returned by backend so selections can be PATCHed.
async function appendChatRecoMsg(cardIds, chatMsgCardMap) {
    var token = getAuthToken();
    try {
        var cards = await Promise.all(cardIds.map(async function (id) {
            var headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = 'Bearer ' + token;
            var res = await fetch('/cards/api/' + id + '/', { headers: headers });
            return res.json();
        }));
        var enriched = cards
            .filter(function (c) { return c && c.card_id; })
            .map(function (c) {
                return Object.assign({}, c, {
                    chat_msg_card_id: (chatMsgCardMap && chatMsgCardMap[String(c.card_id)]) || '',
                    is_selected: false,
                });
            });
        appendInlineCardGroup(enriched);
    } catch (err) {
        console.error('추천 카드를 불러오지 못했어요.', err);
    }
}


// ---- detail close ----
document.getElementById('detailClose').addEventListener('click', function () {
    cardIdParam = null;
    document.querySelectorAll('.reco-item.is-selected').forEach(function (x) { x.classList.remove('is-selected'); });
    detailCol.classList.add('d-none');
    updateLayout();
});


// ============================================================
// CHAT MESSAGES
// ============================================================

function getTime() {
    var now = new Date();
    var hh = now.getHours(); var mm = String(now.getMinutes()).padStart(2, '0');
    var ampm = hh < 12 ? '오전' : '오후'; var h12 = hh % 12 || 12;
    return ampm + ' ' + h12 + ':' + mm;
}

function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Escapes a plain-text segment, then renders the small subset of inline Markdown
// the LLM is prompted to use (bold) so structured answers stay readable.
function renderInlineMarkdown(segment) {
    return escapeHtml(segment).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

// Renders markdown-style [text](url) links as a fixed "[링크]" hyperlink, escaping
// and Markdown-rendering everything else. Newlines are preserved as-is and rely on
// `white-space: pre-wrap` on .bubble to display as line breaks.
function renderWithLinks(rawText) {
    var html = '';
    var lastIndex = 0;
    var linkPattern = /\[[^\]]*\]\((https?:\/\/[^\s)]+)\)/g;
    var match;
    while ((match = linkPattern.exec(rawText)) !== null) {
        html += renderInlineMarkdown(rawText.slice(lastIndex, match.index));
        html += '<a href="' + escapeHtml(match[1]) + '" target="_blank" rel="noopener noreferrer">[링크]</a>';
        lastIndex = match.index + match[0].length;
    }
    html += renderInlineMarkdown(rawText.slice(lastIndex));
    return html;
}

function appendUserMsg(text, quote) {
    var body = document.getElementById('chatBody');
    var msg = document.createElement('div');
    msg.className = 'msg me';
    var _gender = (typeof localStorage !== 'undefined' && localStorage.getItem('policity_gender')) || 'male';
    var _avatarSrc = { male: '/static/assets/boy.png', female: '/static/assets/girl.png', other: '/static/assets/poli_profile.png' }[_gender] || '/static/assets/boy.png';
    msg.innerHTML = '<div class="pixframe" style="width:40px;height:40px;"><div class="imgslot"><img src="' + _avatarSrc + '" alt="프로필" style="width:100%;height:100%;object-fit:cover;object-position:top;"></div></div>'
        + '<div class="end"><div class="bubble"></div><div class="meta">' + getTime() + '</div></div>';
    var bubble = msg.querySelector('.bubble');
    if (quote) {
        var ref = document.createElement('div');
        ref.className = 'quote-ref';
        ref.textContent = quote;
        bubble.appendChild(ref);
    }
    var textNode = document.createElement('span');
    textNode.textContent = text;
    bubble.appendChild(textNode);
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
    return msg;
}

function appendBotMsg(text) {
    var body = document.getElementById('chatBody');
    var msg = document.createElement('div');
    msg.className = 'msg bot';
    msg.innerHTML = '<div class="pixframe" style="width:40px;height:40px;"><div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div></div>'
        + '<div class="end"><div class="name">폴리</div><div class="bubble"></div><div class="meta">' + getTime() + '</div></div>';
    msg.querySelector('.bubble').innerHTML = renderWithLinks(text || '');
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
}

function appendBotErrorMsg(userMsgEl) {
    if (userMsgEl && userMsgEl.parentNode) userMsgEl.parentNode.removeChild(userMsgEl);
    var body = document.getElementById('chatBody');
    var msg = document.createElement('div');
    msg.className = 'msg bot';
    msg.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;"><div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div></div>' +
        '<div class="end"><div class="name">폴리</div>' +
        '<div class="bubble error-bubble">' +
        '<span>오류가 발생했어요. 잠시 후 다시 시도해 주세요!</span>' +
        '<button class="retry-btn" type="button">다시 시도</button>' +
        '</div>' +
        '<div class="meta">' + getTime() + '</div></div>';
    msg.querySelector('.retry-btn').addEventListener('click', function () {
        msg.remove();
        var input = document.getElementById('chatInput');
        input.value = lastSentMessage;
        input.dispatchEvent(new Event('input'));
        sendMsg();
    });
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
}

var TYPING_STAGE_MESSAGES = ['질문을 분석하고 있어요', '관련 정보를 찾고 있어요', '답변을 정리하고 있어요'];
var TYPING_STAGE_DELAY_MS = 4000;
var TYPING_STAGE_INTERVAL_MS = 2500;
var typingStageTimer = null;

function showTypingIndicator() {
    var body = document.getElementById('chatBody');
    var el = document.createElement('div');
    el.className = 'typing-indicator';
    el.id = 'typingIndicator';
    el.innerHTML =
        '<div class="pixframe" style="width:40px;height:40px;">' +
        '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
        '</div>' +
        '<div class="end">' +
        '<div class="name">폴리</div>' +
        '<div class="typing-bubble">' +
        '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>' +
        '<span class="typing-stage-text" id="typingStageText"></span>' +
        '</div>' +
        '</div>';
    body.appendChild(el);
    body.scrollTop = body.scrollHeight;

    clearTimeout(typingStageTimer);
    typingStageTimer = setTimeout(function cycleStage(i) {
        i = i || 0;
        var stageEl = document.getElementById('typingStageText');
        if (!stageEl) return;
        stageEl.textContent = TYPING_STAGE_MESSAGES[i % TYPING_STAGE_MESSAGES.length];
        body.scrollTop = body.scrollHeight;
        typingStageTimer = setTimeout(cycleStage, TYPING_STAGE_INTERVAL_MS, i + 1);
    }, TYPING_STAGE_DELAY_MS);
}

function hideTypingIndicator() {
    clearTimeout(typingStageTimer);
    typingStageTimer = null;
    var el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

function setSendDisabled(disabled) {
    var btn = document.getElementById('sendBtn');
    var input = document.getElementById('chatInput');
    btn.disabled = disabled;
    input.disabled = disabled;
    if (disabled) {
        btn.setAttribute('aria-busy', 'true');
    } else {
        btn.removeAttribute('aria-busy');
        input.focus();
    }
}

function recoSkeletonHTML() {
    return '<div class="reco-skeleton">' +
        '<div class="sk sk-badge"></div>' +
        '<div class="sk sk-pill"></div>' +
        '<div class="sk-lines"><div class="sk sk-line-t"></div><div class="sk sk-line-d"></div></div>' +
        '</div>';
}

// ---- not-found modal ----
function showNotFoundModal(title, msg) {
    showGenericModal({
        icon: '🔍',
        title: title,
        msg: msg,
        buttons: [{ label: '새 대화 시작', variant: 'primary', onClick: startNewChat }],
    });
}

function getCsrfToken() {
    var name = 'csrftoken';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
        var c = cookies[i].trim();
        if (c.startsWith(name + '=')) return decodeURIComponent(c.slice(name.length + 1));
    }
    return '';
}

// ---- send message ----
async function sendMsg() {
    var input = document.getElementById('chatInput');
    var text = input.value.trim();
    if (!text) return;
    if (text.length > MAX_LEN) {
        inputError.style.display = 'inline';
        inputError.textContent = '입력은 500자 이내로 작성해 주세요.';
        ciRow.style.borderColor = 'var(--color-danger, #d94f4f)';
        input.focus();
        return;
    }
    if (!/[가-힣ㄱ-ㅎㅏ-ㅣ]/.test(text)) {
        inputError.style.display = 'inline';
        inputError.textContent = '한국어로 질문해 주세요.';
        ciRow.style.borderColor = 'var(--color-danger, #d94f4f)';
        input.focus();
        return;
    }
    inputError.style.display = 'none';
    ciRow.style.borderColor = '';
    charCounter.textContent = '0 / ' + MAX_LEN;
    charCounter.style.color = 'var(--text-placeholder)';
    input.value = '';
    input.style.height = 'auto';

    lastSentMessage = text;
    var wasNewChat = isNewChat;
    isNewChat = false;

    // Phase transition: collapse welcome cards on first send
    if (wasNewChat) collapseWelcomeCards();

    var currentQuote = quotedText;
    if (currentQuote) {
        quotedText = '';
        quoteBarText.textContent = '';
        quoteBar.classList.remove('visible');
    }

    var userMsgEl = appendUserMsg(text, currentQuote);
    showTypingIndicator();
    setSendDisabled(true);

    // Persist any reco-item selection to chat_msg_cards only now, at send time.
    await persistSelectedRecoCards();

    var messageBody = currentQuote
        ? '[인용: ' + currentQuote + ']\n' + text
        : text;

    var chatPayload = { user_query: messageBody, chat_session_id: currentSessionId, daily_life_count: dailyLifeCount, clarifying_question_count: clarifyingQuestionCount };
    var activeCardId = (document.getElementById('detailCol') || {}).getAttribute
        ? document.getElementById('detailCol').getAttribute('data-card-id')
        : null;
    if (activeCardId && !cardContextDismissed) chatPayload.card_id = activeCardId;

    if (wasNewChat && recoCardIds.length) {
        chatPayload.reco_card_ids = recoCardIds;
        chatPayload.reco_message = recoMessage;
        // Read selected reco card from DOM (user may have clicked one before sending)
        var selectedItem = document.querySelector('#welcomeMsg .reco-item.is-selected');
        if (selectedItem) chatPayload.selected_reco_card_id = selectedItem.getAttribute('data-card-id');
        recoCardIds = [];
    }

    // Streaming fetch
    (async function () {
        var res;
        try {
            res = await authFetch('/api/chatbot/stream/', {
                method: 'POST',
                body: JSON.stringify(chatPayload),
            }, function (token) {
                return {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'Authorization': 'Bearer ' + token
                };
            });
            if (!res.ok) throw new Error('server_error');
        } catch (e) {
            hideTypingIndicator();
            setSendDisabled(false);
            appendBotErrorMsg(userMsgEl);
            return;
        }

        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';
        var routing = null;
        var streamBubble = null;   // the bubble element we stream text into
        var streamText = '';      // raw accumulated text for streamBubble
        var recommendations = null;
        var pendingRecoCardIds = null;  // buffered until done event provides chat_msg_card_map


        function getOrCreateStreamBubble() {
            if (streamBubble) return streamBubble;
            hideTypingIndicator();
            var body = document.getElementById('chatBody');
            var msg = document.createElement('div');
            msg.className = 'msg bot';
            msg.innerHTML =
                '<div class="pixframe" style="width:40px;height:40px;"><div class="imgslot">' +
                '<img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;">' +
                '</div></div>' +
                '<div class="end"><div class="name">폴리</div><div class="bubble"></div>' +
                '<div class="meta">' + getTime() + '</div></div>';
            body.appendChild(msg);
            streamBubble = msg.querySelector('.bubble');
            streamText = '';
            return streamBubble;
        }

        try {
            while (true) {
                var _ref = await reader.read();
                var done = _ref.done, value = _ref.value;
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                var lines = buffer.split('\n');
                buffer = lines.pop(); // keep incomplete line

                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (!line.startsWith('data: ')) continue;
                    var event;
                    try { event = JSON.parse(line.slice(6)); } catch (e) { continue; }

                    if (event.type === 'meta') {
                        routing = event.routing;
                        if (event.chat_session_id) currentSessionId = event.chat_session_id;

                    } else if (event.type === 'chunk') {
                        var bubble = getOrCreateStreamBubble();
                        streamText += event.text;
                        bubble.innerHTML = renderWithLinks(streamText);
                        var chatBody = document.getElementById('chatBody');
                        chatBody.scrollTop = chatBody.scrollHeight;

                    } else if (event.type === 'recommend') {
                        recommendations = event.recommendations;
                        pendingRecoCardIds = event.recommendations;
                        hideTypingIndicator();
                        appendBotMsg(event.message || '관련된 카드들을 추천해드릴게요!');

                    } else if (event.type === 'done') {
                        if (event.chat_session_id) currentSessionId = event.chat_session_id;
                        if (event.daily_life_count != null) dailyLifeCount = event.daily_life_count;
                        if (event.clarifying_question_count != null) clarifyingQuestionCount = event.clarifying_question_count;
                        // Backfill chat_msg_card_id on welcome reco items so later clicks PATCH correctly
                        if (event.reco_card_map) {
                            document.querySelectorAll('#welcomeMsg .reco-item').forEach(function (el) {
                                var cid = el.getAttribute('data-card-id');
                                var cmcId = event.reco_card_map[String(cid)];
                                if (cmcId) el.setAttribute('data-chat-msg-card-id', cmcId);
                            });
                            // If a card was already selected before chat_msg_card_id existed, PATCH it now
                            var preSelected = document.querySelector('#welcomeMsg .reco-item.is-selected');
                            if (preSelected) {
                                var cmcId = preSelected.getAttribute('data-chat-msg-card-id');
                                if (cmcId) {
                                    fetch('/api/chatbot/cards/' + cmcId + '/select/', {
                                        method: 'PATCH',
                                        headers: {
                                            'X-CSRFToken': getCsrfToken(),
                                            'Authorization': 'Bearer ' + getAuthToken()
                                        },
                                    }).catch(function (err) { console.error('카드 선택을 저장하지 못했어요.', err); });
                                }
                            }
                        }
                        // Render mid-chat reco cards now that chat_msg_card_map is available
                        if (pendingRecoCardIds) {
                            await appendChatRecoMsg(pendingRecoCardIds, event.chat_msg_card_map || {});
                            pendingRecoCardIds = null;
                        }
                        setSendDisabled(false);
                        if (wasNewChat) await fetchHistoryList();

                    } else if (event.type === 'error') {
                        hideTypingIndicator();
                        setSendDisabled(false);
                        var foulCount = event.foul_count;
                        if (event.error === 'profanity_detected') {
                            if (userMsgEl && userMsgEl.parentNode) userMsgEl.parentNode.removeChild(userMsgEl);
                            showProfanityModal(foulCount);
                        } else if (event.error === 'biased_query') {
                            appendBotMsg(event.message || '특정 정당·후보·정책을 지지하거나 비방하는 답변하지 않습니다.');
                        } else {
                            appendBotErrorMsg(userMsgEl);
                        }
                    }
                }
            }
        } catch (e) {
            hideTypingIndicator();
            setSendDisabled(false);
            appendBotErrorMsg(userMsgEl);
        }
    })();
}

// ---- char counter & inline error ----
var chatInput = document.getElementById('chatInput');
var charCounter = document.getElementById('charCounter');
var inputError = document.getElementById('inputError');
var ciRow = document.getElementById('ciRow');
var MAX_LEN = 500;

function autoResizeChatInput() {
    chatInput.style.height = 'auto';
    chatInput.style.height = chatInput.scrollHeight + 'px';
}

chatInput.addEventListener('input', function () {
    autoResizeChatInput();
    var len = chatInput.value.length;
    charCounter.textContent = len + ' / ' + MAX_LEN;
    var overLimit = len >= MAX_LEN;
    charCounter.style.color = overLimit ? 'var(--color-danger, #d94f4f)' : 'var(--text-placeholder)';
    if (!overLimit) {
        inputError.style.display = 'none';
        ciRow.style.borderColor = '';
    }
});

document.getElementById('sendBtn').addEventListener('click', sendMsg);
document.getElementById('chatInput').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendMsg();
    }
});


// ============================================================
// INIT — determine entry mode and render accordingly
// ============================================================

(async function () {
    if (sessionIdParam) {
        // ?session= param: load an existing session directly (e.g. from notification link)
        await loadSessionMessages(sessionIdParam, getUserId());

    } else if (cardIdParam) {
        // ?card= param: user arrived from a card page wanting to chat about a specific card.
        // Show the card in a recoCardsPanel (pre-selected), open the detail panel.
        isNewChat = true;
        currentSessionId = null;

        fetch('/cards/api/' + cardIdParam + '/', {
            headers: (function () {
                var h = { 'Content-Type': 'application/json' };
                var token = getAuthToken();
                if (token) h['Authorization'] = 'Bearer ' + token;
                return h;
            })()
        })
            .then(function (res) {
                if (!res.ok) throw new Error('card_not_found');
                return res.json();
            })
            .then(function (card) {
                RECO_CARD_STORE[card.card_id] = card;

                // Build recoCardsPanel with the single card pre-selected
                var body = document.getElementById('chatBody');
                var existing = document.getElementById('welcomeMsg');
                if (existing) existing.remove();

                var msg = document.createElement('div');
                msg.className = 'msg bot welcome-msg-group';
                msg.id = 'welcomeMsg';
                msg.innerHTML =
                    '<div class="pixframe" style="width:40px;height:40px;">' +
                    '<div class="imgslot"><img src="/static/assets/poli_profile.png" alt="폴리" style="width:100%;height:100%;object-fit:cover;"></div>' +
                    '</div>' +
                    '<div class="end welcome-end">' +
                    '<div class="name">폴리</div>' +
                    '<div class="bubble"></div>' +
                    '<article class="panel-card reco p-1 reco-cards-panel" id="recoCardsPanel">' +
                    '<div class="reco-in">' +
                    '<div class="reco-title"><span class="spark">✦</span>선택된 카드<span class="spark">✦</span></div>' +
                    '<div class="reco-cards-wrap"></div>' +
                    '</div>' +
                    '</article>' +
                    '<div class="meta">' + getTime() + '</div>' +
                    '</div>';

                var ctxMessage = '이 카드에 대해 궁금한 점을 물어보세요 😊';
                msg.querySelector('.bubble').textContent = ctxMessage;

                var wrap = msg.querySelector('.reco-cards-wrap');
                var item = buildRecoItemEl(card, false, true); // pre-selected
                bindRecoItem(item);
                wrap.appendChild(item);

                body.appendChild(msg);
                body.scrollTop = body.scrollHeight;

                // Wire up recoCardIds so sendMsg saves this as a reco message on first send
                recoCardIds = [card.card_id];
                recoMessage = ctxMessage;

                CardPanel.open(cardIdParam, getAuthToken());
                detailCol.classList.remove('d-none');
                updateLayout();
            })
            .catch(function (err) {
                console.error('카드 정보를 불러오지 못했어요.', err);
                showNotFoundModal(
                    '카드를 찾을 수 없어요',
                    '요청하신 카드가 존재하지 않거나\n더 이상 제공되지 않는 카드예요.\n\n새로운 대화를 시작해 주세요!'
                );
            });

    } else {
        // Fresh new chat: welcome phase — fetch personalized recommendations
        await fetchRecommendations();
    }
})();
