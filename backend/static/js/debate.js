/* ── Mock Mode ─────────────────────────────────────────────── */
const MOCK_MODE = false;   // ai_agent 서버 없이 UI 확인용. 연동 시 false로 변경

/* ── 진단 로그 ─────────────────────────────────────────────────
   브라우저 콘솔에서 토론 이벤트/렌더 순서를 추적하기 위한 임시 로그.
   끄려면 _DBG=false 또는 콘솔에서 window._DBG=false. */
let _DBG = true;
let _dbgT0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
function _dbg(...a) {
  if (!_DBG) return;
  try {
    const now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    const t = '+' + ((now - _dbgT0) / 1000).toFixed(2) + 's';
    console.log('%c[DEBATE ' + t + ']', 'color:#0a7', ...a);
  } catch (_) { }
}

// 0620 수정
/* ── 성별에 따른 유저 캐릭터 이미지 교체 ───────────────────────── */
(async function _applyGenderChar() {
  try {
    const token = localStorage.getItem('access_token');
    if (!token) return;
    const res = await fetch('/member/me/', { headers: { 'Authorization': 'Bearer ' + token } });
    if (!res.ok) return;
    const { gender } = await res.json();
    // OTHER → 랜덤, FEMALE → 여성 캐릭터
    const isFemale = gender === 'FEMALE' || (gender === 'OTHER' && Math.random() < 0.5);
    if (isFemale) {
      window._DEBATE_IMG.meo = window._DEBATE_IMG.meoF;
      window._DEBATE_IMG.mex = window._DEBATE_IMG.mexF;
      window._DEBATE_IMG.avMeo = window._DEBATE_IMG.avMeoF;
      window._DEBATE_IMG.avMex = window._DEBATE_IMG.avMexF;
    }
  } catch (_) { }
})();

/* ── Card info 초기 로딩 ─────────────────────────────────────── */
let _resolvedCardId = null;  // startDebate()에서 card_id 참조용 (readonly URL 대응)

(async function _initCardInfo() {
  const params = new URLSearchParams(window.location.search);
  let cardId = params.get('card_id');
  const sessionId = params.get('session');

  // session만 있는 경우(이어하기/다시보기): 세션 상세로 card 획득
  // 이어하기 때 들어오는 데이터가 어떤 것인지를 확인할 것 -> 전후에 따라 프론트가 이슈인지 백이 이슈인지 파악할 수 있음...
  // DB에 데이터 잘 저장되어있는지?
  if (!cardId && sessionId) {
    try {
      const sRes = await fetch(`/api/debates/${sessionId}/`);
      if (sRes.ok) {
        const sData = await sRes.json();
        cardId = sData.card_id;
      }
    } catch (_) { }
  }

  if (!cardId) return;
  _resolvedCardId = parseInt(cardId, 10);  // startDebate()가 참조할 수 있도록 저장
  try {
    const res = await fetch(`/api/debates/card-info/${cardId}/`);
    if (!res.ok) return;
    const data = await res.json();
    const topic = (data.debate_topic || data.card_title || '').trim();
    const title = (data.card_title || topic).trim();
    if (!topic) return;
    const els = [
      document.getElementById('topicLabel'),
      document.getElementById('introTopicLabel'),
    ];
    els.forEach(el => { if (el) el.textContent = topic; });
    // 찬성/반대 말풍선 위 주제
    const parts = topic.split(/\s+vs\.?\s+/i);
    const elPro = document.getElementById('stageTopicPro');
    const elCon = document.getElementById('stageTopicCon');
    const elVs = document.querySelector('.stage-topic-vs');
    if (parts.length === 2) {
      if (elPro) elPro.textContent = parts[0].trim();
      if (elCon) { elCon.textContent = parts[1].trim(); elCon.style.display = ''; }
      if (elVs) elVs.style.display = '';
    } else {
      if (elPro) { elPro.textContent = topic; elPro.style.flex = '1'; }
      if (elCon) elCon.style.display = 'none';
      if (elVs) elVs.style.display = 'none';
    }
    const fullText = document.getElementById('fullTopicText');
    if (fullText) fullText.textContent = topic;
    fitTitleText();
  } catch (_) { }
})();

function fitTitleText() {
  const el = document.getElementById('stageTitleText');
  if (!el) return;
  let size = 40;
  el.style.fontSize = size + 'px';
  while (el.scrollWidth > el.offsetWidth && size > 20) {
    size -= 1;
    el.style.fontSize = size + 'px';
  }
}

const MOCK_DATA = {
  rounds: [
    {
      roundNum: '1',
      pro: '청년 주거 안정을 위해 월세 지원 확대는 반드시 필요합니다.\n\n통계청 자료에 따르면 2024년 기준 청년 1인 가구의 소득 대비 임대료 비율(RIR)은 평균 31.4%로, 가처분 소득의 3분의 1이 주거비로 사라지고 있습니다. 이는 OECD 권고 기준인 25%를 크게 웃도는 수준으로, 청년들의 저축·자기계발·출산 결정 모두에 직접적인 영향을 미칩니다.\n\n월세 지원이 확대되면 청년들은 남은 소득을 소비와 자산 형성에 활용할 수 있고, 이는 지역 소비 증가와 내수 경제 활성화로 이어집니다. 단기 재정 투입이지만 장기적으로는 청년 고용률·출산율 상승이라는 사회적 편익이 훨씬 큽니다. 월세 지원은 현재 가장 즉효성 있는 청년 주거 정책입니다.',
      con: '월세 지원 확대는 표면적으로는 매력적이지만, 구조적 문제를 오히려 심화시킬 위험이 있습니다.\n\n경제학적으로 수요 보조금이 공급 확대 없이 투입되면 임대료가 상승하는 경향이 있습니다. 실제로 미국·영국의 임대 바우처 연구(HUD, 2022)에서도 지원금만큼 임대료가 오른 지역이 다수 확인됐습니다. 즉, 지원금의 상당 부분이 집주인 수익으로 귀결될 수 있습니다.\n\n또한 연간 수조 원의 예산이 필요한 사업을 지속하려면 다른 복지 항목의 예산을 줄여야 하고, 이는 저소득층·장애인·노인 지원 약화로 이어질 수 있습니다. 근본 해결책은 공공임대 공급 확대와 임대차 보호 강화입니다.',
      summary: '폴리 요약: 찬이는 청년 RIR 문제와 소비 여력 개선을, 반이는 임대료 상승과 재정 지속성 문제를 핵심으로 제시했어요.',
    },
    {
      roundNum: '2',
      pro: '반이가 제기한 임대료 상승 효과는 공급 병행 없이 수요 보조만 했을 때의 일부 사례에 해당합니다.\n\n핀란드와 덴마크는 바우처 방식의 월세 지원을 공공임대 공급 확대와 함께 추진해 임대료 안정과 청년 자립률 향상을 동시에 달성했습니다(OECD Housing Policy Brief, 2023). 정책 설계가 핵심이지, 지원 자체가 문제가 아닙니다.\n\n또한 높은 월세 부담은 청년의 자기계발 기회를 직접 제한합니다. 주거비에 소득 30% 이상을 쓰는 청년은 교육·자격증·창업에 투자할 여력이 없습니다. 이는 장기적으로 노동 생산성 저하와 세수 감소로 이어져 복지 재원 자체를 위협합니다. 월세 지원은 사회 전체의 투자입니다.',
      con: '찬이의 북유럽 사례는 공공임대 비율이 전체 주택의 20~30%에 달하는 나라에서의 결과입니다.\n\n우리나라 공공임대 비율은 약 8%로, 공급 기반 자체가 다릅니다. 이 상황에서 수요 보조만 늘리면 민간 임대 시장의 가격 압력이 강해질 수밖에 없습니다. 재정 측면에서도 연간 3~5조 원이 소요될 것으로 추산되는 사업을 지속하려면 명확한 세입 확충 방안이 있어야 합니다.\n\n보다 효과적인 대안은 청년 전용 공공임대 물량을 현재의 두 배로 늘리고, 임대차 3법을 개선해 전월세 가격 상한을 강화하는 것입니다. 실질적인 구조 개선 없이 현금 지원만 늘리는 방식은 지속 불가능합니다.',
      summary: '폴리 요약: 찬이는 북유럽의 병행 정책 사례를, 반이는 국내 공급 기반 차이와 재정 한계를 반박 근거로 내세웠어요.',
    },
    {
      roundNum: '3',
      pro: '지금까지의 논의를 정리하면, 월세 지원 확대 여부가 아니라 어떻게 설계하느냐가 핵심입니다.\n\n저는 다음 세 가지 원칙으로 지속 가능한 지원 모델을 제안합니다. 첫째, 소득 하위 40% 청년에 집중한 선별 지원으로 재정 효율을 높입니다. 둘째, 공공임대 공급 확대와 병행해 임대료 상승 압력을 구조적으로 억제합니다. 셋째, 지원금에 전입 신고·임대차 계약서 제출을 의무화해 집주인 전가를 차단합니다.\n\n이 방식이면 연간 약 1조 5천억 원 규모로도 실질적인 주거 안정 효과를 낼 수 있으며, 청년 고용 유지·출산율 개선이라는 사회 환원 가치가 지출을 상쇄합니다. 지금 당장 시행 가능한 가장 현실적인 정책입니다.',
      con: '찬이의 마지막 제안은 이전보다 진전된 내용이지만, 핵심 문제는 여전히 남습니다.\n\n선별 지원이라도 수요 보조 방식인 이상 민간 임대 시장 전체에 가격 상승 신호를 줄 수 있으며, 행정 비용과 부정 수급 관리 비용도 상당합니다. 또한 지원 대상에서 제외된 소득 하위 41~60% 청년은 오히려 임대료 상승 피해를 고스란히 입을 수 있습니다.\n\n저는 같은 재원을 청년 전용 공공임대 건설(10만 호 증설)과 임대료 상한제 강화에 투입하는 방안이 더 많은 청년을 구조적으로 보호한다고 주장합니다. 단기 현금 지원보다 인프라 투자가 장기적으로 더 큰 주거 안정을 만들어낼 수 있습니다.',
      summary: '폴리 요약: 두 입장 모두 선별성과 공급 병행의 중요성에는 공감했어요. 정책 수단의 선택이 핵심 쟁점으로 남았습니다!',
    },
  ],
  final: {
    pro_summary: {
      key_arguments: ['월세 부담 완화는 청년 주거 안정의 핵심입니다.', '소비 여력 개선과 경제 활성화로 이어집니다.', '지원 확대는 장기적 사회 투자로 효과적입니다.'],
      key_rebuttals: ['재정 부담 우려에 대한 경제적 파급 효과 제시', '지속 가능성 확보 방안 구체적 제안'],
    },
    con_summary: {
      key_arguments: ['재정 부담과 지속 가능성 문제가 있습니다.', '수요 증가로 구조적 해결이 우선입니다.', '선택적·맞춤형 지원이 더 효과적입니다.'],
      key_rebuttals: ['재원 마련 방안의 구체성 강화 필요', '다른 주거 정책과의 연계성 설명 보완 필요'],
    },
  },
};

/* ── Stage / Turn 상수 ─────────────────────────────────────── */
const STAGE_LABELS = {
  position: '입장 제시',
  pro_round: '찬성 세부주장',
  con_round: '반대 세부주장',
  summary: '주장 다지기',
  done: '주장 다지기',
  question_ans: '질문 답변',
};

const POLLY_INTROS = {
  position: {
    header: '🐻 폴리의 안내 · 입장 제시',
    text: '안녕하세요! 저는 토론 진행자 폴리예요 🐾\n지금부터 찬이와 반이의 토론을 시작할게요!\n먼저 찬이가 찬성 입장을, 반이가 반대 입장을 차례로 발표해요.\n두 친구의 첫 주장에 귀 기울여봐요 👂',
  },
  pro_round: {
    header: '🐻 폴리의 안내 · 찬성 세부주장',
    text: '입장 발표 끝! 이제 본격적인 토론이에요 🔥\n찬이 → 반이 → 찬이 순서로 발언하는 게 1턴이고, 총 3턴 진행돼요.\n각 턴이 끝나면 질문하거나 추가 토론을 요청할 수 있어요!',
  },
  con_round: {
    header: '🐻 폴리의 안내 · 반대 세부주장',
    text: '찬성 라운드 수고했어요! 이번엔 반대 차례예요 ✕\n반이 → 찬이 → 반이 순서로 발언하는 게 1턴, 역시 3턴 진행돼요.\n찬이가 어떻게 반격할지 지켜봐요!',
  },
  summary: {
    header: '🐻 폴리의 안내 · 주장 다지기',
    text: '치열한 토론 정말 수고했어요! 마지막 단계예요 🎉\n양측의 핵심 주장과 주요 반박을 깔끔하게 정리해드릴게요.\n누구의 주장이 더 설득력 있었나요? 결론은 여러분이 내려봐요!',
  },
};

// 각 스테이지의 선공(먼저 발언하는) 측 — 이 쪽이 말을 시작할 때 배지를 갱신한다
const ROUND_LEADER = { position: 'left', pro_round: 'left', con_round: 'right' };

/* ── AI vs USER 전용 폴리 안내 메세지 ────────────────────────────── */
// A-type: 라운드 시작 전 안내 (이전 라운드 종료 시점에 표시)
const AIUSER_ROUND_INTROS = {
  position: {
    easy: {
      header: '🐻 폴리의 안내 · 입장 제시',
      text: (name) => `안녕하세요! 저는 토론 진행자 폴리예요 🐾\n지금부터 토론을 시작할게요!\n먼저 입장 제시 단계예요.\n${name}님 대신 찬이와 반이가 입장을 발표할 거예요.\n두 친구의 첫 주장에 귀 기울여봐요 👂`,
    },
    hard: {
      pro: {
        header: '🐻 폴리의 안내 · 입장 제시',
        text: () => '안녕하세요! 저는 토론 진행자 폴리예요 🐾\n지금부터 토론을 시작할게요!\n먼저 입장 제시 단계예요.\n여러분이 직접 찬성 입장을 작성해주세요!\n작성 후 반이가 반대 입장을 발표할 거예요 ✍️',
      },
      con: {
        header: '🐻 폴리의 안내 · 입장 제시',
        text: () => '안녕하세요! 저는 토론 진행자 폴리예요 🐾\n지금부터 토론을 시작할게요!\n먼저 입장 제시 단계예요.\n찬이가 먼저 찬성 입장을 발표하고, 여러분이 반대 입장을 직접 작성해주세요!\n찬이의 주장을 잘 듣고 준비해봐요 👂',
      },
    },
  },
  pro_round: {
    pro: {
      header: '🐻 폴리의 안내 · 찬성 세부주장',
      text: '입장 발표 끝! 이제 본격 토론이에요 🔥\n여러분(주장) → 반이(반박) → 여러분(재반박) 순서로 1턴이 진행돼요.\n이 구조로 총 3턴! 근거를 들어 주장하고, 반이의 반박엔 꼭 재반박해봐요 💪',
    },
    con: {
      header: '🐻 폴리의 안내 · 찬성 세부주장',
      text: '입장 발표 끝! 이제 본격 토론이에요 🔥\n찬이(주장) → 여러분(반박) → 찬이(재반박) 순서로 1턴이 진행돼요.\n이 구조로 총 3턴! 찬이의 주장 속 허점을 찾아 날카롭게 반박해봐요 🕵️',
    },
  },
  con_round: {
    pro: {
      header: '🐻 폴리의 안내 · 반대 세부주장',
      text: '찬성 라운드 수고했어요! 이번엔 반대 차례예요 ✕\n반이(주장) → 여러분(반박) → 반이(재반박) 순서로 1턴이 진행돼요.\n이 구조로 총 3턴! 반이의 반대 주장을 논리적으로 반박해봐요 🛡️',
    },
    con: {
      header: '🐻 폴리의 안내 · 반대 세부주장',
      text: '찬성 라운드 수고했어요! 이번엔 여러분 차례예요 ✕\n여러분(주장) → 찬이(반박) → 여러분(재반박) 순서로 1턴이 진행돼요.\n이 구조로 총 3턴! 강력한 반대 근거로 찬이를 압도해봐요 💪',
    },
  },
  summary: {
    header: '🐻 폴리의 안내 · 주장 다지기',
    text: '치열한 토론 정말 수고했어요! 마지막 단계예요 🎉\nAI가 양측의 핵심 주장과 주요 반박을 정리하고,\n여러분의 토론 실력도 총평해드릴게요.\n어떤 평가가 나올지 기대해봐요 🔍',
  },
};

// B-type: 유저 입력 직전 유도 안내 (이전 발언 완료 시점에 표시)
const AIUSER_INPUT_PROMPTS = {
  position: {
    header: '🐻 폴리의 안내',
    pro: '찬성 입장을 작성해봐요!\n왜 찬성하는지 핵심 근거를 담아 주장해봐요 ✍️',
    con: '반대 입장을 작성해봐요!\n왜 반대하는지 핵심 근거를 담아 주장해봐요 ✍️',
  },
  pro_round: {
    header: '🐻 폴리의 안내',
    pro: {
      argument: [
        '여러분의 첫 번째 주장을 펼쳐봐요! 구체적인 사례나 근거를 들면 더 설득력 있어요 💪',
        '두 번째 주장이에요! 앞선 주장을 발전시키거나 새로운 근거를 추가해봐요 🔥',
        '마지막 주장이에요! 핵심 논지를 정리하며 강하게 마무리해봐요 🎯',
      ],
      response: [
        '반이가 반박했어요! 내 주장의 논리를 지키며 재반박해봐요 🛡️',
        '두 번째 재반박이에요! 반이의 지적을 정면으로 맞서봐요 💡',
        '마지막 재반박 기회예요! 찬성 주장의 핵심을 끝까지 지켜봐요 ✨',
      ],
    },
    con: {
      rebuttal: [
        '찬이의 첫 주장에 반박해봐요! 논리의 허점이나 반례를 찾아봐요 🕵️',
        '두 번째 주장에도 맞서봐요! 근거를 들어 꼼꼼하게 반박해봐요 🔍',
        '마지막 반박 기회예요! 찬이 주장의 핵심 약점을 공략해봐요 ⚔️',
      ],
    },
  },
  con_round: {
    header: '🐻 폴리의 안내',
    con: {
      argument: [
        '반대 첫 주장을 펼쳐봐요! 강력한 반대 근거를 제시해봐요 💪',
        '두 번째 주장이에요! 찬이의 반박을 고려해 논리를 더 강화해봐요 🔥',
        '마지막 주장이에요! 반대 입장의 핵심을 강하게 마무리해봐요 🎯',
      ],
      response: [
        '찬이가 반박했어요! 반대 주장을 더 단단히 만들어 재반박해봐요 🛡️',
        '두 번째 재반박이에요! 찬이의 지적을 정면으로 맞서봐요 💡',
        '마지막 재반박 기회예요! 반대 입장의 핵심을 끝까지 지켜봐요 ✨',
      ],
    },
    pro: {
      rebuttal: [
        '반이의 첫 주장에 반박해봐요! 찬성 입장으로 맞서봐요 🛡️',
        '두 번째 주장에도 반박해봐요! 찬성 근거를 더 탄탄히 보여봐요 💡',
        '마지막 반박 기회예요! 찬성 입장의 강점을 부각해봐요 ✨',
      ],
    },
  },
};

/* ── Polly 인트로 상태 ────────────────────────────────────────── */
const _introShown = new Set();   // 이미 소개한 스테이지 ('position', 'pro_round', ...)
let _introTimer = null;       // 자동 닫힘 타이머

/* ── State ─────────────────────────────────────────────────── */
const S = {
  sessionId: null,
  mode: null,   // 'aiai' | 'aiuser'
  stance: null,   // 'pro' | 'con'
  difficulty: 'easy', // 'easy' | 'hard'
  currentStage: 'position',  // 'position' | 'pro_round' | 'con_round' | 'summary'
  currentTurn: 1,           // 1 | 2 | 3 (pro_round / con_round 내 턴)
  memoOpen: false,
  currentAskTarget: 'pro',
  proSources: [],
  conSources: [],
  _isFreshDebate: false,  // startDebate로 시작한 신규 토론 여부 (이어하기와 구분)
};

let _selectedStance = null;

function showDifficulty(stance) {
  _selectedStance = stance;
  document.getElementById('diffTitle').textContent = stance === 'pro' ? '찬성으로 참여하기' : '반대로 참여하기';
  const easyHint = document.getElementById('easyDiffHint');
  if (easyHint) easyHint.textContent = stance === 'pro' ? '입장 제시를 찬이가 도와줘요.' : '입장 제시를 반이가 도와줘요.';
  document.getElementById('introStep1').style.display = 'none';
  document.getElementById('introStep2').style.display = 'flex';
}

function backToIntroStep1() {
  document.getElementById('introStep2').style.display = 'none';
  document.getElementById('introStep1').style.display = 'flex';
}

function startWithDifficulty(diff) {
  S.difficulty = diff;
  const badge = document.getElementById('diffBadge');
  if (badge) badge.textContent = diff === 'easy' ? 'Easy' : 'Hard';
  startDebate('aiuser', _selectedStance, diff);
}

let _sse = null;
const _messages = [];   // 대화 기록 { side, name, label, av, text, stage, turn, sectionId }
let _sectionId = 0;   // sendAction 호출 시마다 증가 — 한 turn 안의 모든 메시지를 묶는 키
const _sectionMeta = { 0: { isExtra: false } }; // { [sectionId]: { isExtra } } — 추가토론 여부 기록
let _actionInProgress = false; // sendAction 중복 호출 방지 플래그
let _autoNextTimer = null;  // 자동 '다음 단계' 진행 타이머 (버튼 없이 자동 넘김)
let _askSubmitted = false; // 질문 모달이 제출로 닫혔는지(취소 닫힘과 구분)
let _feedSrc = [];    // 피드 말풍선별 원문(sources) — renderFeed에서 재구성
let _pendingSection = null;  // sendAction 후 첫 메시지 도착 전까지 보여줄 예비 스테이지 { stage, turn }
let _feedAutoScroll = true;  // 새 발언 시 자동으로 최신 턴 추적 (이전 단계 열람 중이면 false)
let _progScroll = false; // 프로그램이 스크롤 중(사용자 스크롤과 구분)
const AUTO_NEXT_DELAY = 6000;  // 한 턴 읽을 시간(ms) 후 자동 진행 — 추가토론·질문 클릭 시 취소
let _pendingAutoNext = false; // 폴리 안내가 닫힌 뒤에 자동 진행을 시작하기 위한 대기 플래그
let _feedGenerating = false; // AI 발언 생성 중 → 피드에 '주장 제시 중…' 표시
let _feedLiveEl = null;  // 피드 내 라이브 타이핑/로딩 DOM 요소
let _feedLiveSide = null;  // _feedLiveEl이 어느 사이드 발언인지
let _pendingFeedAction = null; // 현재 애니메이션 완료 후 실행할 큐 (다른 사이드 로딩 대기)
let _userEchoText = null;  // 유저 발언 즉시 표시 후, 동일 내용의 서버 에코는 건너뛰기 위한 표시
let _expectingAI = false; // AI 발언을 기다리는 중인지(액션/제출 후) — 유저 차례엔 false라 '주장 제시 중' 안 뜸
const AUTO_ADVANCE = false; // 자동 진행 끔 — 유저가 '다음 단계' 버튼으로 수동 진행
function _clearAutoNext() { _pendingAutoNext = false; if (_autoNextTimer) { clearTimeout(_autoNextTimer); _autoNextTimer = null; } }
function _scheduleAutoNext() { if (!AUTO_ADVANCE) return; _clearAutoNext(); _autoNextTimer = setTimeout(() => { _autoNextTimer = null; sendAction('next'); }, AUTO_NEXT_DELAY); }
function _armAutoNext() {
  if (!AUTO_ADVANCE) return;
  const polly = document.getElementById('pollyIntroBubble');
  if (polly && polly.style.display !== 'none') { _pendingAutoNext = true; }
  else { _scheduleAutoNext(); }
}
const _FEED_LOADING = '';
let _extraRemaining = 2;    // 남은 추가토론 횟수 (서버 waiting 이벤트로 갱신)
let _displayStage = 'position'; // 배지에 표시할 스테이지 (S.currentStage와 별도 — 애니메이션 완료 후 갱신)
let _displayTurn = 1;          // 배지에 표시할 턴 (S.currentTurn와 별도 — 애니메이션 완료 후 갱신)
let _lastAiMsgType = null;       // AI vs USER: 직전 AI 발언의 msg_type ('argument'|'rebuttal'|'response')
let _pendingInputPrompt = null;     // AI vs USER: 애니메이션 완료 후 표시할 B-type 프롬프트 { header, text }
let _replayQueue = [];         // state 이벤트 replay 중 수신된 이벤트 버퍼
let _isReplaying = false;      // position 발언 replay 중 여부 (모든 이벤트 큐잉)
let _isDisplayingState = false;      // 이어하기 복원 직후 message만 잠시 큐잉
let _posSources = { left: [], right: [] }; // position 발언 sources 추적
let _pendingConPosition = null;   // position 단계 con 발언 순차 표시용 { text, sources, stage, turn, sectionId }
let _awaitingConPosFlush = false;  // (구) 미사용 — _proRoundPending로 대체
let _conPosFlushDelay = 3000;   // con 타이핑 완료 후 읽기 딜레이 (ms)
// 입장제시(찬→반) 타이핑이 끝나면 pro_round를 시작하라는 대기 플래그.
// round_update(pro_round)가 입장 타이핑 도중/직후 도착해도, 실제 전환은 반이 타이핑 완료 후로 미룬다.
let _proRoundPending = false;
// 다음 단계/유저 입력으로 인한 '진행 중 재접속'에서는 서버가 보내는 state 스냅샷으로
// 과거 메시지를 다시 그리면 안 된다(이전 턴 내용 잔존·순서 역전 원인). 이어하기(페이지 새로고침)일 때만 복원.
let _skipStateRestore = false;
// 추가 토론(extra) 진행 중 플래그. extra는 같은 stage로 round_update가 오므로 일반 '다음 턴'과
// 구분해야 한다(턴 번호 증가 금지 + 선공이 리더가 아니라 상대측).
let _pendingExtra = false;
// 주제 이탈·경고 표시 중 플래그. true인 동안 B-type 입력 유도 프롬프트가 경고를 덮어쓰지 않도록.
let _warningActive = false;

/* ── Static image paths (window._DEBATE_IMG은 debate.html 인라인 <script>에서 주입) ── */
const IMG = window._DEBATE_IMG;

/* ══════════════════════════════════════════════════════════════
   START DEBATE
══════════════════════════════════════════════════════════════ */
async function startDebate(mode, stance, difficulty) {
  S.mode = mode;
  S.stance = stance || null;
  S.difficulty = difficulty || 'easy';
  S._isFreshDebate = true;
  _isReplaying = false;
  _isDisplayingState = false;
  _skipStateRestore = false;   // 첫 스트림: state 스냅샷은 비어 있어 무해
  _pendingExtra = false;
  _proRoundPending = false;
  _replayQueue = [];
  _posSources = { left: [], right: [] };
  _pendingConPosition = null;
  _awaitingConPosFlush = false;

  // Hide intro, update badges
  document.getElementById('introOverlay').style.display = 'none';
  document.getElementById('introStep1').style.display = 'flex';
  document.getElementById('introStep2').style.display = 'none';
  document.getElementById('modeBadge').textContent = mode === 'aiai' ? 'AI vs AI' : 'AI vs USER';

  // AI vs User: show stance badge, swap character for user side
  if (mode === 'aiuser') {
    document.getElementById('stanceBadge').textContent = stance === 'pro' ? '찬성' : '반대';
    if (stance === 'pro') {
      // user: left(찬성) = meo, AI: right(반대) = 반이
      const _lc = document.getElementById('leftChar'), _lcf = document.getElementById('leftCharFinal');
      _lc.src = IMG.meo; _lc.className = 'stage-char stage-char-left';
      _lcf.src = IMG.meo; _lcf.className = 'stage-char-final stage-char-left-final';
      const _rc = document.getElementById('rightChar'), _rcf = document.getElementById('rightCharFinal');
      _rc.src = IMG.bani; _rc.className = 'stage-char stage-char-right';
      _rcf.src = IMG.bani; _rcf.className = 'stage-char-final stage-char-right-final';
    } else {
      // user: right(반대) = mex, AI: left(찬성) = 찬이
      const _rc = document.getElementById('rightChar'), _rcf = document.getElementById('rightCharFinal');
      _rc.src = IMG.mex; _rc.className = 'stage-char stage-char-right-mex';
      _rcf.src = IMG.mex; _rcf.className = 'stage-char-final stage-char-right-mex-final';
      const _lc = document.getElementById('leftChar'), _lcf = document.getElementById('leftCharFinal');
      _lc.src = IMG.chani; _lc.className = 'stage-char stage-char-chani';
      _lcf.src = IMG.chani; _lcf.className = 'stage-char-final stage-char-chani-final';
    }
    // Hide both ask buttons in AI vs User mode
    document.getElementById('btnAskPro').style.display = 'none';
    document.getElementById('btnAskCon').style.display = 'none';
  }

  // Show loading + 입장 제시 인트로 (SSE 이전에 미리 표시)
  _loadingBubble('left');
  _loadingBubble('right');
  if (mode === 'aiuser') {
    _showAiUserRoundIntro('position');  // AI vs USER: 모드/난이도별 입장제시 안내
  } else {
    _showPollyIntro('position');        // AI vs AI: 기존 안내
  }

  // Get card_id: URL 파라미터 우선, 없으면 _initCardInfo()가 저장한 값 사용
  // (readonly URL 등 card_id 파라미터가 없는 경우 대응)
  const params = new URLSearchParams(window.location.search);
  const cardId = parseInt(params.get('card_id') || '0') || _resolvedCardId;
  if (!cardId) {
    document.getElementById('introOverlay').style.display = 'flex';
    showGenericModal({
      icon: '⚠️',
      title: '카드 정보 없음',
      msg: '토론 카드 정보를 불러올 수 없어요.\n잠시 후 다시 시도해 주세요.',
      buttons: [{ label: '확인', variant: 'primary' }],
    });
    return;
  }

  if (MOCK_MODE) {
    S.sessionId = 'mock';
    S.mockRound = 0;
    _mockRound(0);
    return;
  }

  try {
    const body = {
      card_id: cardId,
      mode: mode === 'aiai' ? 'ai_vs_ai' : 'ai_vs_user',
      difficulty: S.difficulty,
    };
    if (mode === 'aiuser') body.user_stance = stance;

    const res = await fetch('/api/debates/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': _getCsrf(),
        'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || '')
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(JSON.stringify(data));

    S.sessionId = data.debate_session_id;
    _startSSE();
  } catch (err) {
    document.getElementById('introOverlay').style.display = 'flex';
    const msg = err.message || '';
    const is500 = /500|internal server|서버/i.test(msg);
    showGenericModal({
      icon: is500 ? '🔧' : '⚠️',
      title: is500 ? '서버 오류' : '토론 시작 실패',
      msg: is500
        ? '서버에 일시적인 문제가 발생했어요.\n잠시 후 다시 시도해 주세요.'
        : ('토론을 시작하지 못했어요.\n' + msg),
      buttons: [{ label: '확인', variant: 'primary' }],
    });
  }
}

/* ══════════════════════════════════════════════════════════════
   RESUME DEBATE (이어하기)
══════════════════════════════════════════════════════════════ */
function _resumeDebate(sessionId) {
  S.sessionId = sessionId;
  S._isFreshDebate = false;
  _isReplaying = false;
  _isDisplayingState = false;
  _skipStateRestore = false;   // 이어하기(새로고침): state 스냅샷으로 화면 복원 필요
  _proRoundPending = false;
  _pendingExtra = false;
  _replayQueue = [];
  _posSources = { left: [], right: [] };
  _pendingConPosition = null;
  _awaitingConPosFlush = false;
  _sideStage.left = null;
  _sideStage.right = null;
  const params = new URLSearchParams(window.location.search);
  S._readonly = params.get('readonly') === 'true';

  document.getElementById('introOverlay').style.display = 'none';
  if (!S._readonly) {
    _loadingBubble('left');
    _loadingBubble('right');
  }

  _startSSE();
}

/* ══════════════════════════════════════════════════════════════
   SSE STREAM
══════════════════════════════════════════════════════════════ */
function _startSSE() {
  if (_sse) _sse.close();
  _hideActionBar();
  _evQ = [];          // 새 스트림: 이벤트 큐 초기화 (이전 턴 잔여 차단)
  _evBusy = false;
  _sse = new EventSource(`/api/debates/${S.sessionId}/stream/`);

  _sse.onmessage = (e) => {
    try {
      _feedEvent(JSON.parse(e.data));
    }
    catch (err) {
      console.error('[DEBATE] _feedEvent 처리 오류:', err, '\n원본:', (e.data || '').slice(0, 160));
    }
  };
  _sse.onerror = () => {
    _sse.close();
    _actionInProgress = false;
  };
}

/* ══════════════════════════════════════════════════════════════
   이벤트 직렬화 큐
   모든 SSE 이벤트를 도착 순서대로 한 줄로 처리한다. message 이벤트는
   타이핑이 끝날 때까지 다음 이벤트 처리를 막아, 발언이 겹치지 않게(동시 출력 방지)
   '한 발언씩' 보여준다. 입장제시·찬성/반대 라운드·추가토론 모두 동일 경로로 처리.
══════════════════════════════════════════════════════════════ */
let _evQ = [];
let _evBusy = false;   // message가 타이핑 중이면 true → 다음 이벤트 대기
// const _MSG_GAP_MS   = 600;    // FOR TEST 테스트용, 발언과 발언 사이(ms) 간격
const _MSG_GAP_MS = 1000;    // 발언과 발언 사이(ms) 간격
const _STAGE_READ_MS = 2200;  // 스테이지 전환 시 이전 단계를 읽을 시간

function _feedEvent(ev) {
  _dbg('SSE 수신:', ev.type, ev.participant || '', ev.stage || '',
    '| evBusy=' + _evBusy, 'skipState=' + _skipStateRestore,
    '| S=' + S.currentStage + '/' + S.currentTurn, 'q=' + _evQ.length);
  if (ev.type === 'state') {
    // 진행 중 재접속(다음 단계·유저 입력)에서는 과거 메시지를 다시 그리지 않는다.
    if (_skipStateRestore) return;
    _onState(ev);           // 이어하기(새로고침) 복원
    return;
  }
  _evQ.push(ev);
  _drainEv();
}

function _drainEv() {
  if (_evBusy || _evQ.length === 0) return;
  const ev = _evQ.shift();
  switch (ev.type) {
    case 'message': {
      const started = _renderMessageSerial(ev);
      if (!started) _drainEv();          // 렌더할 게 없으면 바로 다음
      break;                             // 렌더 시작 시 onDone에서 _drainEv 재개
    }
    case 'round_update': {
      const delay = _applyRoundUpdate(ev);
      setTimeout(_drainEv, delay || 0);
      break;
    }
    case 'waiting':
      _applyWaiting(ev);                 // 스트림 종료(액션바 표시) — 이후 이벤트 없음
      break;
    case 'summary':
      if (_sse) _sse.close();
      _showFinal(ev.data || ev);
      break;
    case 'warning':
      if (ev.category === 'foul' || typeof ev.foul_count === 'number') {
        if (typeof window.showProfanityModal === 'function') {
          window.showProfanityModal(ev.foul_count);
        }
      } else {
        _showUserWarning(ev.message);
      }
      _drainEv();
      break;
    case 'error':
      _applyError(ev);
      break;
    default:                              // generation_start 등
      if (ev.type === 'generation_start' && _expectingAI && _displayStage !== 'summary') { _feedGenerating = true; renderFeed(); }
      _drainEv();
  }
}

/* message 1건 렌더 (타이핑). 시작하면 true 반환하고, 타이핑 완료 시 onDone에서 큐 재개 */
function _renderMessageSerial(ev) {
  const { participant, content, msg_type, sources = [] } = ev;
  if (!content) return false;
  if (participant === 'user' && content === _userEchoText) { _userEchoText = null; return false; }   // 이미 즉시 표시한 유저 발언 에코 → 건너뜀
  _dbg('  render message:', participant, msg_type, (content || '').slice(0, 12));
  // position 발언 sources 추적
  if (msg_type === 'position') {
    if (participant === 'pro') _posSources.left = sources;
    if (participant === 'con') _posSources.right = sources;
  }
  // AI vs USER: 직전 AI 발언 타입 추적
  if (S.mode === 'aiuser' && participant !== 'user') _lastAiMsgType = msg_type || null;

  let side;
  if (participant === 'pro') side = 'left';
  else if (participant === 'con') side = 'right';
  else if (participant === 'user') side = S.stance === 'pro' ? 'left' : 'right';
  else return false;

  _evBusy = true;
  _fillBubble(side, content, sources, () => {
    _evBusy = false;
    setTimeout(_drainEv, _MSG_GAP_MS);   // 다음 발언까지 짧은 간격
  });
  return true;
}

/* round_update 적용 — 배지/스테이지/턴 갱신 + 폴리. 다음 이벤트까지 둘 딜레이(ms) 반환 */
function _applyRoundUpdate(ev) {
  const stage = ev.stage || 'position';
  if (!VALID_STAGES.has(stage)) return 0;
  _dbg('round_update 적용:', 'stage=' + stage, '| S=' + S.currentStage + '/' + S.currentTurn,
    'pendingExtra=' + _pendingExtra);

  const prevStage = S.currentStage;
  const _isExtraRU = _pendingExtra && stage === S.currentStage;   // 추가 토론 재개

  if (stage !== S.currentStage) {
    S.currentStage = stage;
    S.currentTurn = 1;
    _lastAiMsgType = null;
    if (S.mode === 'aiuser') _showAiUserRoundIntro(stage);
  } else if (_isExtraRU) {
    _lastAiMsgType = null;               // 추가 토론은 같은 턴의 연장 → 턴 번호 유지
  } else {
    S.currentTurn = Math.min(S.currentTurn + 1, 3);
    _lastAiMsgType = null;
  }

  _displayStage = S.currentStage;
  _displayTurn = S.currentTurn;
  if (prevStage !== S.currentStage && S.mode !== 'aiuser') _showPollyIntro(S.currentStage);
  _updateStageDisplay();
  if (_isExtraRU) _pendingExtra = false;

  // 스테이지가 바뀌면 이전 단계를 읽을 시간을 준 뒤 다음 발언을 시작
  return (prevStage !== S.currentStage) ? _STAGE_READ_MS : 0;
}

/* waiting 적용 — 스트림 종료 + 액션바 */
function _applyWaiting(ev) {
  _feedGenerating = false; _expectingAI = false;
  _pendingFeedAction = null;   // 큐 초기화 — 대기 상태에서 남은 로딩 예약 제거
  _setFeedLive(null);          // 피드 내 로딩/타이핑 요소 제거
  renderFeed();                // 피드 재빌드 (로딩 없이)
  if (ev.current_round != null) S.currentTurn = ev.current_round;
  if (ev.current_stage && VALID_STAGES.has(ev.current_stage)) S.currentStage = ev.current_stage;
  if (_displayStage === S.currentStage) { _displayTurn = S.currentTurn; _updateStageDisplay(); }
  if (_sse) _sse.close();
  _showActionBar(ev);
}

function _applyError(ev) {
  console.error('debate error:', ev.message);
  const isNotFound = /not found|찾을 수 없|does not exist|404/i.test(ev.message || '');
  showGenericModal({
    icon: isNotFound ? '🔍' : '⚠️',
    title: isNotFound ? '대화를 찾을 수 없어요' : '오류가 발생했어요',
    msg: isNotFound
      ? '요청하신 토론 세션이 존재하지 않거나\n삭제된 세션이에요.\n\n새로운 토론을 시작해 주세요!'
      : (ev.message || '알 수 없는 오류가 발생했어요.\n잠시 후 다시 시도해 주세요.'),
    buttons: [
      { label: '홈으로', variant: 'ghost', onClick: () => { window.location.href = '/'; } },
      { label: '새 토론 시작', variant: 'primary', onClick: () => { window.location.href = '/debate/'; } },
    ],
  });
  if (_sse) { _sse.close(); _actionInProgress = false; }
}

/* round_update에서 올 수 있는 유효 스테이지 값 */
const VALID_STAGES = new Set(['position', 'pro_round', 'con_round', 'summary', 'done']);

/* waiting 시점에서 표시할 인트로 스테이지 결정
   ev.current_stage는 LangGraph 내부 상태('user_choice' 등)일 수 있으므로
   VALID_STAGES 필터를 통과한 S.currentStage를 사용 */
function _nextIntroStage() {
  const stage = S.currentStage;
  const turn = S.currentTurn;

  // ① 현재 스테이지의 인트로가 아직 표시 안 됐으면 지금 표시
  if (stage && POLLY_INTROS[stage] && !_introShown.has(stage)) {
    return stage;
  }

  // ② turn 3 이상이면 다음 스테이지 예고
  if (stage === 'pro_round' && turn >= 3) return 'con_round';
  if (stage === 'con_round' && turn >= 3) return 'summary';
  return null;
}


/* ── Typewriter 설정 ───────────────────────────────────────── */
const TW_CHUNK = 2;   // 한 번에 찍는 글자 수
// const TW_MS    = 20;  // FOR TEST 테스트용, 찍는 간격(ms)
const TW_MS = 100;  // 찍는 간격(ms)
const _twTimer = { left: null, right: null };  // 진행 중 타이머 핸들
const _skipFn = { left: null, right: null };  // 현재 타이핑 발언을 즉시 완성하는 함수 (빨리감기)
const _sideStage = { left: null, right: null }; // 사이드별 마지막 표시 스테이지

/* ══════════════════════════════════════════════════════════════
   RESUME — state 이벤트 처리
══════════════════════════════════════════════════════════════ */

/* state 이벤트의 current_stage가 "user_choice" 등 내부 상태일 때 실제 스테이지 추론
   (AI agent의 waiting 이벤트 inferred_stage 로직과 동일) */
function _inferStageFromMessages(messages, rawStage) {
  if (VALID_STAGES.has(rawStage)) return rawStage;
  // 마지막 argument 메시지 발언자로 판단
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.msg_type === 'argument') {
      if (msg.participant === 'pro') return 'pro_round';
      if (msg.participant === 'con') return 'con_round';
    }
  }
  // argument 없으면 position 단계
  return 'position';
}

/* AI vs User 모드에서 메시지 목록으로 stance 추론 */
function _inferStanceFromMessages(messages) {
  const firstUser = messages.find(m => m.participant === 'user');
  if (!firstUser) return null;
  const idx = messages.indexOf(firstUser);
  const prev = idx > 0 ? messages[idx - 1] : null;

  if (firstUser.msg_type === 'position') {
    // 이미 pro position 메시지가 있으면 user는 con
    return messages.slice(0, idx).some(m => m.msg_type === 'position') ? 'con' : 'pro';
  }
  if (firstUser.msg_type === 'rebuttal') {
    // 이전 발언자가 pro면 user는 con(반박), con이면 user는 pro(반박)
    if (prev && prev.participant === 'pro') return 'con';
    if (prev && prev.participant === 'con') return 'pro';
    return 'con';
  }
  if (firstUser.msg_type === 'argument' || firstUser.msg_type === 'response') {
    if (prev && prev.participant === 'con') return 'pro';
    if (prev && prev.participant === 'pro') return 'con';
  }
  return 'pro';
}

/* state 이벤트 → UI 복원 */
function _onState(ev) {
  const messages = ev.messages || [];
  const mode = ev.mode === 'ai_vs_user' ? 'aiuser' : 'aiai';
  const stage = _inferStageFromMessages(messages, ev.current_stage || '');
  const turn = ev.current_round || 1;
  _dbg('★ _onState(복원):', 'mode=' + mode, 'stage=' + stage, 'turn=' + turn,
    'messages.len=' + messages.length, 'isFresh=' + S._isFreshDebate);

  S.mode = mode;
  S.currentStage = stage;
  S.currentTurn = turn;
  _displayStage = stage;
  _displayTurn = turn;

  // AI vs User: stance 추론 + 캐릭터/배지 세팅
  // 신규 토론(_isFreshDebate)이면 startDebate에서 이미 S.stance가 올바르게 세팅됨 → 덮어쓰지 않음
  // 이어하기: 서버가 user_stance를 직접 보내주면 최우선 사용, 없으면 메시지 목록으로 추론
  if (mode === 'aiuser') {
    if (!S._isFreshDebate) {
      let stance = ev.user_stance || _inferStanceFromMessages(messages);
      if (!stance) {
        if (stage === 'pro_round') stance = 'con';
        else if (stage === 'con_round') stance = 'pro';
      }
      S.stance = stance || 'pro';
    }

    document.getElementById('stanceBadge').textContent = S.stance === 'pro' ? '찬성' : '반대';
    if (S.stance === 'pro') {
      // user: left(찬성) = meo, AI: right(반대) = 반이
      const lc = document.getElementById('leftChar'), lcf = document.getElementById('leftCharFinal');
      lc.src = IMG.meo; lc.className = 'stage-char stage-char-left';
      lcf.src = IMG.meo; lcf.className = 'stage-char-final stage-char-left-final';
      const rc = document.getElementById('rightChar'), rcf = document.getElementById('rightCharFinal');
      rc.src = IMG.bani; rc.className = 'stage-char stage-char-right';
      rcf.src = IMG.bani; rcf.className = 'stage-char-final stage-char-right-final';
    } else {
      // user: right(반대) = mex, AI: left(찬성) = 찬이
      const rc = document.getElementById('rightChar'), rcf = document.getElementById('rightCharFinal');
      rc.src = IMG.mex; rc.className = 'stage-char stage-char-right-mex';
      rcf.src = IMG.mex; rcf.className = 'stage-char-final stage-char-right-mex-final';
      const lc = document.getElementById('leftChar'), lcf = document.getElementById('leftCharFinal');
      lc.src = IMG.chani; lc.className = 'stage-char stage-char-chani';
      lcf.src = IMG.chani; lcf.className = 'stage-char-final stage-char-chani-final';
    }
    document.getElementById('btnAskPro').style.display = 'none';
    document.getElementById('btnAskCon').style.display = 'none';
  }

  document.getElementById('modeBadge').textContent = mode === 'aiai' ? 'AI vs AI' : 'AI vs USER';

  // 이미 지나간(또는 현재) 스테이지 인트로만 '표시됨' 처리 — 미래 스테이지 인트로는 남겨둔다.
  // 새 토론(_isFreshDebate)은 SSE 첫머리의 빈 state 스냅샷이 모든 폴리 안내를 죽이지 않도록 건드리지 않는다.
  // (position 인트로는 startDebate()에서 이미 표시됨)
  if (!S._isFreshDebate) {
    const _STAGE_ORDER = ['position', 'pro_round', 'con_round', 'summary', 'done'];
    const _curIdx = _STAGE_ORDER.indexOf(stage);
    _STAGE_ORDER.forEach((s, i) => {
      if (i <= _curIdx) {
        _introShown.add(s);
        _introShown.add('aiuser_' + s);
      }
    });
  }

  _updateStageDisplay();

  // 이전 메시지 복원 — 각 메시지의 단계/턴을 msg_type로 추론해 라운드별 섹션 구성.
  // (서버 메시지엔 단계 정보가 없어 클라이언트가 추론 → 라운드 바로 이전 단계 클릭 열람 가능)
  let lastLeft = null, lastRight = null;
  let _rStage = 'position', _pt = 0, _ct = 0, _sec = _sectionId, _prevKey = null;
  messages.forEach((msg, _idx) => {
    const { participant, content, sources = [], msg_type } = msg;
    if (msg_type === 'summary') return;   // 요약은 피드 말풍선으로 그리지 않음

    // 유저 질문 → 질문 카드로 복원
    // msg_type='question' (신규) 또는 participant='user'+다음이 question_ans (구형 저장)
    const _nextMsg = messages[_idx + 1] || {};
    const _isQuestion = msg_type === 'QUESTION' || msg_type === 'question' ||
      (participant === 'user' && _nextMsg.msg_type === 'question_ans');
    if (_isQuestion) {
      const qTarget = (_nextMsg.msg_type === 'question_ans' && _nextMsg.participant)
        || (S.stance === 'pro' ? 'con' : 'pro');
      _messages.push({
        type: 'question', target: qTarget, text: content,
        stage: _rStage, sectionId: _sec
      });
      return;
    }

    let side;
    if (participant === 'pro') side = 'left';
    else if (participant === 'con') side = 'right';
    else if (participant === 'user') side = S.stance === 'pro' ? 'left' : 'right';
    else return;

    // 단계 추론: position → 입장, argument(공격측 선공) → 새 라운드/턴, 나머지는 현재 단계 유지
    const kind = (participant === 'user') ? (S.stance || 'pro')
      : (participant.indexOf('pro') === 0 ? 'pro'
        : participant.indexOf('con') === 0 ? 'con' : '');
    if (msg_type === 'position') {
      _rStage = 'position';
    } else if (msg_type === 'argument') {
      if (kind === 'pro') { _rStage = 'pro_round'; _pt = Math.min(_pt + 1, 3); }
      else if (kind === 'con') { _rStage = 'con_round'; _ct = Math.min(_ct + 1, 3); }
    }
    // 질문 답변(question_ans)은 stage 추론에 영향 안 줌
    const _isRestoreAnswer = (msg_type === 'question_ans');
    const _turn = _rStage === 'pro_round' ? (_pt || 1) : _rStage === 'con_round' ? (_ct || 1) : 1;
    const _key = _rStage + '-' + _turn;
    if (_key !== _prevKey) { _sec++; _prevKey = _key; }   // 단계/턴 바뀌면 새 섹션(라운드 바 앵커용)

    const info = _charInfo(side);
    _messages.push({
      side, name: info.name, emoji: info.emoji, label: info.label, av: info.av,
      text: content, sources, stage: _rStage, turn: _turn, sectionId: _sec,
      isAnswer: _isRestoreAnswer
    });
    if (side === 'left') lastLeft = { content, sources };
    else lastRight = { content, sources };
  });
  _sectionId = _sec;   // 이후 라이브 발언은 이 섹션 다음부터

  _renderRecords();

  // ── 완료 세션(done) 또는 다시 보기(readonly) ──────────────────
  if (stage === 'done' || S._readonly) {
    if (lastLeft) _fillBubbleInstant('left', lastLeft.content, lastLeft.sources);
    else _clearBubble('left');
    if (lastRight) _fillBubbleInstant('right', lastRight.content, lastRight.sources);
    else _clearBubble('right');

    if (_sse) { _sse.close(); _sse = null; }
    if (stage === 'done' || S._readonly) {
      const summaryMsg = messages.find(m => m.msg_type === 'summary');
      let finalData = {};
      if (summaryMsg && summaryMsg.content) {
        try { finalData = JSON.parse(summaryMsg.content); }
        catch (_) { finalData = {}; }
      }
      setTimeout(() => _showFinal(finalData), 150);
    }
    return;
  }

  // ── 진행 중 세션 이어하기: 마지막 발언만 즉시 표시. 이후 들어오는 새 발언은
  //    이벤트 큐(_feedEvent→_drainEv)가 순서대로 처리한다.
  if (lastLeft) { _fillBubbleInstant('left', lastLeft.content, lastLeft.sources); _sideStage['left'] = stage; }
  if (lastRight) { _fillBubbleInstant('right', lastRight.content, lastRight.sources); _sideStage['right'] = stage; }
}

/* ── Message → bubble ──────────────────────────────────────── */
function _charInfo(side) {
  const isUser = S.mode === 'aiuser';
  if (side === 'left') {
    if (isUser && S.stance === 'pro') return { name: '나', emoji: '', label: '찬성', av: IMG.avMeo };
    return { name: '찬이', emoji: '🐶', label: '찬성', av: IMG.avChani };
  } else {
    if (isUser && S.stance === 'con') return { name: '나', emoji: '', label: '반대', av: IMG.avMex };
    return { name: '반이', emoji: '🐰', label: '반대', av: IMG.avBani };
  }
}

function _clearBubble(side) {
  if (_twTimer[side]) { clearTimeout(_twTimer[side]); _twTimer[side] = null; }
  _skipFn[side] = null;
  const skipBtn = document.getElementById('skipAllBtn');
  const el = document.getElementById(side === 'left' ? 'leftContent' : 'rightContent');
  el.innerHTML = '';
  const btn = document.getElementById(side === 'left' ? 'leftSourceBtn' : 'rightSourceBtn');
  if (btn) btn.style.display = 'none';
}

function _fillBubble(side, text, sources, onDone, opts) {
  const _isAnswer = !!(opts && opts.isAnswer);  // 질문 답변 여부
  // 스테이지/턴은 큐 체크 전에 캡처 — 큐에서 재실행될 때 opts로 전달해 일관성 유지
  const capturedStage = (opts && opts.capturedStage) || S.currentStage;
  const capturedTurn = (opts && opts.capturedTurn) || S.currentTurn;
  // 다른 사이드가 현재 타이핑 중이면 큐에 넣고 대기
  const _otherSide = side === 'left' ? 'right' : 'left';
  if (_feedLiveEl && _feedLiveSide === _otherSide) {
    _pendingFeedAction = () => _fillBubble(side, text, sources, onDone,
      { ...(opts || {}), capturedStage, capturedTurn });
    return;
  }
  _dbg('▶ _fillBubble(타이핑 시작):', side, 'stage=' + capturedStage, '내용:', (text || '').slice(0, 14),
    '| twL=' + (_twTimer.left !== null), 'twR=' + (_twTimer.right !== null));

  // ── 배지 갱신: 각 라운드의 선공 측이 발언을 시작하는 순간에만 갱신
  if (ROUND_LEADER[capturedStage] === side) {
    const oldStage = _displayStage;
    _displayStage = capturedStage;
    _displayTurn = capturedTurn;
    // AI vs AI만 여기서 인트로 표시 — AI vs USER는 round_update / startDebate 에서 처리
    if (oldStage !== capturedStage && S.mode !== 'aiuser') _showPollyIntro(capturedStage);
    _updateStageDisplay();
  }

  // 새 스테이지 첫 발언: 상대측 말풍선 삭제 (이전 스테이지 내용이 남아있으면)
  const otherSide = side === 'left' ? 'right' : 'left';
  if (capturedStage !== _sideStage[side] && _sideStage[otherSide] !== capturedStage) {
    _clearBubble(otherSide);
  }
  _sideStage[side] = capturedStage;

  const el = document.getElementById(side === 'left' ? 'leftContent' : 'rightContent');
  const btnId = side === 'left' ? 'leftSourceBtn' : 'rightSourceBtn';

  // 이전 타이핑 취소
  if (_twTimer[side]) { clearTimeout(_twTimer[side]); _twTimer[side] = null; }

  // 숨겨진 버블에도 즉시 채움 (_isShowingLoading 등 내부 호환성용)
  const ps = text.split('\n').filter(l => l.trim());
  el.innerHTML = ps.map(p => `<p style="margin:0 0 6px;">${_esc(p)}</p>`).join('');
  const srcBtn = document.getElementById(btnId);
  if (srcBtn) srcBtn.style.display = 'none';

  // ── 피드 라이브 타이핑 요소 설정 (로딩 → 타이핑으로 전환)
  const info = _charInfo(side);
  const who = _esc(((info.emoji || '') + ' ' + (info.name || '')).trim());
  _setFeedLive(side,
    '<div class="feed-msg ' + side + '"><div class="feed-bubble">'
    + '<div class="feed-who">' + who + '</div>'
    + '<span class="feed-typing-body"></span>'
    + '</div></div>'
  );

  let pIdx = 0, cIdx = 0;

  /* 타이핑 완료 공통 처리 (내부) */
  function _done() {
    _setFeedLive(null);
    const info2 = _charInfo(side);
    _messages.push({
      side, name: info2.name, emoji: info2.emoji, label: info2.label, av: info2.av,
      text, sources, stage: capturedStage, turn: capturedTurn, sectionId: _sectionId,
      isAnswer: _isAnswer
    });
    _renderRecords();
    _finishBubble(side, sources, btnId, onDone);
    // 큐에 대기 중인 다음 사이드 액션 실행 (e.g. 찬성 완료 후 반대 로딩 시작)
    if (_pendingFeedAction) {
      const fn = _pendingFeedAction;
      _pendingFeedAction = null;
      fn();
    }
    // 질문 답변 완료 → 자동으로 다음 단계 진행 (action bar 노출 없이 바로 이어감)
    if (_isAnswer) {
      setTimeout(() => sendAction('next'), 600);
    }
  }

  function tick() {
    if (pIdx >= ps.length) { _done(); return; }
    const para = ps[pIdx];
    const end = Math.min(cIdx + TW_CHUNK, para.length);

    // 현재까지 입력된 텍스트 계산
    let partial = '';
    for (let i = 0; i < pIdx; i++) partial += ps[i] + '\n';
    partial += para.substring(0, end);

    // 피드 라이브 요소 직접 업데이트 (전체 재렌더 없이)
    if (_feedLiveEl) {
      const bodyEl = _feedLiveEl.querySelector('.feed-typing-body');
      if (bodyEl) bodyEl.textContent = partial;
      const feed = document.getElementById('debateFeed');
      if (feed && _feedAutoScroll) feed.scrollTop = feed.scrollHeight;
    }

    if (end >= para.length) { pIdx++; cIdx = 0; }
    else { cIdx = end; }
    _twTimer[side] = setTimeout(tick, TW_MS);
  }

  // 빨리감기 훅 등록
  const skipBtnEl = document.getElementById('skipAllBtn');
  _skipFn[side] = () => {
    if (_twTimer[side]) { clearTimeout(_twTimer[side]); _twTimer[side] = null; }
    _skipFn[side] = null;
    _done();
  };

  tick();
}

/* 타이핑 완료 공통 처리 — tick() 자연 완료 / _skipFn 스킵 모두 이 경로
   onDone이 정확히 1번만 호출되도록 여기에서 일괄 처리 */
function _finishBubble(side, sources, btnId, onDone) {
  _twTimer[side] = null;
  _skipFn[side] = null;
  // 스킵 버튼 숨기기
  const skipBtn = document.getElementById('skipAllBtn');
  // 출처 버튼 표시
  if (sources && sources.length) {
    if (side === 'left') S.proSources = sources;
    if (side === 'right') S.conSources = sources;
    const btn = document.getElementById(btnId);
    if (btn) btn.style.display = 'flex';
  }
  // AI vs USER: AI 측 완료 → 대기 중인 B-type 입력 유도 프롬프트 표시
  if (S.mode === 'aiuser' && _pendingInputPrompt) {
    const aiSide = S.stance === 'pro' ? 'right' : 'left';
    if (side === aiSide) {
      const bar = document.getElementById('userTurnBar');
      if (bar && bar.style.display !== 'none') {
        _showPollyMessage(_pendingInputPrompt.header, _pendingInputPrompt.text);
      }
      _pendingInputPrompt = null;
    }
  }
  if (typeof onDone === 'function') onDone();   // 직렬화 큐 재개
}

/* 피드 라이브 요소(로딩/타이핑) 설정 헬퍼 */
function _setFeedLive(side, html) {
  const feed = document.getElementById('debateFeed');
  if (html) {
    if (!_feedLiveEl) _feedLiveEl = document.createElement('div');
    _feedLiveEl.innerHTML = html;
    _feedLiveSide = side;
    if (feed && !_feedLiveEl.parentNode) feed.appendChild(_feedLiveEl);
    if (feed) feed.scrollTop = feed.scrollHeight;
  } else {
    if (_feedLiveEl && _feedLiveEl.parentNode) _feedLiveEl.remove();
    _feedLiveEl = null;
    _feedLiveSide = null;
  }
}

function _loadingBubble(side, opts) {
  const _isAnswerLoading = !!(opts && opts.isAnswer);
  // 다른 사이드가 현재 타이핑 중이면 큐에 넣고 대기
  const other = side === 'left' ? 'right' : 'left';
  if (_feedLiveEl && _feedLiveSide === other) {
    _pendingFeedAction = () => _loadingBubble(side, opts);
    return;
  }
  // 진행 중 타이핑 취소 후 로딩 표시
  if (_twTimer[side]) { clearTimeout(_twTimer[side]); _twTimer[side] = null; }
  const el = document.getElementById(side === 'left' ? 'leftContent' : 'rightContent');
  const _isPosition = _displayStage === 'position';
  const loadingText = _isAnswerLoading ? '답변 작성 중...'
    : _isPosition ? (side === 'left' ? '찬성 입장 제시 중...' : '반대 입장 제시 중...')
      : (side === 'left' ? '찬성측 주장 제시 중...' : '반대측 주장 제시 중...');
  el.innerHTML = '<div class="bubble-loading"><div class="bubble-spinner"></div> ' + loadingText + '</div>';
  const srcBtn = document.getElementById(side === 'left' ? 'leftSourceBtn' : 'rightSourceBtn');
  if (srcBtn) srcBtn.style.display = 'none';
  // 피드에도 로딩 표시 — 질문 답변은 노란색 버블
  const info = _charInfo(side);
  const who = _esc(((info.emoji || '') + ' ' + (info.name || '')).trim());
  const bubbleCls = _isAnswerLoading ? 'feed-bubble feed-bubble-answer' : 'feed-bubble';
  _setFeedLive(side,
    '<div class="feed-msg ' + side + '"><div class="' + bubbleCls + '">'
    + '<div class="feed-who">' + who + '</div>'
    + '<div class="bubble-loading"><div class="bubble-spinner"></div><span> ' + _esc(loadingText) + '</span></div>'
    + '</div></div>'
  );
}

/* 이어하기 복원용 — 타이핑 애니메이션 없이 즉시 표시 */
function _fillBubbleInstant(side, text, sources) {
  _dbg('◆ _fillBubbleInstant(즉시표시):', side, '내용:', (text || '').slice(0, 14));
  if (_twTimer[side]) { clearTimeout(_twTimer[side]); _twTimer[side] = null; }
  const el = document.getElementById(side === 'left' ? 'leftContent' : 'rightContent');
  const ps = text.split('\n').filter(l => l.trim());
  el.innerHTML = ps.map(p => `<p style="margin:0 0 6px;">${_esc(p)}</p>`).join('');
  if (sources && sources.length) {
    if (side === 'left') S.proSources = sources;
    if (side === 'right') S.conSources = sources;
    const btn = document.getElementById(side === 'left' ? 'leftSourceBtn' : 'rightSourceBtn');
    if (btn) btn.style.display = 'flex';
  }
}

/* ── Stage / Turn 표시 ─────────────────────────────────────── */
function _updateStageDisplay() {
  // _displayStage / _displayTurn 사용 — 서버 상태가 아닌 화면 표시 기준
  const stageLabel = STAGE_LABELS[_displayStage] || _displayStage;
  const hasTurns = _displayStage === 'pro_round' || _displayStage === 'con_round';
  const _sb = document.getElementById('stageBadge');
  if (_sb) _sb.textContent = stageLabel;
  const _cb = document.getElementById('countBadge');
  if (_cb) _cb.textContent = hasTurns ? `${_displayTurn} / 3 턴` : '—';

  // 라운드 이동 바 하이라이트 (입장0 / 찬성1-3 / 반대4-6 / 주장다지기7)
  const _rmap = { position: 0, pro_round: _displayTurn, con_round: 3 + _displayTurn, summary: 7, done: 7 };
  const _ridx = _rmap[_displayStage];
  // question_ans 등 매핑 없는 stage는 nav 업데이트 스킵 (이전 상태 유지)
  if (_ridx != null) {
    document.querySelectorAll('#roundNav .round-btn').forEach((b, i) => {
      b.classList.toggle('active', i === _ridx);
      b.classList.toggle('visited', i < _ridx);
      b.classList.toggle('future', i > _ridx);
    });
  }
}

/* 라운드 바 버튼 클릭 → 피드에서 해당 라운드 위치로 스크롤 점프 */
window._jumpToRound = function (stage, turn) {
  const id = (stage === 'pro_round' || stage === 'con_round') ? 'feed-' + stage + '-' + (turn || 1) : 'feed-' + stage;
  const feed = document.getElementById('debateFeed');
  const a = document.getElementById(id);
  if (feed && a) {
    _progScroll = true;
    feed.scrollTop = Math.max(0, a.offsetTop - 6);
    requestAnimationFrame(() => { _progScroll = false; });
    // 최신 섹션으로 점프 → 자동 추적 재개 / 이전 섹션 → 읽는 동안 자동 스크롤 멈춤
    _feedAutoScroll = (a === feed.lastElementChild);
    _displayStage = stage; _displayTurn = turn || 1;
    _updateStageDisplay();
  }
};

/* (비활성화됨) 폴리 안내 중에도 채팅을 가리지 않음 — 발언이 항상 보이게.
   라운드 버튼(입장 등)을 누르면 그 단계 내용이 바로 보이도록 하기 위함. */
function _toggleFeedForPolly(pollySpeaking) {
  const f = document.getElementById('debateFeed');
  const b = document.querySelector('.blur-backdrop');
  if (f) f.style.visibility = 'visible';
  if (b) b.style.visibility = 'visible';
}

/* ── 폴리 말풍선 공통 표시 ─────────────────────────────────── */
function _showPollyMessage(header, text) {
  document.getElementById('pollyIntroHeader').textContent = header;
  document.getElementById('pollyIntroText').textContent = text;
  document.getElementById('pollyIntroBubble').style.display = 'block';
  _toggleFeedForPolly(true);
  if (_introTimer) clearTimeout(_introTimer);
  _introTimer = setTimeout(_hidePollyIntro, 8000);
}

/* ── AI vs AI: 라운드 시작 안내 (스테이지 첫 발언 시점에 표시) ── */
function _showPollyIntro(stage) {
  const intro = POLLY_INTROS[stage];
  if (!intro || _introShown.has(stage)) return;  // 이미 표시했으면 스킵
  _introShown.add(stage);
  _showPollyMessage(intro.header, intro.text);
}

function _hidePollyIntro() {
  _warningActive = false;
  if (_introTimer) { clearTimeout(_introTimer); _introTimer = null; }
  const el = document.getElementById('pollyIntroBubble');
  if (el) el.style.display = 'none';
  _toggleFeedForPolly(false);   // 폴리 말 끝 → 블러·채팅 복구
  if (_pendingAutoNext) { _pendingAutoNext = false; _scheduleAutoNext(); }  // 안내 닫힘 → 그때부터 자동 진행 카운트
}
window._hidePollyIntro = _hidePollyIntro;

/* ── AI vs USER: A-type 라운드 시작 안내 (이전 라운드 종료 시점) ── */
function _showAiUserRoundIntro(stage) {
  const key = 'aiuser_' + stage;
  if (_introShown.has(key)) return;
  _introShown.add(key);

  const userName = (window._DEBATE_CONFIG && window._DEBATE_CONFIG.userName) || '여러분';

  if (stage === 'summary') {
    const intro = AIUSER_ROUND_INTROS.summary;
    _showPollyMessage(intro.header, intro.text);
    return;
  }

  if (stage === 'position') {
    const pos = AIUSER_ROUND_INTROS.position;
    if (S.difficulty === 'easy') {
      _showPollyMessage(pos.easy.header, pos.easy.text(userName));
    } else {
      const sub = pos.hard[S.stance] || pos.hard.pro;
      _showPollyMessage(sub.header, sub.text());
    }
    return;
  }

  // pro_round or con_round
  const roundIntros = AIUSER_ROUND_INTROS[stage];
  if (roundIntros && roundIntros[S.stance]) {
    const intro = roundIntros[S.stance];
    _showPollyMessage(intro.header, intro.text);
  }
}

/* ── AI vs USER: B-type 입력 유도 프롬프트 계산 ─────────────────── */
function _computeInputPrompt() {
  const stage = S.currentStage;
  const turn = S.currentTurn;
  const tIdx = Math.max(0, Math.min(turn - 1, 2));  // 0|1|2 (배열 인덱스)
  const stance = S.stance;

  if (stage === 'position') {
    const p = AIUSER_INPUT_PROMPTS.position;
    return { header: p.header, text: p[stance] || p.pro };
  }

  if (stage === 'pro_round') {
    const p = AIUSER_INPUT_PROMPTS.pro_round;
    if (stance === 'pro') {
      // 선공: argument(첫 발언) 또는 response(재반박)
      if (!_lastAiMsgType || _lastAiMsgType === 'argument') {
        return { header: p.header, text: p.pro.argument[tIdx] };
      }
      return { header: p.header, text: p.pro.response[tIdx] };
    } else {  // stance === 'con'
      // 후공: 반박(rebuttal)
      return { header: p.header, text: p.con.rebuttal[tIdx] };
    }
  }

  if (stage === 'con_round') {
    const p = AIUSER_INPUT_PROMPTS.con_round;
    if (stance === 'con') {
      // 선공: argument(첫 발언) 또는 response(재반박)
      if (!_lastAiMsgType || _lastAiMsgType === 'argument') {
        return { header: p.header, text: p.con.argument[tIdx] };
      }
      return { header: p.header, text: p.con.response[tIdx] };
    } else {  // stance === 'pro'
      // 후공: 반박(rebuttal)
      return { header: p.header, text: p.pro.rebuttal[tIdx] };
    }
  }

  return null;
}

/* ── 주제 이탈·금칙어 경고 (서버 warning SSE) ──────────────── */
function _showUserWarning(msg) {
  _warningActive = true;
  document.getElementById('pollyIntroHeader').textContent = '⚠️ 입력 내용을 확인해주세요';
  document.getElementById('pollyIntroText').textContent =
    msg || '토론 주제와 관련된 내용을 입력해주세요.';
  document.getElementById('pollyIntroBubble').style.display = 'block';
  if (_introTimer) clearTimeout(_introTimer);
  _introTimer = setTimeout(_hidePollyIntro, 8000);
}

/* ── 유저 입력 글자 수 카운터 ───────────────────────────────── */
function _updateUserInputCount() {
  const len = document.getElementById('userInputField').value.length;
  const el = document.getElementById('charCounter');
  if (!el) return;
  el.textContent = `${len} / 500`;
  el.style.color = len >= 500 ? '#e25c5c' : len >= 400 ? '#e8973a' : '#b0bcb5';
}
window._updateUserInputCount = _updateUserInputCount;

/* ── textarea 자동 높이 확장 ─────────────────────────────────── */
function _autoResizeInput(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}
window._autoResizeInput = _autoResizeInput;

/* ── Loading 버블 여부 체크 ────────────────────────────────── */
function _isShowingLoading(side) {
  const el = document.getElementById(side === 'left' ? 'leftContent' : 'rightContent');
  return el && el.querySelector('.bubble-loading') !== null;
}

/* ── Action bar ────────────────────────────────────────────── */
function _showActionBar(ev) {
  if (ev.wait_type === 'user_turn') {
    // 유저 차례: 유저 쪽 버블에 로딩이 남아있으면 제거 (sendAction → _loadingBubble 잔재)
    const userSide = S.stance === 'pro' ? 'left' : 'right';
    if (_isShowingLoading(userSide)) _clearBubble(userSide);
    document.getElementById('userTurnBar').style.display = 'flex';
    const _bn0 = document.getElementById('btnNextSide'); if (_bn0) _bn0.style.display = 'none';
    _feedGenerating = false;
    _setFeedLive(null);   // 어느 쪽이든 라이브 요소 제거 (user_turn = AI 발언 완료)
    renderFeed();

    if (S.mode === 'aiuser') {
      // B-type 입력 유도 프롬프트: AI 애니메이션 중이면 완료 후 표시, 아니면 즉시 표시
      const promptData = _computeInputPrompt();
      if (promptData) {
        const aiSide = S.stance === 'pro' ? 'right' : 'left';
        if (_twTimer[aiSide]) {
          // AI 애니메이션 진행 중 → 완료 후 표시 (tick() 완료 시점에서 처리)
          _pendingInputPrompt = promptData;
        } else if (!_warningActive) {
          // 경고 표시 중이 아닐 때만 B-type 프롬프트 표시 (경고 덮어쓰기 방지)
          _showPollyMessage(promptData.header, promptData.text);
        }
      }
    } else {
      // AI vs AI: 폴리 인트로 닫기
      _hidePollyIntro();
    }
    return;
  }

  // user_choice: 폴리 인트로 닫기 (공통)
  _hidePollyIntro();

  // user_choice: AI vs User 모드에서 유저 response 후 AI 쪽 로딩이 남았으면 제거
  if (S.mode === 'aiuser') {
    const aiSide = S.stance === 'pro' ? 'right' : 'left';
    if (_isShowingLoading(aiSide)) _clearBubble(aiSide);
  }
  // user_choice: show action buttons
  _actionInProgress = false;  // waiting 이벤트 = 턴 완료 → 다음 액션 허용
  const _bn = document.getElementById('btnNextSide'); if (_bn) _bn.style.display = 'inline-flex';  // 수동 '다음 단계' 노출

  // ★ 자동 진행: 폴리 안내가 떠 있으면 닫힌 뒤부터 카운트 (안내 중엔 채팅이 숨겨져 있으므로)
  _armAutoNext();

  const bar = document.getElementById('actionBar');
  bar.style.display = 'flex';
  _extraRemaining = (ev.extra_remaining != null)
    ? ev.extra_remaining
    : (ev.extra_available ? 1 : 0);
  const btnExtra = document.getElementById('btnExtra');
  btnExtra.textContent = `💬 추가 토론 (${_extraRemaining})`;
  btnExtra.classList.toggle('btn-extra-exhausted', _extraRemaining <= 0);

  // PHASE 3: 입장제시 게이트 — '다음 단계'만 표시, 추가토론·질문 숨김
  if (ev.gate === 'position_done') {
    btnExtra.style.display = 'none';
    document.getElementById('btnAskPro').style.display = 'none';
    document.getElementById('btnAskCon').style.display = 'none';
    return;
  }

  // 일반 user_choice: 버튼 복구 (이전 position_done 게이트 후 복구용)
  btnExtra.style.display = '';

  if (S.mode === 'aiuser') {
    // AI vs User: 질문 버튼은 항상 숨김(유저가 직접 발언)
    document.getElementById('btnAskPro').style.display = 'none';
    document.getElementById('btnAskCon').style.display = 'none';
  } else {
    // ★ AI vs AI: 게이트에서 숨겼던 질문 버튼을 다시 표시
    document.getElementById('btnAskPro').style.display = 'flex';
    document.getElementById('btnAskCon').style.display = 'flex';
  }
}

function _hideActionBar() {
  _clearAutoNext();
  const _bn = document.getElementById('btnNextSide'); if (_bn) _bn.style.display = 'none';
  document.getElementById('actionBar').style.display = 'none';
  document.getElementById('userTurnBar').style.display = 'none';
  // 다음 턴 시작 시 폴리 버블 숨기고 말풍선 visibility 복구
  const b = document.getElementById('pollyQBubble');
  if (b) b.style.display = 'none';
  document.getElementById('leftBubble').style.visibility = 'visible';
  document.getElementById('rightBubble').style.visibility = 'visible';
  // 인트로 말풍선은 새 라운드 시작 전까지 유지 (닫지 않음)
}

/* ── Extra debate button handler ───────────────────────────── */
function handleExtraClick() {
  _clearAutoNext();              // 추가토론 선택 → 자동 진행 멈춤
  if (_extraRemaining <= 0) {
    // 추가토론 소진 → 폴리 안내 메시지 표시
    document.getElementById('pollyIntroHeader').textContent = '추가 토론을 모두 사용했어요!';
    document.getElementById('pollyIntroText').textContent = '이번 라운드에서 추가 토론은 더 이상 불가능해요.\n다음 단계로 넘어가거나, 찬이·반이에게 질문해보세요 😊';
    document.getElementById('pollyIntroBubble').style.display = 'block';
    if (_introTimer) clearTimeout(_introTimer);
    _introTimer = setTimeout(_hidePollyIntro, 8000);
    return;
  }
  sendAction('extra');
}

/* ── Summary card (mid-debate) ─────────────────────────────── */
function _showSummaryCard(text) {
  const card = document.getElementById('summaryCard');
  const body = document.getElementById('summaryBody');
  const lines = text.split('\n').filter(l => l.trim());
  body.innerHTML = lines.map(l => `<p style="margin:0 0 7px;">${_esc(l)}</p>`).join('');
  card.style.display = 'block';
}

/* ══════════════════════════════════════════════════════════════
   ACTIONS
══════════════════════════════════════════════════════════════ */
async function sendAction(action) {
  if (_actionInProgress) return;  // 중복 호출 방지
  _actionInProgress = true;
  _clearAutoNext();               // 진행 시작 → 대기 중 자동 타이머 취소
  _expectingAI = true;
  _feedGenerating = true; renderFeed();   // 클릭 즉시 '주장 제시 중…' 표시

  _sectionId++;                                               // 새 턴/추가토론 시작 → 새 섹션
  _sectionMeta[_sectionId] = { isExtra: action === 'extra' }; // extra vs regular 기록
  _hideActionBar();
  // 큐 초기화 후 양쪽 버블 즉시 비움 (큐 시스템 우회 — SSE 이벤트가 실제 로딩 표시)
  _pendingFeedAction = null;
  _setFeedLive(null);
  _clearBubble('left');
  _clearBubble('right');
  document.getElementById('summaryCard').style.display = 'none';

  // 진행 중 재접속: 서버 state 스냅샷으로 과거 메시지를 다시 그리지 않도록
  _skipStateRestore = true;
  S._isFreshDebate = false;

  // 추가 토론(extra): round_update가 같은 stage로 와도 턴 증가 금지 + 상대측 선공으로 처리하도록 표시
  _pendingExtra = (action === 'extra');

  // ── 다음 단계: 다음 stage/turn을 예측해 배지·폴리를 '발언 전에' 즉시 표시 ──
  // (S.currentStage/currentTurn은 건드리지 않음 — 서버 round_update가 단일 진실원으로 갱신)
  if (action === 'next') {
    const _st = S.currentStage, _tn = S.currentTurn;
    let _nSt = _st, _nTn = _tn;
    if (_st === 'position') { _nSt = 'pro_round'; _nTn = 1; }   // PHASE 3: 입장제시 게이트
    else if (_tn < 3) { _nTn = _tn + 1; }      // 같은 라운드 다음 턴
    else if (_st === 'pro_round') { _nSt = 'con_round'; _nTn = 1; }
    else if (_st === 'con_round') { _nSt = 'summary'; _nTn = 1; }
    _displayStage = _nSt;
    _displayTurn = _nTn;
    _updateStageDisplay();                                     // 배지 즉시 갱신
    if (_nSt !== _st) {                                        // 새 단계 진입 → 폴리 안내 즉시 표시
      if (S.mode === 'aiuser') _showAiUserRoundIntro(_nSt);
      else _showPollyIntro(_nSt);
    }
    // ── 피드에 새 단계 섹션 즉시 표시 (메시지 도착 전 예비 헤더 + 로딩) ──
    _pendingSection = { stage: _nSt, turn: _nTn };
    renderFeed();                              // 예비 섹션 헤더를 피드에 즉시 삽입
    // 새 단계 첫 발언 측 로딩 표시 (pro_round/same stage → 왼쪽, con_round → 오른쪽, summary → 바로 최종카드)
    const _firstSide = (_nSt === 'con_round') ? 'right' : 'left';
    if (_nSt !== 'summary') _loadingBubble(_firstSide);
  } else if (action === 'extra') {
    // 추가 토론: 배지(턴 번호)는 그대로. 상대측(opp)이 먼저 반박하므로 그쪽에 로딩 표시.
    if (S.currentStage === 'pro_round' || S.currentStage === 'con_round') {
      const _rl = ROUND_LEADER[S.currentStage];
      const _opp = _rl === 'left' ? 'right' : 'left';
      _clearBubble(_rl);
      _loadingBubble(_opp);
    }
  }

  if (MOCK_MODE) {
    S.mockRound = (S.mockRound || 0) + 1;
    setTimeout(() => _mockRound(S.mockRound), 400);
    return;
  }

  try {
    await fetch(`/api/debates/${S.sessionId}/action/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _getCsrf() },
      body: JSON.stringify({ user_action: action }),
    });
    _startSSE();
  } catch (e) {
    console.error(e);
    _actionInProgress = false;  // 에러 시 플래그 해제
  }
}

async function submitUserInput() {
  const text = document.getElementById('userInputField').value.trim();
  if (!text) return;
  if (text.length > 500) return;

  // 전체 영어(한글 0개) 차단 — 일부 영어는 허용
  if (!/[가-힣ㄱ-ㅎㅏ-ㅣ]/.test(text)) {
    _showUserWarning('한국어를 포함해서 입력해주세요. 한국어 외 언어가 많아질 경우 답변 품질이 떨어질 수 있습니다.');
    return;
  }

  const inputEl = document.getElementById('userInputField');
  inputEl.value = '';
  inputEl.style.height = 'auto';   // 높이 초기화
  const cntEl = document.getElementById('charCounter');   // 실제 표시 카운터 id (userInputCount 아님)
  if (cntEl) { cntEl.textContent = '0 / 500'; cntEl.style.color = '#b0bcb5'; }
  document.getElementById('userTurnBar').style.display = 'none';

  if (MOCK_MODE) {
    const userSide = S.stance === 'pro' ? 'left' : 'right';
    const aiSide = S.stance === 'pro' ? 'right' : 'left';
    _fillBubble(userSide, text, []);
    _loadingBubble(aiSide);
    const rd = MOCK_DATA.rounds[Math.min(S.mockRound, MOCK_DATA.rounds.length - 1)];
    setTimeout(() => {
      _fillBubble(aiSide, S.stance === 'pro' ? rd.con : rd.pro, []);
      setTimeout(() => {
        S.mockRound = (S.mockRound || 0) + 1;
        if (S.mockRound >= MOCK_DATA.rounds.length) {
          _showFinal(MOCK_DATA.final);
        } else {
          _showActionBar({ wait_type: 'user_choice', extra_available: true });
        }
      }, 500);
    }, 900);
    return;
  }

  // 유저 텍스트 즉시 표시 (SSE 에코 전), AI 쪽만 로딩
  const _uSide = S.stance === 'pro' ? 'left' : 'right';
  const _aSide = S.stance === 'pro' ? 'right' : 'left';
  const _uEl = document.getElementById(_uSide === 'left' ? 'leftContent' : 'rightContent');
  _uEl.innerHTML = text.split('\n').filter(l => l.trim())
    .map(l => `<p style="margin:0 0 6px;">${_esc(l)}</p>`).join('');

  // 유저 발언을 피드에 즉시 현출 (서버 에코 기다리지 않음). 동일 내용 에코는 _renderMessageSerial에서 건너뜀
  const _uInfo = _charInfo(_uSide);
  _messages.push({
    side: _uSide, name: _uInfo.name, emoji: _uInfo.emoji, label: _uInfo.label, av: _uInfo.av,
    text, sources: [], stage: S.currentStage, turn: S.currentTurn, sectionId: _sectionId
  });
  _userEchoText = text;
  _feedGenerating = false;        // 제출 직후엔 유저 발언만 현출, '주장 제시 중'은 안 띄움
  _expectingAI = true;            // 다음 스트림 generation_start부터 AI 응답 '주장 제시 중' 표시
  renderFeed();

  // 배지 갱신: 유저가 선공 측일 때 (예: user=pro가 찬성라운드에서 발언 시작)
  if (ROUND_LEADER[S.currentStage] === _uSide) {
    _displayStage = S.currentStage;
    _displayTurn = S.currentTurn;
    // AI vs USER에서는 인트로를 표시하지 않음 — round_update / startDebate 에서 처리
    _updateStageDisplay();
  }

  // 유저 제출 시 대기 중인 B-type 프롬프트 초기화
  _pendingInputPrompt = null;
  _loadingBubble(_aSide);
  // 진행 중 재접속: 서버 state 스냅샷으로 과거 메시지를 다시 그리지 않도록
  _skipStateRestore = true;
  S._isFreshDebate = false;
  try {
    await fetch(`/api/debates/${S.sessionId}/input/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _getCsrf() },
      body: JSON.stringify({ user_input: text }),
    });
    _startSSE();
  } catch (e) { console.error(e); }
}

/* ── Ask modal open ─────────────────────────────────────────── */
function openAskModal(target) {
  _clearAutoNext();              // 질문 작성 중에는 자동 진행 멈춤
  S.currentAskTarget = target;
  const name = target === 'pro' ? '찬이' : '반이';
  document.getElementById('askModalTitle').textContent = `🤖 ${name}에게 질문`;
  const ta = document.getElementById('askTextarea');
  if (ta) { ta.value = ''; }
  document.getElementById('askCharCount').textContent = '0 / 500';
  document.getElementById('askCharCount').classList.remove('over');
  const warn = document.getElementById('askWarnMsg');
  if (warn) { warn.style.display = 'none'; warn.textContent = ''; }
  bootstrap.Modal.getOrCreateInstance(document.getElementById('askModal')).show();
}

/* ── Char counter ────────────────────────────────────────────── */
function updateAskCharCount() {
  const ta = document.getElementById('askTextarea');
  const cnt = document.getElementById('askCharCount');
  const len = ta.value.length;
  cnt.textContent = `${len} / 500`;
  cnt.classList.toggle('over', len > 500);
}

/* ── Submit question ─────────────────────────────────────────── */
async function submitQuestion() {
  const ta = document.getElementById('askTextarea');
  const text = ta ? ta.value.trim() : '';
  const warn = document.getElementById('askWarnMsg');

  if (!text) {
    if (warn) { warn.textContent = '질문을 입력해주세요.'; warn.style.display = 'block'; }
    return;
  }
  if (text.length > 500) {
    if (warn) { warn.textContent = '500자 이내로 입력해주세요.'; warn.style.display = 'block'; }
    return;
  }

  // 전체 영어(한글 0개) 차단 — 일부 영어는 허용
  if (!/[가-힣ㄱ-ㅎㅏ-ㅣ]/.test(text)) {
    if (warn) { warn.textContent = '한국어를 포함해서 입력해주세요. 한국어 외 언어가 많아질 경우 답변 품질이 떨어질 수 있습니다.'; warn.style.display = 'block'; }
    return;
  }

  // 모달 닫기 (제출로 닫음 → 취소 닫힘과 구분)
  _askSubmitted = true;
  bootstrap.Modal.getInstance(document.getElementById('askModal'))?.hide();
  if (ta) ta.value = '';

  // 폴리 질문 말풍선 표시 → 2.5초 후 자동 닫기
  const bubble = document.getElementById('pollyQBubble');
  const qText = document.getElementById('pollyQText');
  if (bubble && qText) {
    qText.textContent = text;
    bubble.style.display = 'block';
    setTimeout(() => { bubble.style.display = 'none'; }, 2500);
  }

  // 기록에 질문 추가
  addQuestionToRecord(text);

  // 질문 답변 대기 중 로딩 표시 (노란색 + '답변 작성 중...')
  // addQuestionToRecord가 _sectionId를 이미 증가시킴 → 동일 sectionId로 answer도 기록됨
  const answerSide = S.currentAskTarget === 'pro' ? 'left' : 'right';
  _loadingBubble(answerSide, { isAnswer: true });

  if (MOCK_MODE) {
    await new Promise(r => setTimeout(r, 1200));
    const mockAnswer = S.currentAskTarget === 'pro'
      ? `재정 부담은 분명 고려해야 할 사항이지만, 청년 주거 불안정으로 인한 사회적 비용이 더 큽니다. 연구에 따르면 주거 안정이 보장된 청년은 취업률과 출산율이 모두 높아지며, 이는 장기적으로 세수 증가로 이어져 지원 비용을 상쇄할 수 있습니다.`
      : `재정 부담 문제는 정책의 지속 가능성과 직결됩니다. 월세 지원 확대에 필요한 연간 3~5조 원을 충당하려면 다른 복지 예산을 줄이거나 세금을 올려야 하는데, 이는 또 다른 계층의 부담으로 이어질 수 있어요. 공공임대 확충이 더 근본적인 해결책입니다.`;
    _fillBubble(answerSide, mockAnswer, []);
    return;
  }

  try {
    const res = await fetch(`/api/debates/${S.sessionId}/question/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _getCsrf() },
      body: JSON.stringify({ user_input: text, question_target: S.currentAskTarget }),
    });
    const data = await res.json();
    _dbg('[질문 API 응답]', JSON.stringify(data).slice(0, 120));

    if (data.error === 'profanity_detected') {
      if (typeof window.showProfanityModal === 'function') window.showProfanityModal(data.foul_count);
      _setFeedLive(null); return;
    }
    if (data.error === 'off_topic') {
      _showUserWarning(data.message);
      _setFeedLive(null); return;
    }
    // content 필드 다양한 키 대응
    const content = data.content || data.answer || data.response || data.text || '';
    const participant = data.participant || (S.currentAskTarget === 'pro' ? 'pro' : 'con');
    const side = participant === 'pro' ? 'left' : 'right';
    if (content) {
      _fillBubble(side, content, data.sources || [], null,
        { isAnswer: true, capturedStage: 'question_ans' });
    } else {
      _dbg('[질문 API] content 없음:', data);
      _setFeedLive(null);
    }
  } catch (e) {
    console.error('[질문 API 오류]', e);
    _setFeedLive(null);
  }
}

/* ── Add question card to record ─────────────────────────────── */
function addQuestionToRecord(text) {
  // stage를 'question_ans'로만 바꾸면 sectionId가 같아도 별도 섹션이 생김 (_sectionId 증가 불필요)
  _messages.push({ type: 'question', target: S.currentAskTarget, text, stage: 'question_ans', sectionId: _sectionId });
  _renderRecords();
  // 질문이 추가된 위치로 피드 스크롤
  const feed = document.getElementById('debateFeed');
  if (feed) {
    const qs = feed.querySelectorAll('.feed-q');
    const lastQ = qs[qs.length - 1];
    if (lastQ) lastQ.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/* ── setAskTarget (legacy — kept for compatibility) ──────────── */
function setAskTarget(target) {
  S.currentAskTarget = target;
  const name = target === 'pro' ? '찬이' : '반이';
  document.getElementById('askModalTitle').textContent = `🤖 ${name}에게 질문`;
}


/* ── Source modal ──────────────────────────────────────────── */
function openSource(side) {
  _renderSourceModal(side === 'pro' ? S.proSources : S.conSources);
}
window._openFeedSource = function (i) { _renderSourceModal(_feedSrc[i] || []); };

function _renderSourceModal(sources) {
  const body = document.getElementById('sourceModalBody');
  if (!sources || sources.length === 0) {
    body.innerHTML = '<p style="color:#8a948c;">출처 정보가 없습니다.</p>';
    return;
  }
  body.innerHTML = sources.map(s => {
    const meta = s.metadata || {};
    const title = meta.title || meta.publisher || '원문';
    const date = meta.date || '';
    const url = meta.source_url || meta.url || '';
    return `
      <div style="margin-bottom:16px;">
        ${date ? `<div style="font-size:12px;color:#8a948c;margin-bottom:4px;">${_esc(date)}</div>` : ''}
        <div style="font-weight:700;color:#2b3a36;margin-bottom:8px;">${_esc(title)}</div>
        ${url ? `<a href="${_esc(url)}" target="_blank" rel="noopener"
                    style="color:#2f8f6b;font-size:14px;">참고 출처</a>` : ''}
      </div>`;
  }).join('<hr style="border-color:#eee;">');
}

/* ══════════════════════════════════════════════════════════════
   FINAL VIEW
══════════════════════════════════════════════════════════════ */
let _finalShown = false;   // 주장 다지기(요약) 화면이 한 번이라도 떴는지 — 재열기 버튼 활성 조건
function _showFinal(data) {
  // 로딩 버블/타이핑 상태 완전 제거
  _feedGenerating = false;
  _expectingAI = false;
  _setFeedLive(null);
  _finalShown = true;
  // debateView는 블러 배경으로 유지 — 숨기지 않음
  // (interactive 요소는 이미 _hideActionBar로 숨겨짐)
  document.getElementById('finalView').style.display = 'flex';
  const _rfBtn = document.getElementById('reopenFinalBtn');
  if (_rfBtn) _rfBtn.style.display = 'none';   // 오버레이가 떠 있으면 재열기 버튼 숨김
  const _cfBtn2 = document.getElementById('closeFinalBtn2');
  if (_cfBtn2) _cfBtn2.style.display = '';

  // Titles
  if (S.mode === 'aiuser') {
    document.getElementById('finalLeftTitle').textContent =
      S.stance === 'pro' ? '내 핵심 주장' : '찬이의 핵심 주장';
    document.getElementById('finalRightTitle').textContent =
      S.stance === 'con' ? '내 핵심 주장' : '반이의 핵심 주장';
  }

  // Populate lists
  const p = data.pro_summary || {};
  const c = data.con_summary || {};
  _fillList('finalProKey', p.key_arguments);
  _fillList('finalConKey', c.key_arguments);

  // 좌하(fb-bl): AI vs User는 user_feedback, AI vs AI는 상호 반박
  const uf = data.user_feedback || {};
  if (S.mode === 'aiuser' && (uf.strong_points || uf.weak_points)) {
    const area = document.getElementById('finalRebuttalArea');
    const strongItems = (uf.strong_points || []).map(t => `<li style="margin-bottom:4px;">${_esc(t)}</li>`).join('');
    const weakItems = (uf.weak_points || []).map(t => `<li style="margin-bottom:4px;">${_esc(t)}</li>`).join('');
    area.innerHTML = `
      <div class="f4-rebuttal">
        <span class="f4-rb-tag f4-rb-pro">💪 효과적인 반박</span>
        <ul style="margin:4px 0 0 0;padding-left:16px;">${strongItems || '<li style="color:#8a948c;">-</li>'}</ul>
      </div>
      <div class="f4-rebuttal" style="margin-top:8px;">
        <span class="f4-rb-tag f4-rb-con">📌 보완이 필요한 부분</span>
        <ul style="margin:4px 0 0 0;padding-left:16px;">${weakItems || '<li style="color:#8a948c;">-</li>'}</ul>
      </div>`;
  } else {
    _fillList('finalGoodRebuttal', p.key_rebuttals || data.good_rebuttal);
    _fillList('finalNeedImprove', c.key_rebuttals || data.need_improve);
  }

  // Record this debate session
  _addRecord();
}

function _fillList(id, items) {
  document.getElementById(id).innerHTML =
    (items || []).map(t => `<li style="margin-bottom:4px;">${_esc(t)}</li>`).join('');
}

/* ══════════════════════════════════════════════════════════════
   RESTART
══════════════════════════════════════════════════════════════ */
function restartDebate() {
  if (_sse) _sse.close();

  // Reset state
  S.sessionId = null;
  S.currentStage = 'position';
  S.currentTurn = 1;
  S.mockRound = 0;
  _introShown.clear();
  _hidePollyIntro();
  _sideStage.left = null;
  _sideStage.right = null;
  _messages.length = 0;
  _sectionId = 0;
  _actionInProgress = false;
  _extraRemaining = 2;
  _displayStage = 'position';
  _displayTurn = 1;
  _lastAiMsgType = null;
  _pendingInputPrompt = null;
  _isReplaying = false;
  _isDisplayingState = false;
  _replayQueue = [];
  _posSources = { left: [], right: [] };
  _pendingConPosition = null;
  _awaitingConPosFlush = false;
  S._isFreshDebate = false;
  for (const k in _sectionMeta) delete _sectionMeta[k];
  _sectionMeta[0] = { isExtra: false };
  _renderRecords();

  // Reset UI
  _finalShown = false;
  const _rfBtn = document.getElementById('reopenFinalBtn');
  if (_rfBtn) _rfBtn.style.display = 'none';
  document.getElementById('finalView').style.display = 'none';
  document.getElementById('debateView').style.display = 'block';
  document.getElementById('userBadges').style.display = 'none';
  document.getElementById('summaryCard').style.display = 'none';

  // Reset characters (src + class 리셋 — chani는 별도 클래스로 발 위치 보정)
  const _rlc = document.getElementById('leftChar');
  const _rlcf = document.getElementById('leftCharFinal');
  _rlc.src = IMG.chani; _rlc.className = 'stage-char stage-char-chani';
  _rlcf.src = IMG.chani; _rlcf.className = 'stage-char-final stage-char-chani-final';
  const _rrc = document.getElementById('rightChar');
  const _rrcf = document.getElementById('rightCharFinal');
  _rrc.src = IMG.bani; _rrc.className = 'stage-char stage-char-right';
  _rrcf.src = IMG.bani; _rrcf.className = 'stage-char-final stage-char-right-final';

  // Show both ask buttons
  document.getElementById('btnAskPro').style.display = 'flex';
  document.getElementById('btnAskCon').style.display = 'flex';

  _loadingBubble('left');
  _loadingBubble('right');
  _hideActionBar();
  _updateStageDisplay();

  document.getElementById('introOverlay').style.display = 'flex';
}

/* ══════════════════════════════════════════════════════════════
   MEMO PANEL
══════════════════════════════════════════════════════════════ */
function openMemo() {
  if (S.memoOpen) { closeMemo(); return; }
  S.memoOpen = true;
  document.getElementById('memoPanel').classList.add('open');
  document.getElementById('debateApp')?.classList.add('memo-open');  // 스테이지를 패널만큼 왼쪽으로 비킴
}
function closeMemo() {
  S.memoOpen = false;
  document.getElementById('memoPanel').classList.remove('open');
  document.getElementById('debateApp')?.classList.remove('memo-open');
}

/* ── 메모 글자수 카운터 ─────────────────────────── */
function updateMemoCount(textareaId, counterId) {
  const ta = document.getElementById(textareaId);
  const cnt = document.getElementById(counterId);
  if (!ta || !cnt) return;
  const len = ta.value.length;
  cnt.textContent = `${len} / 500`;
  cnt.classList.toggle('over', len >= 500);
}
window.updateMemoCount = updateMemoCount;

// Persist memo to localStorage + 초기 글자수 표시
// 키에 card_id 또는 session을 포함시켜 토론별로 메모 분리
const _memoMap = {
  memoProText: 'memoProCount',
  memoConText: 'memoConCount',
  memoCheckText: 'memoCheckCount',
};

function _getMemoNamespace() {
  const p = new URLSearchParams(window.location.search);
  if (p.get('card_id')) return 'card_' + p.get('card_id');
  if (p.get('session')) return 'session_' + p.get('session');
  return 'default';
}

function _initMemo() {
  const ns = _getMemoNamespace();
  Object.entries(_memoMap).forEach(([id, countId]) => {
    const el = document.getElementById(id);
    if (!el) return;
    try {
      const saved = localStorage.getItem('policity_memo_' + ns + '_' + id);
      el.value = saved || '';
      updateMemoCount(id, countId);
    } catch (_) { }
    el.addEventListener('input', () => {
      try { localStorage.setItem('policity_memo_' + ns + '_' + id, el.value); } catch (_) { }
    });
  });
}
_initMemo();

/* ══════════════════════════════════════════════════════════════
   DEBATE RECORDS (session-local)
══════════════════════════════════════════════════════════════ */
const _records = [];

function _addRecord() {
  const modeTag = S.mode === 'aiai' ? 'AI vs AI · 관전' : `AI vs USER · ${S.stance === 'pro' ? '찬성' : '반대'}`;
  const now = new Date();
  const dateStr = `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')}`;
  _records.unshift({
    icon: S.mode === 'aiai' ? '🐶' : (S.stance === 'pro' ? '🙋' : '✋'),
    title: document.getElementById('topicLabel').textContent,
    meta: `${modeTag} · ${dateStr}`,
    tag: S.mode === 'aiai' ? '관전' : '참여',
    color: S.mode === 'aiai' ? '#1f7a52' : '#3b6bd6',
    bg: S.mode === 'aiai' ? '#eaf6ee' : '#e9f0fc',
  });
  _renderRecords();
}

/* ── 카카오톡 스타일 스크롤 피드 (프로토타입) — _messages로부터 렌더 ── */
function renderFeed() {
  const feed = document.getElementById('debateFeed');
  if (!feed) return;
  _feedSrc = [];   // 말풍선별 원문(sources) 레지스트리 재구성
  if (_messages.length === 0) { feed.innerHTML = _feedGenerating ? _FEED_LOADING : ''; return; }
  const sections = [];
  let cur = null;
  _messages.forEach(m => {
    if (!cur || cur.id !== m.sectionId || cur.stage !== m.stage) {
      cur = { id: m.sectionId, stage: m.stage, messages: [] }; sections.push(cur);
    }
    cur.messages.push(m);
  });
  let html = '';
  sections.forEach(sec => {
    const turn = (sec.messages.find(m => m.turn != null) || {}).turn || 1;
    let label = STAGE_LABELS[sec.stage] || sec.stage || '입장 제시';
    let anchor = 'feed-' + sec.stage;
    if (sec.stage === 'pro_round') { label = '찬성 세부주장 ' + turn; anchor += '-' + turn; }
    else if (sec.stage === 'con_round') { label = '반대 세부주장 ' + turn; anchor += '-' + turn; }
    html += '<div class="feed-turn" id="' + anchor + '"><div class="feed-sep">' + _esc(label) + '</div>';
    sec.messages.forEach(m => {
      if (m.type === 'question') {
        html += '<div class="feed-q">💬 ' + _esc(m.target === 'pro' ? '찬이' : '반이') + '에게 질문 — ' + _esc(m.text) + '</div>';
        return;
      }
      const side = m.side === 'right' ? 'right' : 'left';
      const body = m.text.split('\n\n').map(para => {
        const lines = para.split('\n').filter(l => l.trim()).map(l => _esc(l)).join('<br>');
        return lines ? '<p style="margin:0 0 10px;padding:0;">' + lines + '</p>' : '';
      }).filter(Boolean).join('');
      let srcBtn = '';
      if (m.sources && m.sources.length) {
        _feedSrc.push(m.sources);
        const si = _feedSrc.length - 1;
        srcBtn = '<button type="button" class="feed-src-btn" data-bs-toggle="modal" data-bs-target="#sourceModal" onclick="_openFeedSource(' + si + ')">참고 출처</button>';
      }
      const bubbleCls = 'feed-bubble' + (m.isAnswer ? ' feed-bubble-answer' : '');
      html += '<div class="feed-msg ' + side + '"><div class="' + bubbleCls + '"><div class="feed-who">'
        + _esc(((m.emoji || '') + ' ' + (m.name || '')).trim()) + '</div>' + body + srcBtn + '</div></div>';
    });
    html += '</div>';
  });
  // ── 예비 섹션: 메시지 도착 전 다음 단계 헤더를 미리 표시 ──
  if (_pendingSection) {
    const { stage: ps, turn: pt } = _pendingSection;
    const alreadyCovered = sections.some(s => s.stage === ps && s.messages.some(m => m.turn === pt));
    if (!alreadyCovered) {
      let psLabel = STAGE_LABELS[ps] || ps;
      let psAnchor = 'feed-' + ps;
      if (ps === 'pro_round') { psLabel = '찬성 세부주장 ' + pt; psAnchor += '-' + pt; }
      else if (ps === 'con_round') { psLabel = '반대 세부주장 ' + pt; psAnchor += '-' + pt; }
      html += '<div class="feed-turn" id="' + psAnchor + '"><div class="feed-sep">' + _esc(psLabel) + '</div></div>';
    } else {
      _pendingSection = null;  // 실제 메시지 도착 → 예비 섹션 해제
    }
  }
  feed.innerHTML = html;
  // 라이브 타이핑/로딩 요소가 있으면 피드 재빌드 후 재부착
  if (_feedLiveEl) feed.appendChild(_feedLiveEl);
  // 자동 추적 중일 때만 최신 턴을 맨 위로 스크롤. 사용자가 이전 단계를 보고 있으면 건드리지 않음.
  if (_feedAutoScroll) {
    _progScroll = true;
    feed.scrollTop = feed.scrollHeight;
    requestAnimationFrame(() => { _progScroll = false; });
  }
}

function _renderRecords() {
  renderFeed();
  const el = document.getElementById('recordModalBody');
  const sub = document.getElementById('recordModalSub');

  if (_messages.length === 0) {
    el.innerHTML = '<div class="record-empty">아직 토론 기록이 없습니다.</div>';
    return;
  }

  // 헤더 서브타이틀 갱신
  if (sub) {
    const topic = document.getElementById('topicLabel')?.textContent || '';
    const mode = S.mode === 'aiai' ? 'AI vs AI' : 'AI vs USER';
    sub.textContent = `${topic} · ${mode}`;
  }

  // ── sectionId + stage 기준으로 메시지 묶기 ─────────────────
  // ① sectionId가 다르면 항상 새 섹션 (sendAction 경계)
  // ② sectionId가 같아도 stage가 바뀌면 새 섹션
  //    (position 종료 후 pro_round 턴 1이 같은 SSE 세션에서 이어지는 경우 분리)
  const sections = [];
  let curSec = null;
  _messages.forEach(m => {
    const isNew = !curSec || curSec.id !== m.sectionId || curSec.stage !== m.stage;
    if (isNew) {
      curSec = { id: m.sectionId, stage: m.stage, messages: [] };
      sections.push(curSec);
    }
    curSec.messages.push(m);
  });

  // ── 스테이지별 턴 카운터
  //    _sectionMeta[id].isExtra 로 regular / extra 구분
  //    → extra는 별도 카운터로 "추가 N" 라벨 표시
  let proRegular = 0, proExtra = 0;
  let conRegular = 0, conExtra = 0;
  // 현재 섹션 앵커: _sectionId가 같은 섹션이 여럿이면 가장 마지막(question_ans 등)을 기준으로 스크롤
  let currentSecIdx = -1;
  sections.forEach((sec, i) => { if (sec.id === _sectionId) currentSecIdx = i; });

  let html = '';

  sections.forEach((sec, secIdx) => {
    const stageLabel = STAGE_LABELS[sec.stage] || sec.stage || '입장 제시';
    let turnLabel = '';
    const isExtra = (_sectionMeta[sec.id] || {}).isExtra || false;
    if (sec.stage === 'pro_round') {
      if (isExtra) { proExtra++; turnLabel = ` · 추가 ${proExtra}`; }
      else { proRegular++; turnLabel = ` · ${proRegular} / 3 턴`; }
    } else if (sec.stage === 'con_round') {
      if (isExtra) { conExtra++; turnLabel = ` · 추가 ${conExtra}`; }
      else { conRegular++; turnLabel = ` · ${conRegular} / 3 턴`; }
    }

    const isCurrent = (secIdx === currentSecIdx);
    html += `
      <div ${isCurrent ? 'id="record-current-section"' : ''} style="text-align:center;margin:${secIdx > 0 ? '14px' : '4px'} 0 14px;">
        <span style="display:inline-block;background:#eef3ee;color:#6b756e;font-size:12px;font-weight:700;padding:4px 14px;border-radius:20px;">📌 ${_esc(stageLabel + turnLabel)}</span>
      </div>`;

    sec.messages.forEach(m => {
      // ── 질문 카드 ─────────────────────────────────────────────
      if (m.type === 'question') {
        const targetName = m.target === 'pro' ? '찬이' : '반이';
        html += `
          <div style="display:flex;justify-content:center;margin-bottom:14px;">
            <div style="background:#eef0fb;border:1px solid #c8d0f0;border-radius:12px;padding:8px 14px;max-width:88%;font-size:13px;color:#4a5bbf;">
              💬 ${_esc(targetName)}에게 질문 — ${_esc(m.text)}
            </div>
          </div>`;
        return;
      }

      // ── AI 발언 카드 ──────────────────────────────────────────
      const isRight = m.side === 'right';
      const avBorder = isRight ? '#f3d4d0' : '#cfe9d8';
      const bubbleBg = m.isAnswer ? '#fff9ec' : (isRight ? '#fbf3f2' : '#f2f8f3');
      const bubbleBdr = m.isAnswer ? '#f0d87a' : (isRight ? '#f0d6d3' : '#d6ead9');
      const bubbleR = isRight ? '14px 0 14px 14px' : '0 14px 14px 14px';
      const nameColor = isRight ? '#c0392b' : '#1f7a52';
      const flexDir = isRight ? 'flex-direction:row-reverse;' : '';
      const nameAlign = isRight ? 'text-align:right;' : '';

      const nameHtml = isRight
        ? `${_esc(m.name)} ${m.emoji || ''} <span style="color:#a0aaa4;font-weight:400;">${_esc(m.label)} ·</span>`
        : `${m.emoji || ''} ${_esc(m.name)} <span style="color:#a0aaa4;font-weight:400;">· ${_esc(m.label)}</span>`;

      const bodyText = m.text.split('\n').filter(l => l.trim()).join(' ');

      html += `
        <div style="display:flex;gap:10px;margin-bottom:14px;${flexDir}">
          <img src="${m.av}" style="width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;border:2px solid ${avBorder};">
          <div style="flex:1;">
            <div style="font-size:12px;font-weight:700;color:${nameColor};margin-bottom:4px;${nameAlign}">${nameHtml}</div>
            <div style="background:${bubbleBg};border:1px solid ${bubbleBdr};border-radius:${bubbleR};padding:10px 13px;font-size:13px;line-height:1.65;color:#3a463f;">
              ${_esc(bodyText)}
            </div>
          </div>
        </div>`;
    });
  });

  el.innerHTML = html;
  // 모달이 이미 열려있을 때만 즉시 스크롤 — 닫혀있으면 shown.bs.modal에서 처리
  _scrollRecordToCurrent(el);
}

/* recordModalBody를 맨 아래(최신 섹션)로 스크롤 */
function _scrollRecordToCurrent(el) {
  el = el || document.getElementById('recordModalBody');
  if (!el) return;
  el.scrollTop = el.scrollHeight;
}

/* ══════════════════════════════════════════════════════════════
   MOCK HELPER
══════════════════════════════════════════════════════════════ */
function _mockRound(idx) {
  if (idx >= MOCK_DATA.rounds.length) {
    _showFinal(MOCK_DATA.final);
    return;
  }
  const rd = MOCK_DATA.rounds[idx];
  // mock: 스테이지·턴 배지 업데이트 (실 서버 round_update 이벤트 대신)
  S.currentStage = idx === 0 ? 'position' : (idx <= 3 ? 'pro_round' : 'con_round');
  S.currentTurn = idx === 0 ? 1 : ((idx - 1) % 3) + 1;
  _updateStageDisplay();
  _loadingBubble('left');
  _loadingBubble('right');

  setTimeout(() => {
    _fillBubble('left', rd.pro, []);
    setTimeout(() => {
      _fillBubble('right', rd.con, []);
      setTimeout(() => {
        if (S.mode === 'aiuser') {
          _showActionBar({ wait_type: 'user_turn' });
        } else {
          _showActionBar({ wait_type: 'user_choice', extra_available: idx < MOCK_DATA.rounds.length - 1 });
        }
      }, 500);
    }, 800);
  }, 800);
}

/* ══════════════════════════════════════════════════════════════
   UTILITIES
══════════════════════════════════════════════════════════════ */
function _setEl(id, prop, val) {
  const el = document.getElementById(id);
  if (el) el[prop] = val;
}

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _getCsrf() {
  return document.cookie.split(';')
    .map(c => c.trim())
    .find(c => c.startsWith('csrftoken='))
    ?.split('=')[1] || '';
}

/* ── URL에 session 있으면 이어하기 모드로 자동 진입 ────────── */
(function _checkResume() {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get('session');
  if (sessionId && !MOCK_MODE) _resumeDebate(parseInt(sessionId, 10));
})();

/* ── Expose globals for onclick ────────────────────────────── */
/* ── 빨리감기 버튼 핸들러 ─────────────────────────────────────── */
function skipTyping(side) {
  if (_skipFn[side]) _skipFn[side]();
}
function closeFinal() {
  // 오버레이만 닫고 뒤의 토론 기록(피드)을 노출. (readonly/done 공통)
  renderFeed();   // 뒤에 깔리는 토론 기록 보장
  document.getElementById('finalView').style.display = 'none';
  // 다시 주장 다지기로 돌아올 수 있는 재열기 버튼 노출
  const _rfBtn = document.getElementById('reopenFinalBtn');
  if (_rfBtn && _finalShown) _rfBtn.style.display = '';
}
window.closeFinal = closeFinal;

/* 닫았던 주장 다지기(요약) 오버레이를 다시 표시 */
function reopenFinal() {
  if (!_finalShown) return;   // 아직 요약이 생성되지 않았으면 무시
  document.getElementById('finalView').style.display = 'flex';
  const _rfBtn = document.getElementById('reopenFinalBtn');
  if (_rfBtn) _rfBtn.style.display = 'none';
}
window.reopenFinal = reopenFinal;
function skipAll() { skipTyping('left'); skipTyping('right'); }
window.skipAll = skipAll;

window.startDebate = startDebate;
window.showDifficulty = showDifficulty;
window.backToIntroStep1 = backToIntroStep1;
window.startWithDifficulty = startWithDifficulty;
window.sendAction = sendAction;
window.submitUserInput = submitUserInput;
window.openAskModal = openAskModal;
window.updateAskCharCount = updateAskCharCount;
window.submitQuestion = submitQuestion;
window.addQuestionToRecord = addQuestionToRecord;
window.setAskTarget = setAskTarget;
window.openSource = openSource;
window.openMemo = openMemo;
window.closeMemo = closeMemo;
window.restartDebate = restartDebate;
window.skipTyping = skipTyping;

/* 피드 수동 스크롤 감지 — 맨 아래로 내리면 자동 추적 재개, 위로 올리면 멈춤 */
(function () {
  const feed = document.getElementById('debateFeed');
  if (!feed) return;
  feed.addEventListener('scroll', () => {
    if (_progScroll) return;   // 프로그램 스크롤은 무시
    _feedAutoScroll = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) < 50;
  });
})();

/* 질문 모달을 '취소'로 닫았을 때(제출 아님) — 액션바가 떠 있으면 자동 진행 재개 */
(function () {
  const m = document.getElementById('askModal');
  if (!m) return;
  m.addEventListener('hidden.bs.modal', () => {
    if (_askSubmitted) { _askSubmitted = false; return; }   // 제출 → SSE가 이어감
    const bar = document.getElementById('actionBar');
    if (bar && bar.style.display !== 'none' && !_actionInProgress) _scheduleAutoNext();
  });
})();
