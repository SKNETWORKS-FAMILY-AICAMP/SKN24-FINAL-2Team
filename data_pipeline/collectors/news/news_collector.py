"""
collectors/news/news_collector.py

뉴스 수집 파이프라인:
  STEP 1.  머니투데이 키워드 추출
  STEP 3.  청년 정책 키워드 필터 (GPT)
  STEP 4.  네이버 API 크롤링 (비동기)
  STEP 5.  찬반 프레임 생성 + GPT 분류
  STEP 5-1 stance별 상한 필터 (pro 10 / con 10 / neutral 6) — 저장 전 적용
  STEP 6.  raw JSON 저장 (필터된 기사만)
  STEP 7.  전처리 → clean JSON 저장
  STEP 8a. 카테고리 분류 (GPT, 기사 단위)
  STEP 8b. RDS insert  ← db_conn=None 이면 스킵
  STEP 8c. 카드 생성 트리거 체크
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import requests
import trafilatura
from bs4 import BeautifulSoup
from openai import AsyncOpenAI, OpenAI

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────
NAVER_API_URL      = "https://openapi.naver.com/v1/search/news.json"
MT_SERIES_URL      = "https://www.mt.co.kr/series/21"
CONCURRENT_LIMIT   = 20   # 기사 본문 크롤링 동시 연결 수 (높이면 차단 위험)
STANCE_CONCURRENT  = 8    # GPT 찬반 분류 동시 배치 수 (높이면 429 오류 위험)
CARD_TRIGGER_COUNT = 20   # category별 누적 기사 수 기준 — 이 수 이상이면 카드 생성 트리거

# stance별 보관 상한 (카드 생성 기준: 찬성 4 / 반대 4 / 중립 2 + 여유분 추가 가능)
PRO_LIMIT     = 4
CON_LIMIT     = 4
NEUTRAL_LIMIT = 2

CRAWL_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer":         "https://www.naver.com/",
}

CATEGORY_MAP = {
    1: "일자리", 2: "주거", 3: "교육",
    4: "금융",  5: "생활복지", 6: "문화",
}

# ── 전처리 상수 ───────────────────────────────────────────────────────────
TITLE_KEYS     = {"title", "headline", "news_title", "article_title", "subject"}
CONTENT_KEYS   = {"content", "body", "text", "article", "article_body", "article_text", "description", "summary", "snippet"}
URL_KEYS       = {"url", "link", "href", "article_url", "origin_url", "source_url"}
PUBLISHER_KEYS = {"publisher", "press", "media", "source_name", "site"}
DATE_KEYS      = {"published_at", "published", "pubdate", "date", "created_at", "updated_at"}

SECTION_WORDS       = {"뉴스","정치","경제","사회","문화","스포츠","국제","전국","지역","금융","증권","산업","부동산","교육","오피니언","사설","칼럼","인터뷰","포토","영상","전체"}
NOISE_SUBSTRINGS    = ("(으)로 기사보내기","URL복사","무단 전재","무단전재","재배포 금지","All Rights Reserved","Copyright","인터넷신문등록번호","신문등록번호","등록번호","등록일","발행일","발행인","편집인","편집국장","청소년보호","개인정보","대표전화","사업자등록번호","통신판매업신고번호","주사무소","본 사이트","판권","광고문의","제휴문의","댓글 내용입력","삭제한 댓글","그래도 삭제","관련 키워드","여러분의 제보","뉴스가 됩니다","카카오톡 :","기사를 추천합니다","AI 학습","기사 바로가기")
BOUNDARY_SUBSTRINGS = ("댓글","주요기사","주요뉴스","인기기사","추천기사","최신뉴스","많이 본 기사","관련기사","관련 기사","관련뉴스","관련 뉴스","관련 키워드","#Tag","기자 전체보기","기사전체보기","뉴스 듣기","기사 공유","이 기사를 추천합니다","많이 본 뉴스","기사 바로가기","회사소개","매체소개","고객센터","개인정보","청소년보호","이용약관","저작권","Copyright","All Rights Reserved")
NEWS_VERB_HINTS     = ("밝혔다","말했다","전했다","설명했다","덧붙였다","강조했다","추진","지원","모집","선발","개최","운영","시행","참여","대상","오는","지난","이번")
EXACT_NOISE_WORDS   = {"로그인","로그아웃","회원가입","마이페이지","구독","제보","검색","기사검색","전체기사","뉴스홈","바로가기","복사하기","닫기","더보기","비밀번호","수정","삭제","등록","작성자","기자명","사진","가","나","다","라","페이스북","트위터","카카오톡","카카오스토리","밴드","홈페이지","즐겨찾기","회사소개","매체소개","고객센터","공지사항","광고안내","기사제보","독자투고","이용약관","회원약관","편집규약","윤리강령","청소년보호정책","개인정보취급방침","개인정보처리방침","이메일무단수집거부","고충처리","고충처리인","다른기사 보기","이전 기사보기","다음 기사보기","본문 글씨 줄이기","본문 글씨 키우기","공유하기","이 기사를 공유합니다","주요기사","주요뉴스","인기기사","추천기사","최신뉴스","많이 본 기사","관련기사","오늘의 운세","신문 구독","뉴스레터 구독","기사 구매 안내","관련뉴스","관련 뉴스","관련 기사","관련 키워드","기자 전체보기","기사전체보기","#Tag","알림","알림서비스는 로그인 후 이용 가능합니다","전체 보기","님","마이 콘텐츠","회원정보","통합검색 & 사이트맵","사이트맵","사이트맵 닫기","RSS","뉴스 듣기","글자 크기","글자 크기 설정","기사 공유","기사공유","주소복사","북마크","다크모드","프린트","네이버 채널구독","다음 채널구독","이 기사를 추천합니다.","좋아요","많이 본 뉴스","단독"}

HTML_BLOCK_TAG_RE  = re.compile(r"</?(?:p|br|div|section|article|header|footer|li|ul|ol|h[1-6]|tr|td|blockquote)[^>]*>", re.I)
HTML_TAG_RE        = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE    = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
SPACE_RE           = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_RE   = re.compile(r"\n{3,}")
KOREAN_RE          = re.compile(r"[가-힣]")
LETTER_OR_DIGIT_RE = re.compile(r"[0-9A-Za-z가-힣]")
EMAIL_RE           = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE           = re.compile(r"\b(?:\d{2,4}[-)]?\s*)?\d{3,4}-\d{4}\b")
DATE_ONLY_RE       = re.compile(r"^(?:\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{1,2}:\d{2})(?:\s.*)?$")
AUTHOR_RE          = re.compile(r"^[./\s]*(?:[가-힣]{2,5}|[A-Za-z][A-Za-z .-]{1,30})\s*(?:인턴)?기자(?:\s+.*)?$")
INPUT_META_RE      = re.compile(r"^(입력|수정|승인|최종수정)\s*[:：]?\s*")
PHOTO_CAPTION_RE   = re.compile(r"^(?:[▲△▶▷■□◆◇]\s*)?(?:\[[^\]]*(?:사진|자료|제공)[^\]]*\]|(?:사진|자료사진|이미지|그래픽|CG|표)\s*[=:：])", re.I)
SENTENCE_END_RE    = re.compile(r"(?:[.!?。][\"\'']?$|(?:밝혔다|말했다|전했다|설명했다|강조했다|덧붙였다|전망했다|예정이다|계획이다|방침이다|나섰다|했다|됐다|된다|있다|없다|한다|이다|다)[\"\'']?$)")


@dataclass
class CleanResult:
    content: str
    status: str
    reason: str
    raw_line_count: int
    clean_line_count: int
    raw_char_count: int
    clean_char_count: int
    quality_score: float


# ══════════════════════════════════════════════════════════════════════════
# STEP 1. 머니투데이 키워드 추출
# ══════════════════════════════════════════════════════════════════════════

# 오늘 수집한 키워드를 저장해두는 캐시 파일 경로
# run()에서 clean_dir 기준으로 덮어씀
_KEYWORD_CACHE_PATH: Path | None = None

def crawl_mt_keywords(max_pages: int = 33, today_filter: bool = True) -> list[str]:
    """
    머니투데이 [검색폭발 이슈키워드] 추출.
    today_filter=True  : 오늘 날짜 기사만 수집 (캐시 있을 때)
    today_filter=False : 날짜 무관 전체 수집   (최초 실행, 캐시 없을 때)
    """
    today   = date.today().strftime("%Y-%m-%d")
    session = requests.Session()
    session.headers.update(CRAWL_HEADERS)

    # 당일 이미 수집한 키워드 로드
    seen: set[str] = set()
    if _KEYWORD_CACHE_PATH and _KEYWORD_CACHE_PATH.exists():
        try:
            cache = json.loads(_KEYWORD_CACHE_PATH.read_text(encoding="utf-8"))
            if cache.get("date") == today:
                seen = set(cache.get("keywords", []))
                logger.info(f"[STEP 1] 당일 MT 크롤링 캐시 로드: {len(seen)}개 (중복 방지용)")
        except Exception:
            pass

    keywords: list[str] = []

    for page_no in range(1, max_pages + 1):
        try:
            resp = session.get(f"{MT_SERIES_URL}?page={page_no}", timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"[STEP 1] 페이지 요청 실패 (page={page_no}): {e}")
            break

        soup  = BeautifulSoup(resp.text, "html.parser")
        cards = (
            soup.select("li.article_item")       # 머니투데이 현행 구조
            or soup.select("div.article_wrap")
            or soup.select("ul.list_area > li")
            or soup.select("div.series_list li")
            or soup.select("ul.list01 > li")
        )

        page_has_today = False
        targets = []

        if cards:
            for card in cards:
                # 날짜 추출 시도
                date_tag = (
                    card.select_one("div.article_date")  # 머니투데이 현행: "2026.05.27  15:09"
                    or card.select_one("span.date") or card.select_one("p.date")
                    or card.select_one("span.time") or card.select_one("dd.date")
                )
                card_date = date_tag.get_text(strip=True) if date_tag else ""

                # 날짜 형식 통일: "2026.05.27" → "2026-05-27" 변환 후 비교
                card_date_norm = card_date.replace(".", "-")
                # 오늘 날짜 포함 여부 확인 (YYYY-MM-DD 또는 MM-DD 형태 모두 허용)
                is_today = today in card_date_norm or today[5:] in card_date_norm

                # h3.headline 에 "[검색폭발 이슈키워드]키워드명" 텍스트 포함
                tag = (
                    card.select_one("h3.headline")
                    or card.select_one("h3")
                    or card.select_one("a.news_ttl") or card.select_one("strong.tit")
                    or card.select_one("h2 a")
                    or card.select_one("a[href*='/news/']")
                )
                if tag:
                    targets.append((tag.get_text(strip=True), is_today))
                    if is_today:
                        page_has_today = True
        else:
            # 날짜 파싱 불가 시 텍스트 전체에서 키워드만 추출
            for text in soup.get_text(separator="\n").splitlines():
                targets.append((text, True))

        for text, is_today in targets:
            # today_filter=True면 오늘 것만, False면 전부 허용
            if today_filter and cards and not is_today:
                continue
            m = re.search(r"\[검색폭발\s*이슈키워드\]\s*(.+)", text)
            if m:
                kw = m.group(1).strip()
                # 키워드가 너무 길면 본문이 붙은 것 — 20자 초과 시 첫 어절만
                if len(kw) > 20:
                    kw = kw.split()[0] if kw.split() else kw
                if kw and kw not in seen:
                    seen.add(kw)
                    keywords.append(kw)

        # today_filter 모드에서 오늘 기사 없는 페이지 만나면 중단
        if today_filter and cards and date_tag and not page_has_today:
            logger.info(f"[STEP 1] page={page_no}에 오늘 기사 없음 — 크롤링 중단")
            break

        time.sleep(0.5)

    # 캐시 저장
    if _KEYWORD_CACHE_PATH:
        try:
            _KEYWORD_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _KEYWORD_CACHE_PATH.write_text(
                json.dumps({"date": today, "keywords": list(seen)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[STEP 1] 캐시 저장 실패: {e}")

    logger.info(f"[STEP 1] 키워드 추출 완료: {len(keywords)}개 (신규)")
    return keywords


# ══════════════════════════════════════════════════════════════════════════
# STEP 3. 청년 정책 키워드 필터 (GPT)
# ══════════════════════════════════════════════════════════════════════════

def filter_debate_keywords(keywords: list[str], openai_api_key: str) -> list[dict]:
    """
    청년 정책 관련 키워드 필터링 + 네이버 검색용 쿼리 생성.
    반환: [{"keyword": str, "search_query": str}, ...]
    """
    client     = OpenAI(api_key=openai_api_key)
    batch_size = 50  # 한 번에 50개씩 처리
    results    = []

    for i in range(0, len(keywords), batch_size):
        batch   = keywords[i:i + batch_size]
        kw_list = "\n".join(f"- {kw}" for kw in batch)
        prompt  = f"""다음 키워드들 중 아래 조건을 만족하는 것만 골라주세요.

{kw_list}

[선택 조건]
1. 청년(20~30대)의 일상(취업, 주거, 금융, 복지, 노동, 교육, 병역, 출산 등)과 관련된 이슈일 것
2. 아래 중 하나 이상 해당할 것
   - 정부·지자체·국회가 추진 중이거나 논의 중인 정책·법안·제도
   - 청년 세대에 실질적 영향을 미치는 사회·경제 구조적 이슈
   - 청년층이 직접 당사자인 사회적 쟁점
3. 찬성/반대 입장이 나뉠 수 있는 주제일 것

[제외 조건]
- 단순 경제 지표·수치 자체 (물가지수, 환율, 금리 수치 등)
- 특정 정치인 개인·선거 결과·정치 스캔들
- 단순 사건사고·자연재해·범죄 사건
- 연예·스포츠·게임·유행어·밈
- 특정 기업 실적·주가·IPO 등 투자 정보
- 해외 이슈 중 국내 청년과 직접 연관 없는 것
- 순수 과학기술 용어 (연구 성과, 우주탐사 등)
- 외교·안보·무역·군사 이슈 (무역분쟁, 외교정책, 군사장비, 국제기구 등)
- 법인·기업 대상 세제·규제 (법인세, 탄소세, 반덤핑 관세 등)
- 특정 국가·종교·민족 관련 해외 이슈
- 정치 제도·절차 자체 (필리버스터, 연립정부, 선거제도 등)
- 보험 통계·손해율 등 금융 업계 내부 지표

각 키워드에 대해 네이버 뉴스 검색에 쓸 구체적인 검색어도 함께 생성하세요.
검색어는 "정책명 + 정책/제도/논란" 형태로, 관련 기사가 잘 잡히도록 구체적으로 작성하세요.
예: 키워드 "청년도약계좌" → 검색어 "청년도약계좌 정책"
예: 키워드 "주휴수당" → 검색어 "주휴수당 폐지 논란"
예: 키워드 "중대재해처벌법" → 검색어 "중대재해처벌법 개정 논란"

Return only valid json.
{{"results": [{{"keyword": "원본키워드", "search_query": "네이버검색어"}}]}}"""

        try:
            resp    = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            content = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
            batch_result = json.loads(content)["results"]
            results.extend(batch_result)
            logger.info(f"[STEP 3] 배치 {i//batch_size + 1}: {len(batch)}개 중 {len(batch_result)}개 통과")
        except Exception as e:
            logger.warning(f"[STEP 3] 배치 {i//batch_size + 1} 실패: {e}")

    logger.info(f"[STEP 3] 토론 가능: {len(results)}개 / 전체 {len(keywords)}개")
    for r in results:
        logger.info(f"  ✅ {r['keyword']} → 검색어: '{r['search_query']}'")
    return results
    for r in result:
        logger.info(f"  ✅ {r['keyword']} → 검색어: '{r['search_query']}'")
    return result


# ══════════════════════════════════════════════════════════════════════════
# STEP 4. 네이버 API 크롤링 (비동기)
# ══════════════════════════════════════════════════════════════════════════

# 네이버 뉴스 언론사 도메인 → 한국어 매핑
PRESS_NAME_MAP = {
    # 통신사
    "yna":              "연합뉴스",
    "yonhapnewstv":     "연합뉴스TV",
    "newsis":           "뉴시스",
    "newspim":          "뉴스핌",
    "news1":            "뉴스1",
    # 지상파
    "kbs":              "KBS",
    "news.kbs":         "KBS",
    "mbc":              "MBC",
    "imnews":           "MBC",
    "sbs":              "SBS",
    "news.sbs":         "SBS",
    # 종편·보도전문
    "ytn":              "YTN",
    "jtbc":             "JTBC",
    "news.jtbc":        "JTBC",
    "tvchosun":         "TV조선",
    "mbn":              "MBN",
    "ichannela":        "채널A",
    "news.ichannela":   "채널A",
    # 종합일간지
    "chosun":           "조선일보",
    "donga":            "동아일보",
    "joongang":         "중앙일보",
    "koreajoongangdaily": "코리아중앙데일리",
    "hani":             "한겨레",
    "khan":             "경향신문",
    "hankookilbo":      "한국일보",
    "munhwa":           "문화일보",
    "segye":            "세계일보",
    "kmib":             "국민일보",
    "naeil":            "내일신문",
    "seoul":            "서울신문",
    "fnnews":           "파이낸셜뉴스",
    # 경제지
    "hankyung":         "한국경제",
    "mk":               "매일경제",
    "sedaily":          "서울경제",
    "mt":               "머니투데이",
    "edaily":           "이데일리",
    "etnews":           "전자신문",
    "asiae":            "아시아경제",
    "ajunews":          "아주경제",
    "businesspost":     "비즈니스포스트",
    "businesskorea":    "비즈니스코리아",
    "thebell":          "더벨",
    "bloter":           "블로터",
    "zdnet":            "지디넷코리아",
    "itchosun":         "IT조선",
    "ddaily":           "디지털데일리",
    "inews24":          "아이뉴스24",
    "dt":               "디지털타임스",
    "ebn":              "EBN",
    "etoday":           "이투데이",
    "newdaily":         "뉴데일리",
    "shinailbo":        "신아일보",
    "wowtv":            "한국경제TV",
    "heraldcorp":       "헤럴드경제",
    "koreaherald":      "코리아헤럴드",
    "koreatimes":       "코리아타임스",
    # 인터넷·시사
    "ohmynews":         "오마이뉴스",
    "pressian":         "프레시안",
    "mediatoday":       "미디어오늘",
    "wikitree":         "위키트리",
    "huffpost":         "허프포스트코리아",
    "sisain":           "시사IN",
    "sisajournal":      "시사저널",
    "chosunbiz":        "조선비즈",
    "biz.chosun":       "조선비즈",
    "kukinews":         "쿠키뉴스",
    "enewstoday":       "이뉴스투데이",
    "greenpostkorea":   "그린포스트코리아",
    # 전문지
    "lawtimes":         "법률신문",
    "medicaltimes":     "메디컬타임스",
    "healthchosun":     "헬스조선",
    "kormedi":          "코메디닷컴",
    "moneyweek":        "머니위크",
    "nongmin":          "농민신문",
    "labortoday":       "매일노동뉴스",
    "eduinnews":        "에듀인뉴스",
    "hangyo":           "한국교육신문",
    # 지역지
    "busan":            "부산일보",
    "kookje":           "국제신문",
    "imaeil":           "매일신문",
    "yeongnam":         "영남일보",
    "domin":            "도민일보",
    "hallailbo":        "한라일보",
    "jeonbuk":          "전북일보",
    "joongboo":         "중부일보",
    "kyeonggi":         "경기일보",
    "incheonilbo":      "인천일보",
    "cctoday":          "충청투데이",
    "daejonilbo":       "대전일보",
    "kwnews":           "강원일보",
}


def get_press_from_url(url: str) -> str:
    """URL 도메인 → 한국어 언론사명. 매핑 없으면 도메인 그대로 반환."""
    domain = urlparse(url).netloc.replace("www.", "").lower()
    key    = domain.split(".")[0]
    return PRESS_NAME_MAP.get(key) or PRESS_NAME_MAP.get(domain) or key


def clean_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    for ent, ch in [("&quot;", '"'), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")]:
        text = text.replace(ent, ch)
    return text.strip()


def is_similar_title(t1: str, t2: str, threshold: float = 0.85) -> bool:
    return SequenceMatcher(None, t1, t2).ratio() >= threshold


def extract_body(html_text: str) -> str:
    body = trafilatura.extract(
        html_text, include_comments=False, include_tables=False,
        no_fallback=False, favor_precision=False,
    ) or ""
    if len(body) < 200:
        soup = BeautifulSoup(html_text, "html.parser")
        for sel in ["article", "div[class*='article']", "div[class*='content']",
                    "div[class*='body']", "div[id*='article']"]:
            tag = soup.select_one(sel)
            if tag:
                for noise in tag.select(
                    "script,style,aside,nav,footer,header,figure,figcaption,"
                    "iframe,.ad,.banner,[class*='related'],[class*='recommend'],"
                    "[class*='comment'],[class*='copyright'],[class*='reporter']"
                ):
                    noise.decompose()
                candidate = re.sub(r"\s+", " ", tag.get_text(separator=" ", strip=True))
                if len(candidate) > len(body):
                    body = candidate
                break
    return body if len(body) >= 100 else ""


async def fetch_body_async(url: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> tuple[str, str]:
    async with semaphore:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html_text = await resp.text(errors="replace")
            return url, extract_body(html_text)
        except Exception:
            return url, ""


async def collect_async(
    query: str,
    naver_client_id: str,
    naver_client_secret: str,
    max_articles: int = 1000,
    today_only: bool = False,
    existing_urls: set | None = None,
) -> list[dict]:
    existing_urls = existing_urls or set()
    api_session = requests.Session()
    api_session.headers.update({
        "X-Naver-Client-Id":     naver_client_id,
        "X-Naver-Client-Secret": naver_client_secret,
    })
    from email.utils import parsedate as _parsedate
    from datetime import date as _date

    def _is_today(pub_date_str: str) -> bool:
        try:
            parsed = _parsedate(pub_date_str)
            if parsed:
                return _date(*parsed[:3]) == _date.today()
        except Exception:
            pass
        return False

    all_items = []
    for start in range(1, max_articles + 1, 100):
        try:
            resp  = api_session.get(
                NAVER_API_URL,
                params={"query": query, "start": start, "display": min(100, max_articles - len(all_items)), "sort": "date"},
                timeout=10,
            )
            items = resp.json().get("items", [])
            if not items:
                break
            if today_only:
                today_items, stop = [], False
                for item in items:
                    if _is_today(item.get("pubDate", "")):
                        today_items.append(item)
                    else:
                        stop = True
                all_items.extend(today_items)
                if stop:
                    break
            else:
                all_items.extend(items)
            if len(all_items) >= max_articles:
                break
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[Naver API] 오류: {e}")
            break

    logger.info(f"[STEP 4] '{query}' API 반환: {len(all_items)}건 (today_only={today_only})")

    seen_urls, seen_titles, candidates = set(), [], []
    for item in all_items:
        url       = item.get("originallink") or item.get("link", "")
        raw_title = clean_html_tags(item.get("title", ""))
        pub_date  = item.get("pubDate", "")
        if not url or url in seen_urls or url in existing_urls:
            continue
        if any(is_similar_title(raw_title, t) for t in seen_titles):
            continue
        seen_urls.add(url)
        seen_titles.append(raw_title)
        candidates.append({"url": url, "title": raw_title, "date": pub_date, "press": get_press_from_url(url)})

    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT, ssl=False)
    async with aiohttp.ClientSession(headers=CRAWL_HEADERS, connector=connector) as session:
        fetched = await asyncio.gather(*[fetch_body_async(c["url"], session, semaphore) for c in candidates])

    body_map = {url: body for url, body in fetched}
    results, no_body = [], 0
    for c in candidates:
        body = body_map.get(c["url"], "")
        if not body:
            no_body += 1
            continue
        results.append({
            "keyword": query, "title": c["title"], "press": c["press"],
            "url": c["url"], "date": c["date"], "content": body,
            "stance": None, "stance_score": None, "stance_reason": None,
        })

    logger.info(f"  본문없음={no_body} → 최종 {len(results)}건")
    return results


# ══════════════════════════════════════════════════════════════════════════
# STEP 5. 찬반 프레임 생성 + GPT 분류
# ══════════════════════════════════════════════════════════════════════════

def get_debate_frame(keyword: str, openai_api_key: str) -> dict:
    client = OpenAI(api_key=openai_api_key)
    prompt = f"""키워드: {keyword}

이 키워드와 관련된 청년 정책 주제에 맞는 찬반 논거를 생성하세요.
   - pro_frames: 찬성/긍정/지지 논거 3개
   - con_frames: 반대/부정/반대 논거 3개
   - neutral_frames: 찬반 논조 없이 현상/사실 기술 맥락 2개

반드시 아래 JSON 형식으로만 응답하세요.
Return only valid json. Do not include markdown.
{{
  "pro_frames": ["논거1", "논거2", "논거3"],
  "con_frames": ["논거1", "논거2", "논거3"],
  "neutral_frames": ["맥락1", "맥락2"]
}}"""

    for attempt in range(3):
        try:
            resp  = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            frame = json.loads(resp.choices[0].message.content.strip())
            logger.info(f"[STEP 5] '{keyword}' 프레임 생성 완료")
            return frame
        except Exception as e:
            logger.warning(f"[STEP 5] 프레임 생성 실패 (시도 {attempt+1}): {e}")
    return {"pro_frames": [], "con_frames": [], "neutral_frames": []}


async def gpt_classify_batch(
    batch: list[dict], keyword: str, frame: dict,
    async_client: AsyncOpenAI, semaphore: asyncio.Semaphore,
) -> None:
    pro_str     = "\n".join(f"  - {f}" for f in frame.get("pro_frames", []))
    con_str     = "\n".join(f"  - {f}" for f in frame.get("con_frames", []))
    neutral_str = "\n".join(f"  - {f}" for f in frame.get("neutral_frames", []))

    items_str = "\n".join([
        f"{j}. 제목: {art['title']} / 요약: {art['content'][:150]}"
        for j, art in enumerate(batch)
    ])
    prompt = (
    f"토론 주제: {keyword} 관련 정책\n\n"
    f"[찬성 논거]\n{pro_str}\n\n"
    f"[반대 논거]\n{con_str}\n\n"
    f"[중립 맥락]\n{neutral_str}\n\n"
    "각 기사를 아래 기준으로 엄격하게 분류하세요.\n\n"
    '- "pro": 위 찬성 논거를 직접 뒷받침하는 내용이 기사의 핵심일 것\n'
    '- "con": 위 반대 논거를 직접 뒷받침하는 내용이 기사의 핵심일 것\n'
    '- "neutral": 해당 정책을 직접 다루되, 찬반 없이 사실만 전달할 것\n'
    '- "discard": 아래 중 하나라도 해당하면 무조건 discard\n'
    '  · 정책 이름만 언급되고 내용은 다른 주제인 기사\n'
    '  · 정책과 간접적으로만 연관된 기사 (예: 관련 통계, 유사 해외 사례)\n'
    '  · 인사·부고·행사·공고·채용 기사\n'
    '  · pro/con/neutral 어디에도 명확히 해당하지 않으면 discard\n\n'
    "⚠️ 애매하면 반드시 discard로 분류하세요. neutral은 해당 정책을 직접 다루는 경우만 허용합니다.\n\n"
    f"기사 목록:\n{items_str}\n\n"
    "rule: idx 0부터, 모든 기사 포함, score 0~1, reason 한 문장\n\n"
    '{"results": [{"idx": 0, "stance": "pro", "score": 0.8, "reason": "근거"}]}'
)
    async with semaphore:
        try:
            resp    = await async_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            content = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
            for r in json.loads(content)["results"]:
                idx = r.get("idx")
                if idx is None or idx >= len(batch):
                    continue
                batch[idx].update({
                    "stance":        r.get("stance", "discard"),
                    "stance_score":  round(float(r.get("score", 0.0)), 3),
                    "stance_reason": r.get("reason", ""),
                })
        except Exception as e:
            logger.warning(f"GPT 배치 실패: {e}")


async def run_gpt_classify(articles: list[dict], keyword: str, frame: dict, openai_api_key: str) -> None:
    if not articles:
        return
    logger.info(f"[STEP 5] GPT 분류: {len(articles)}건")
    async_client = AsyncOpenAI(api_key=openai_api_key)
    semaphore    = asyncio.Semaphore(STANCE_CONCURRENT)
    batches      = [articles[i:i+15] for i in range(0, len(articles), 15)]
    await asyncio.gather(*[gpt_classify_batch(b, keyword, frame, async_client, semaphore) for b in batches])


# ══════════════════════════════════════════════════════════════════════════
# STEP 6. raw JSON 저장
# ══════════════════════════════════════════════════════════════════════════

def save_raw(query: str, articles: list[dict], raw_dir: Path) -> Path:
    safe = re.sub(r'[\\/*?:"<>|]', "_", query)
    path = raw_dir / f"naver_{safe}.json"

    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    today_str     = date.today().strftime("%Y-%m-%d")
    existing_urls = {a["url"] for a in existing}
    _CATEGORY_MAP = {1: "일자리", 2: "주거", 3: "교육", 4: "금융", 5: "생활복지", 6: "문화"}
    new_articles  = []
    for a in articles:
        if a.get("stance") not in ("pro", "con", "neutral"):
            continue
        if a["url"] in existing_urls:
            continue
        cat_id = a.get("category_id", 5)
        # RDS 필드 + 본문(원본) + 분석 필드 순서로 정렬 저장
        ordered = {
            "keyword":       a.get("keyword", ""),
            "title":         a.get("title", ""),        # preprocess_articles 호환용
            "data_title":    a.get("title", ""),
            "category_id":   cat_id,
            "category_name": _CATEGORY_MAP.get(cat_id, "생활복지"),
            "press":         a.get("press", ""),
            "url":           a.get("url", ""),          # preprocess_articles 호환용
            "source_url":    a.get("url", ""),
            "date":          a.get("date", ""),         # rds.parse_pub_date 호환용
            "published_at":  a.get("date", ""),
            "collected_at":  a.get("collected_at", today_str),
            "updated_at":    today_str,
            "content":       a.get("content", ""),      # preprocess_articles 호환용
            "full_article":  a.get("content", ""),
            "stance":        a.get("stance", ""),
            "stance_score":  a.get("stance_score", None),
            "stance_reason": a.get("stance_reason", ""),
        }
        new_articles.append(ordered)
        existing_urls.add(a["url"])

    merged = existing + new_articles
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    pro     = sum(1 for a in merged if a.get("stance") == "pro")
    con     = sum(1 for a in merged if a.get("stance") == "con")
    neutral = sum(1 for a in merged if a.get("stance") == "neutral")
    logger.info(f"[STEP 6] 저장 → {path.name} (신규 {len(new_articles)}건 / 찬성 {pro} / 반대 {con} / 중립 {neutral})")
    return path


# ══════════════════════════════════════════════════════════════════════════
# STEP 7. 전처리
# ══════════════════════════════════════════════════════════════════════════

def normalize_text(text: Any) -> str:
    if text is None: return ""
    text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = html.unescape(text)
    text = text.replace("\ufeff","").replace("\u200b","").replace("\xa0"," ")
    if "<" in text and ">" in text:
        text = SCRIPT_STYLE_RE.sub(" ", text)
        text = HTML_BLOCK_TAG_RE.sub("\n", text)
        text = HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = text.replace("\\r\\n","\n").replace("\\n","\n").replace("\r\n","\n").replace("\r","\n")
    text = SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return MULTI_NEWLINE_RE.sub("\n\n", text).strip()


def split_lines(text: str) -> list[str]:
    normalized = normalize_text(text)
    lines = []
    for part in normalized.split("\n"):
        part = part.strip(" \t|")
        if not part: continue
        if " | " in part and len(part) > 250:
            lines.extend(chunk.strip() for chunk in part.split(" | ") if chunk.strip())
        else:
            lines.append(part)
    return lines


def compact_for_compare(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize_text(text)).lower()


def make_content_hash(title: str, content: str) -> str:
    key = compact_for_compare(title)[:60] + compact_for_compare(content)[:120]
    return hashlib.md5(key.encode()).hexdigest()


def is_boundary_line(line: str) -> bool:
    if line.startswith("#"): return True
    if any(token in line for token in BOUNDARY_SUBSTRINGS): return True
    if line in {"닫기", "더보기", "내 댓글 모음"}: return True
    return False


def is_hard_noise_line(line: str, title: str, doc_freq: Counter, start_freq: Counter, end_freq: Counter, total_docs: int) -> bool:
    stripped = line.strip()
    if not stripped: return True
    if stripped.startswith("#"): return True
    if stripped in SECTION_WORDS or stripped in EXACT_NOISE_WORDS: return True
    if len(stripped) <= 2 and not KOREAN_RE.search(stripped): return True
    if re.fullmatch(r"[\d\s./|·ㆍㅣ-]+", stripped): return True
    if INPUT_META_RE.match(stripped): return True
    if DATE_ONLY_RE.match(stripped): return True
    if AUTHOR_RE.match(stripped): return True
    if PHOTO_CAPTION_RE.match(stripped): return True
    if stripped[0] in "▲△▶▷■□◆◇" and len(stripped) <= 120: return True
    if ("[사진" in stripped or "사진=" in stripped or "사진제공" in stripped) and len(stripped) < 180: return True
    if EMAIL_RE.search(stripped) and len(stripped) < 80: return True
    if PHONE_RE.search(stripped) and len(stripped) < 90: return True
    if any(token in stripped for token in NOISE_SUBSTRINGS): return True
    if total_docs >= 8:
        threshold = max(4, int(total_docs * 0.06))
        edge_threshold = max(3, int(total_docs * 0.04))
        if len(stripped) <= 45 and doc_freq[stripped] >= threshold: return True
        if len(stripped) <= 70 and (start_freq[stripped] >= edge_threshold or end_freq[stripped] >= edge_threshold): return True
    if title and SequenceMatcher(None, compact_for_compare(stripped), compact_for_compare(title)).ratio() >= 0.92: return True
    return False


def is_sentence_like(line: str) -> bool:
    return bool(SENTENCE_END_RE.search(line.strip()))


def is_headline_like(line: str) -> bool:
    stripped = line.strip()
    compact_len = len(compact_for_compare(stripped))
    if compact_len < 14 or compact_len > 140: return False
    if is_sentence_like(stripped): return False
    if "…" in stripped or "..." in stripped: return True
    if stripped.startswith("[") or stripped.startswith('"'): return True
    if stripped.count("·") >= 1 and compact_len < 90: return True
    return False


def line_article_score(line: str, doc_freq: Counter, start_freq: Counter, end_freq: Counter, title: str, total_docs: int = 1) -> float:
    line = line.strip()
    if is_hard_noise_line(line, title, doc_freq, start_freq, end_freq, total_docs): return -8.0
    score = 0.0
    compact = re.sub(r"\s+", "", line)
    length  = len(compact)
    if length >= 25: score += 1.2
    if length >= 45: score += 1.2
    if length >= 80: score += 1.0
    if length >= 140: score += 0.7
    korean_count = len(KOREAN_RE.findall(line))
    symbol_count = max(len(LETTER_OR_DIGIT_RE.findall(line)), 1)
    if korean_count / symbol_count >= 0.45: score += 1.0
    if re.search(r"[.!?。]$|[다요죠음임됩니다했다됐다된다있다없다한다이다]$|[\"\'']$", line): score += 1.0
    if any(hint in line for hint in NEWS_VERB_HINTS): score += 0.7
    if is_sentence_like(line): score += 0.8
    if is_headline_like(line): score -= 2.0
    if EMAIL_RE.search(line): score -= 1.0
    if len(line) <= 12: score -= 2.0
    if " | " in line: score -= 1.0
    return score


def build_line_stats(records: list[dict]) -> tuple[Counter, Counter, Counter]:
    doc_freq, start_freq, end_freq = Counter(), Counter(), Counter()
    for record in records:
        lines = [l for l in split_lines(record.get("content", "")) if l]
        doc_freq.update(set(lines))
        start_freq.update(lines[:50])
        end_freq.update(lines[-50:])
    return doc_freq, start_freq, end_freq


def normalized_record(record: dict) -> dict:
    def pick(keys):
        for k in record:
            if k.lower().replace("-", "_") in keys:
                v = normalize_text(record[k])
                if v: return v
        return ""
    return {
        "title":        pick(TITLE_KEYS),
        "content":      pick(CONTENT_KEYS),
        "publisher":    pick(PUBLISHER_KEYS),
        "published_at": pick(DATE_KEYS),
        "url":          pick(URL_KEYS),
    }


def clean_article_content(raw_content: str, title: str, doc_freq: Counter, start_freq: Counter, end_freq: Counter, total_docs: int, min_chars: int) -> CleanResult:
    lines = split_lines(raw_content)
    raw_char_count = len(normalize_text(raw_content))
    segments, current, current_start, soft_gap = [], [], 0, 0

    def flush():
        nonlocal current, current_start, soft_gap
        if current: segments.append((current_start, current))
        current, current_start, soft_gap = [], 0, 0

    for idx, line in enumerate(lines):
        if is_boundary_line(line):
            flush(); continue
        if is_hard_noise_line(line, title, doc_freq, start_freq, end_freq, total_docs):
            if current:
                soft_gap += 1
                if soft_gap >= 4: flush()
            continue
        score = line_article_score(line, doc_freq, start_freq, end_freq, title, total_docs)
        if score >= 0.5 or len(compact_for_compare(line)) >= 28:
            if not current: current_start = idx
            current.append((line, score))
            soft_gap = 0
        elif current:
            soft_gap += 1
            if soft_gap >= 4: flush()
    flush()

    if not segments:
        return CleanResult("", "drop", "no_article_segment", len(lines), 0, raw_char_count, 0, 0.0)

    def segment_score(item):
        start_idx, segment = item
        text = "\n".join(line for line, _ in segment)
        char_count = len(compact_for_compare(text))
        sentence_lines = sum(1 for line, _ in segment if is_sentence_like(line))
        headline_lines = sum(1 for line, _ in segment if is_headline_like(line))
        line_count = max(len(segment), 1)
        score = sum(s for _, s in segment)
        score += min(char_count / 250.0, 8.0)
        score += min(sentence_lines, 10) * 1.6
        score -= headline_lines * 1.7
        if sentence_lines == 0: score -= 20.0
        if line_count >= 6 and sentence_lines / line_count < 0.35: score -= line_count * 1.5
        if headline_lines / line_count > 0.55: score -= line_count * 2.0
        score -= start_idx * 0.015
        return score

    best = max(segments, key=segment_score)
    output_lines, seen_lines = [], set()
    for line, _ in best[1]:
        key = compact_for_compare(line)
        if not key: continue
        if key in seen_lines and len(key) < 90: continue
        seen_lines.add(key)
        output_lines.append(line)

    while output_lines and is_hard_noise_line(output_lines[0], title, doc_freq, start_freq, end_freq, total_docs):
        output_lines.pop(0)
    while output_lines and is_hard_noise_line(output_lines[-1], title, doc_freq, start_freq, end_freq, total_docs):
        output_lines.pop()

    clean_text  = "\n".join(output_lines).strip()
    clean_chars = len(compact_for_compare(clean_text))
    quality     = segment_score(best)

    if clean_chars < min_chars:
        return CleanResult(clean_text, "review" if clean_chars >= 70 else "drop", "too_short_after_cleaning", len(lines), len(output_lines), raw_char_count, len(clean_text), quality)
    if len(output_lines) == 1 and clean_chars < 220:
        return CleanResult(clean_text, "review", "snippet_like_single_line", len(lines), len(output_lines), raw_char_count, len(clean_text), quality)
    return CleanResult(clean_text, "keep", "article_body_extracted", len(lines), len(output_lines), raw_char_count, len(clean_text), quality)


def preprocess_articles(articles: list[dict], min_chars: int = 120) -> list[dict]:
    doc_freq, start_freq, end_freq = build_line_stats(articles)
    total_docs = max(len(articles), 1)
    result, seen_urls, seen_hashes = [], set(), set()

    for record in articles:
        norm  = normalized_record(record)
        clean = clean_article_content(
            norm["content"], norm["title"],
            doc_freq, start_freq, end_freq, total_docs, min_chars,
        )
        if clean.status != "keep":
            continue
        url_key      = norm["url"].strip()
        content_hash = make_content_hash(norm["title"], clean.content)
        if (url_key and url_key in seen_urls) or content_hash in seen_hashes:
            continue
        if url_key: seen_urls.add(url_key)
        seen_hashes.add(content_hash)

        art_out = dict(record)
        art_out["content"] = clean.content
        result.append(art_out)

    return result


def save_clean(query: str, articles: list[dict], clean_dir: Path) -> list[dict]:
    safe       = re.sub(r'[\\/*?:"<>|]', "_", query)
    path       = clean_dir / f"naver_{safe}_clean.json"
    clean_arts = preprocess_articles(articles)

    today_str = date.today().strftime("%Y-%m-%d")
    _CATEGORY_MAP = {1: "일자리", 2: "주거", 3: "교육", 4: "금융", 5: "생활복지", 6: "문화"}
    ordered_arts = []
    for art in clean_arts:
        cat_id = art.get("category_id", 5)
        ordered = {
            "keyword":       art.get("keyword", ""),
            "data_title":    art.get("title", ""),
            "category_id":   cat_id,
            "category_name": _CATEGORY_MAP.get(cat_id, "생활복지"),
            "press":         art.get("press", ""),
            "source_url":    art.get("url", ""),
            "published_at":  art.get("date", ""),
            "collected_at":  art.get("collected_at", today_str),
            "updated_at":    today_str,
            "content":       art.get("content", ""),   # 전처리 완료 본문
            "stance":        art.get("stance", ""),
            "stance_score":  art.get("stance_score", None),
            "stance_reason": art.get("stance_reason", ""),
        }
        ordered_arts.append(ordered)
    clean_arts = ordered_arts

    path.write_text(json.dumps(clean_arts, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[STEP 7] 전처리 저장 → {path.name} ({len(clean_arts)}건 / 전체 {len(articles)}건)")
    return clean_arts


# ── STEP 7-1. stance별 상한 필터 ─────────────────────────────────────────

def filter_by_stance_limit(articles: list[dict]) -> list[dict]:
    """
    stance_score 높은 순으로 정렬 후 상한 초과분 제거.
      찬성 최대 PRO_LIMIT     (10)
      반대 최대 CON_LIMIT     (10)
      중립 최대 NEUTRAL_LIMIT  (6)
    """
    limits  = {"pro": PRO_LIMIT, "con": CON_LIMIT, "neutral": NEUTRAL_LIMIT}
    buckets: dict[str, list[dict]] = {"pro": [], "con": [], "neutral": []}

    for art in articles:
        stance = art.get("stance")
        if stance in buckets:
            buckets[stance].append(art)

    result = []
    for stance, limit in limits.items():
        bucket  = sorted(buckets[stance], key=lambda a: a.get("stance_score") or 0, reverse=True)
        kept    = bucket[:limit]
        dropped = len(bucket) - len(kept)
        if dropped:
            logger.info(f"[STEP 7-1] {stance}: {len(bucket)}건 → {len(kept)}건 유지 ({dropped}건 탈락)")
        result.extend(kept)

    logger.info(
        f"[STEP 7-1] 최종 {len(result)}건 "
        f"(찬성 {sum(1 for a in result if a.get('stance')=='pro')} / "
        f"반대 {sum(1 for a in result if a.get('stance')=='con')} / "
        f"중립 {sum(1 for a in result if a.get('stance')=='neutral')})"
    )
    return result


# ══════════════════════════════════════════════════════════════════════════
# STEP 8a. 카테고리 분류 (GPT, 기사 단위)
# ══════════════════════════════════════════════════════════════════════════

def classify_categories(articles: list[dict], openai_api_key: str) -> list[dict]:
    """
    response_format={"type": "json_object"} 사용 시 messages 안에
    "json" 단어가 반드시 포함돼야 함 → "Return only valid json" 명시.
    배치 실패 시 기본값 생활복지(5)로 처리하고 로깅.
    """
    client         = OpenAI(api_key=openai_api_key)
    batch_size     = 20
    failed_batches = 0

    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]

        for art in batch:
            art["category_id"] = 5  # 기본값: 생활복지

        items_str = "\n".join([
            f"{j}. 제목: {art.get('title', '')} / 본문: {art.get('content', '')[:100]}"
            for j, art in enumerate(batch)
        ])
        prompt = (
            "다음 기사들을 아래 카테고리 중 하나로 분류하세요.\n\n"
            "카테고리:\n"
            "1. 일자리 (취업, 고용, 노동, 임금, 파업)\n"
            "2. 주거 (전세, 월세, 부동산, 청약, 주택)\n"
            "3. 교육 (학교, 입시, 학원, 장학금)\n"
            "4. 금융 (대출, 금리, 투자, 주식, 세금, ETF, 보험)\n"
            "5. 생활복지 (의료, 복지, 연금, 육아, 법/제도)\n"
            "6. 문화 (여가, 엔터테인먼트, 여행, 게임)\n\n"
            f"기사 목록:\n{items_str}\n\n"
            "규칙: idx는 0부터, 모든 기사 포함, category_id는 1~6 중 하나.\n"
            "Return only valid json. Do not include markdown.\n"
            '{"results": [{"idx": 0, "category_id": 4}]}'
        )

        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content.strip())
            for r in data.get("results", []):
                idx = r.get("idx")
                try:
                    cat_id = int(r.get("category_id", 5))
                except (TypeError, ValueError):
                    cat_id = 5
                if isinstance(idx, int) and 0 <= idx < len(batch) and cat_id in CATEGORY_MAP:
                    batch[idx]["category_id"] = cat_id
        except Exception as e:
            failed_batches += 1
            logger.warning(f"[STEP 8a] 카테고리 분류 배치 실패: {e} — 기본값 5(생활복지) 적용")

    if failed_batches:
        logger.warning(f"[STEP 8a] 실패 배치 수: {failed_batches}")
    logger.info(f"[STEP 8a] 카테고리 분류 완료: {len(articles)}건")
    return articles


# ══════════════════════════════════════════════════════════════════════════
# RDS import 헬퍼
# ══════════════════════════════════════════════════════════════════════════

def _import_rds_module():
    try:
        from storage import rds
        return rds
    except ModuleNotFoundError:
        import rds
        return rds


# ══════════════════════════════════════════════════════════════════════════
# 메인 실행 함수
# ══════════════════════════════════════════════════════════════════════════

async def run(
    raw_dir: Path,
    clean_dir: Path,
    naver_client_id: str,
    naver_client_secret: str,
    openai_api_key: str,
    db_conn,                    # None이면 수집·전처리만 (--step collect용)
    qdrant_handler=None,
    max_articles: int = 1000,
    today_only: bool = False,
    metadata_dir: Path | None = None,
) -> list[int]:
    """
    뉴스 수집 파이프라인 전체 실행.
    db_conn=None 이면 STEP 1~7-1까지만 실행하고 JSON 저장 후 종료.
    반환: 카드 생성 트리거 대상 category_id 리스트
    """
    rds = _import_rds_module() if not (db_conn is None) else None
    if metadata_dir is None:
        metadata_dir = clean_dir
    metadata_dir.mkdir(parents=True, exist_ok=True)

    # 키워드 캐시 경로 설정
    global _KEYWORD_CACHE_PATH
    _KEYWORD_CACHE_PATH = metadata_dir / "keyword_cache.json"

    collect_only = (db_conn is None)
    if collect_only:
        logger.info("[run] db_conn=None — 수집·전처리만 실행 (DB/Qdrant 적재 없음)")

    # 누적 키워드 캐시 로드 (날짜 무관하게 전체 보관)
    today = date.today().strftime("%Y-%m-%d")
    if not collect_only:
        all_cache = rds.load_keyword_cache(db_conn)
        logger.info(f"[STEP 1] RDS 키워드 캐시 로드: {len(all_cache)}개")
    else:
        all_cache = {}
    # 구조: {"키워드": {"search_query": "...", "last_collected": "YYYY-MM-DD"}}

    # STEP 1
    # 캐시가 비어있으면 전체 33페이지 수집 (최초 실행)
    # 캐시가 있으면 오늘 날짜 기사만 수집 (일별 업데이트)
    is_first_run = not bool(all_cache)
    if is_first_run:
        logger.info("[STEP 1] 캐시 없음 — 전체 페이지 수집 (최초 실행)")
    crawled_keywords = crawl_mt_keywords(max_pages=33, today_filter=not is_first_run)
    # crawled_keywords = crawl_mt_keywords(max_pages=33, today_filter=False)

    # STEP 3 — 신규 키워드만 GPT 필터링
    new_keywords = [kw for kw in crawled_keywords if kw not in all_cache]
    if new_keywords:
        new_debate = filter_debate_keywords(new_keywords, openai_api_key)
        for item in new_debate:
            all_cache[item["keyword"]] = {
                "search_query":   item["search_query"],
                "last_collected": "1970-01-01",  # 아직 수집 안 됨
            }
            if not collect_only:
                rds.upsert_keyword_cache(db_conn, item["keyword"], item["search_query"], "1970-01-01")
        logger.info(f"[STEP 3] 신규 키워드 {len(new_debate)}개 캐시 추가")
    else:
        logger.info("[STEP 3] 신규 키워드 없음 — 종료")

    # 처리 대상: 신규(미수집)만
    to_process = []
    for kw, meta in all_cache.items():
        is_new = meta["last_collected"] == "1970-01-01"
        if is_new:
            to_process.append({
                "keyword":      kw,
                "search_query": meta["search_query"],
                "is_new":       True,
            })

    if not to_process:
        logger.info("오늘 수집할 키워드 없음 — 종료")
        return []

    logger.info(f"수집 대상: {len(to_process)}개 (신규)")

    trigger_category_ids: list[int] = []

    for kw_item in to_process:
        keyword      = kw_item["keyword"]
        search_query = kw_item["search_query"]

        collect_today_only = today_only
        collect_count      = max_articles

        logger.info(f"\n{'='*60}\n키워드: {keyword}  검색어: {search_query}\n{'='*60}")

        # STEP 4
        if collect_only:
            # DB 없이 실행 — 이미 저장된 clean JSON에서 기존 URL 로드해 중복 방지
            existing_urls = set()
            for f in clean_dir.glob("*_clean.json"):
                try:
                    for a in json.loads(f.read_text(encoding="utf-8")):
                        if a.get("url"):
                            existing_urls.add(a["url"])
                except Exception:
                    pass
            logger.info(f"[STEP 4] 기존 URL {len(existing_urls)}건 로드 (clean JSON 기준)")
        else:
            existing_urls = rds.get_existing_urls(db_conn)
        articles = await collect_async(
            search_query, naver_client_id, naver_client_secret,
            collect_count, collect_today_only, existing_urls,
        )
        if not articles:
            logger.warning(f"'{search_query}' 수집 결과 없음 — 스킵")
            continue

        # STEP 5
        frame = get_debate_frame(keyword, openai_api_key)
        await run_gpt_classify(articles, keyword, frame, openai_api_key)

        pro     = sum(1 for a in articles if a.get("stance") == "pro")
        con     = sum(1 for a in articles if a.get("stance") == "con")
        neutral = sum(1 for a in articles if a.get("stance") == "neutral")
        discard = sum(1 for a in articles if a.get("stance") == "discard")
        logger.info(f"분류 결과 — 찬성 {pro} / 반대 {con} / 중립 {neutral} / 폐기 {discard}")

        # STEP 5-1: stance별 상한 필터 (신규 키워드만 적용)
        safe_keyword = re.sub(r'[\\/*?:"<>|]', '_', keyword)
        raw_path     = raw_dir / f"naver_{safe_keyword}.json"

        filtered = filter_by_stance_limit(articles)

        # STEP 6: filtered 기사만 raw JSON에 저장
        save_raw(keyword, filtered, raw_dir)

        # STEP 7: raw JSON 읽어서 전처리
        raw        = json.loads(raw_path.read_text(encoding="utf-8"))
        clean_arts = save_clean(keyword, raw, clean_dir)

        # STEP 8a: 카테고리 분류
        clean_arts = classify_categories(clean_arts, openai_api_key)

        # category_id / category_name 을 clean JSON에 재저장
        _CATEGORY_MAP = {1: "일자리", 2: "주거", 3: "교육", 4: "금융", 5: "생활복지", 6: "문화"}
        for art in clean_arts:
            cat_id = art.get("category_id", 5)
            art["category_id"]   = cat_id
            art["category_name"] = _CATEGORY_MAP.get(cat_id, "생활복지")
        safe_kw    = re.sub(r'[\\/*?:"<>|]', "_", keyword)
        clean_path = clean_dir / f"naver_{safe_kw}_clean.json"
        clean_path.write_text(json.dumps(clean_arts, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[STEP 8a] category 반영 후 재저장 → {clean_path.name}")

        # collect_only면 JSON 저장까지만
        if collect_only:
            continue

        # STEP 8b: RDS insert
        clean_arts = rds.insert_articles(db_conn, clean_arts)

        # STEP 8b-2: 메타데이터 CSV 저장
        rds.save_metadata_csv(clean_arts, metadata_dir)

        # STEP 8b-3: Qdrant upsert
        if qdrant_handler is not None:
            qdrant_arts = [a for a in clean_arts if a.get("data_id")]
            if qdrant_arts:
                upserted = qdrant_handler.upsert_articles(qdrant_arts)
                logger.info(f"[Qdrant] '{keyword}' {upserted}건 적재 완료")

        # STEP 8c: 카드 생성 트리거 체크 (category 단위)
        from collections import Counter as _Counter
        cat_counts = _Counter(a.get("category_id", 5) for a in clean_arts)
        rep_cat_id = cat_counts.most_common(1)[0][0] if cat_counts else 5

        count = rds.count_articles_by_category(db_conn, rep_cat_id)
        logger.info(f"[STEP 8c] category_id={rep_cat_id} 누적 {count}건 (트리거 기준: {CARD_TRIGGER_COUNT})")
        if count >= CARD_TRIGGER_COUNT:
            if rep_cat_id not in trigger_category_ids:
                trigger_category_ids.append(rep_cat_id)
            logger.info(f"[STEP 8c] category_id={rep_cat_id} 카드 생성 트리거 ✅")

        # 캐시 날짜 업데이트 (수집 완료 표시)
        if keyword in all_cache:
            all_cache[keyword]["last_collected"] = today
            if not collect_only:
                rds.upsert_keyword_cache(db_conn, keyword, all_cache[keyword]["search_query"], today)

    logger.info("뉴스 수집 파이프라인 완료")
    return trigger_category_ids