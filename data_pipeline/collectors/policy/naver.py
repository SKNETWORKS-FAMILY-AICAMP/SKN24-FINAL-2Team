"""
네이버 뉴스 검색 API 수집기 (collectors/naver.py)

변경 사항:
  - 쿼리당 최대 1000건 수집 (API 최대 display=100, start 페이징)
  - 본문 추출: n.news.naver.com → #dic_area 고정, 그 외 → trafilatura 폴백 체인
  - GPT 정책 관련성 필터: 키워드만 포함된 무관 기사 제거
"""
import json
import logging
import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import (
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    OPENAI_API_KEY,
    REQUEST_TIMEOUT,
)
from collectors.models import Article
from collectors.utils import (
    clean_html_tags,
    is_duplicate,
    log_failure,
    make_session,
    mark_seen,
    parse_datetime,
    polite_sleep,
    with_retry,
)

logger = logging.getLogger(__name__)

NAVER_API_URL  = "https://openapi.naver.com/v1/search/news.json"
DISPLAY_COUNT  = 50          # 네이버 API 한 번에 최대 100건
MAX_PER_QUERY  = 50         # 쿼리당 최대 수집 건수 (start 상한 1000)
SOURCE_NAME    = "naver"

# GPT 관련성 필터 설정
GPT_BATCH_SIZE = 20
GPT_MODEL      = "gpt-4o-mini"

PUBLISHER_DOMAIN_MAP = {
    # ── 주요 전국지 ──
    "donga": "동아일보", "hani": "한겨레", "hankookilbo": "한국일보",
    "hankyung": "한국경제", "joongang": "중앙일보", "khan": "경향신문",
    "kmib": "국민일보", "mk": "매일경제", "mt": "머니투데이",
    "munhwa": "문화일보", "segye": "세계일보", "seoul": "서울신문",
    "shinailbo": "신아일보", "sedaily": "서울경제", "chosun": "조선일보",
    # ── 방송 ──
    "news": "SBS", "ytn": "YTN", "mbn": "MBN", "mbnmoney": "MBN머니",
    "imnews": "MBC", "wowtv": "한국경제TV", "fetv": "FETV",
    "natv": "국회방송", "ktv": "KTV", "n": "네이버뉴스",
    "ichannela": "채널A", "cjb": "CJB청주방송", "dgmbc": "대구MBC",
    "jibs": "JIBS", "jejumbc": "제주MBC", "kjmbc": "광주MBC",
    "kbsm": "KBS미디어", "mbceg": "MBC경남", "nbntv": "NBN TV",
    "obsnews": "OBS", "paxetv": "평화방송", "sentv": "SEN TV",
    "tjmbc": "대전MBC", "ysmbc": "여수MBC", "onews": "ONews",
    "jmbc": "전주MBC", "ikbc": "KBC광주방송",
    # ── 통신사 ──
    "yna": "연합뉴스", "yonhapnewstv": "연합뉴스TV",
    "newsis": "뉴시스", "news1": "뉴스1", "nspna": "NSP통신",
    # ── 경제·금융 전문지 ──
    "edaily": "이데일리", "fnnews": "파이낸셜뉴스", "fntimes": "한국금융신문",
    "ebn": "EBN", "ekn": "이코노믹리뷰", "biz": "헤럴드경제",
    "view": "아시아경제", "dt": "디지털타임스", "etoday": "이투데이",
    "newspim": "뉴스핌", "viva100": "브릿지경제", "businesspost": "비즈니스포스트",
    "businesskorea": "비즈니스코리아", "businessplus": "비즈니스플러스",
    "ceoscoredaily": "CEO스코어데일리", "dailian": "데일리안",
    "dealsite": "딜사이트", "econovill": "이코노빌", "economist": "이코노미스트",
    "economychosun": "이코노미조선", "etnews": "전자신문",
    "financialpost": "파이낸셜포스트", "financialreview": "파이낸셜리뷰",
    "finomy": "파이노미", "fsnews": "식품안전뉴스", "g-enews": "글로벌이코노믹",
    "hansbiz": "한스경제", "insightkorea": "인사이트코리아",
    "investchosun": "조선비즈", "kbanker": "한국금융",
    "m-economynews": "M이코노미뉴스", "marketnews": "마켓뉴스",
    "meconomynews": "M이코노미", "megaeconomy": "메가경제",
    "metroseoul": "메트로신문", "moneys": "머니S",
    "niceeconomy": "나이스경제", "sateconomy": "새틀경제",
    "segyebiz": "세계비즈", "seouleconews": "서울이코노미뉴스",
    "seoulfn": "서울파이낸스", "smartfn": "스마트에프엔",
    "thevaluenews": "더밸류뉴스", "webeconomy": "웹이코노미",
    "womaneconomy": "여성경제신문", "datanews": "데이터뉴스",
    "datasom": "데이터솜", "newsway": "뉴스웨이", "newsworks": "뉴스웍스",
    "joongangenews": "중앙이코노미스트", "isfnews": "IFS포스트",
    "fntoday": "파이낸스투데이", "econonews": "이코노뉴스",
    "econotelling": "이코노텔링", "junggi": "중기이코노미",
    "kbiznews": "중소기업뉴스", "smedaily": "중소기업신문",
    # ── IT·테크 전문지 ──
    "aitimes": "AI타임스", "betanews": "베타뉴스", "bloter": "블로터",
    "ddaily": "디지털데일리", "digitaltoday": "디지털투데이",
    "epnc": "IT비즈뉴스", "itbiznews": "IT비즈뉴스",
    "koreaittimes": "코리아IT타임스", "techholic": "테크홀릭",
    "techm": "테크M", "technoa": "테크노아", "zdnet": "ZDNet코리아",
    "eroun": "이로운넷",
    # ── 조선 계열 ──
    "health": "헬스조선", "sportschosun": "스포츠조선", "shindonga": "신동아",
    # ── 스포츠 ──
    "sportsseoul": "스포츠서울", "sportsworldi": "스포츠월드",
    "starnewskorea": "스타뉴스", "topstarnews": "탑스타뉴스",
    "mydaily": "마이데일리", "interfootball": "인터풋볼", "khgames": "경향게임스",
    # ── 법률 전문 ──
    "lawtimes": "법률신문", "lawtalknews": "로톡뉴스", "lawissue": "법률방송",
    "ltn": "법조타임즈", "lec": "한국법률경제신문",
    # ── 의료·보건 ──
    "dailymedi": "데일리메디", "dailypharm": "데일리팜",
    "doctorsnews": "의사신문", "doctorstimes": "닥터스타임스",
    "docdocdoc": "후생신보", "kormedi": "코메디닷컴",
    "medicalworldnews": "메디컬월드뉴스", "medigatenews": "메디게이트뉴스",
    "mkhealth": "MK헬스", "mdtoday": "메디컬투데이",
    # ── 교육 전문 ──
    "edupress": "에듀프레스", "kyosu": "교수신문",
    "lecturernews": "강사신문", "veritas-a": "베리타스알파",
    "kidshankook": "키즈한국일보",
    # ── 노동 전문 ──
    "laborplus": "노동법률", "labortoday": "매일노동뉴스", "worktoday": "워크투데이",
    # ── 세금·회계 전문 ──
    "taxtimes": "세금뉴스", "taxwatch": "택스워치", "joseilbo": "조세일보",
    # ── 농업·식품 전문 ──
    "foodnews": "식품음료신문", "nongmin": "농민신문",
    "nongup": "농업인신문", "farmnmarket": "팜앤마켓",
    "cooknchefnews": "쿡앤셰프뉴스",
    # ── 에너지·환경 전문 ──
    "electimes": "전기신문", "greened": "그린에드",
    "greenpostkorea": "그린포스트코리아", "industrynews": "인더스트리뉴스",
    # ── 복지·NGO ──
    "ablenews": "에이블뉴스", "welfarenews": "복지타임스", "ngonews": "NGO뉴스",
    # ── 지역지 ──
    "asiatime": "아시아타임즈", "asiatoday": "아시아투데이",
    "busan": "부산일보", "chungnamilbo": "충남일보", "daejonilbo": "대전일보",
    "gjdream": "광주드림", "gndomin": "경남도민일보", "gnmaeil": "경남매일",
    "gnnews": "경남뉴스", "headlinejeju": "헤드라인제주", "hidomin": "하이도민",
    "idaegu": "아이대구", "idjnews": "의령뉴스", "idomin": "아이도민",
    "incheonilbo": "인천일보", "incheonin": "인천IN", "incheontoday": "인천투데이",
    "jbnews": "전북일보", "jejudomin": "제주도민일보", "jejumaeil": "제주매일",
    "jejunews": "제주신문", "jejusori": "제주의소리", "jemin": "제민일보",
    "jeollailbo": "전라일보", "jeonmae": "전남매일", "jeonmin": "전민일보",
    "jjan": "전주일보", "jjn": "전주뉴스", "jndn": "전남도민신문",
    "jnilbo": "전남일보", "joongdo": "중도일보", "kado": "강원도민일보",
    "kbmaeil": "경북매일", "kgnews": "경기뉴스", "kihoilbo": "기호일보",
    "kjdaily": "광주일보", "kmaeil": "경남매일", "knnews": "경남신문",
    "kookje": "국제신문", "kwangju": "광주매일신문", "kwnews": "강원뉴스",
    "kyeonggi": "경기일보", "kyeongin": "경인일보", "kyongbuk": "경북일보",
    "mdilbo": "무등일보", "namdonews": "남도뉴스", "sejungilbo": "세정일보",
    "siminilbo": "시민일보", "siminsori": "시민소리", "sjbnews": "새전북신문",
    "ujeil": "울산제일일보", "iusm": "울산매일", "dkilbo": "대경일보",
    "imaeil": "매일신문", "joongboo": "중부일보", "ksilbo": "경상일보",
    "ktnews": "경기타임스", "cctoday": "충청투데이", "ccreview": "충청리뷰",
    "cctimes": "충청타임즈", "ccnnews": "충청뉴스", "cnbizm": "충남비즈니스",
    "ccdailynews": "충청데일리뉴스", "cstimes": "충청시대",
    "goodmorningcc": "굿모닝충청", "gimhaenews": "김해뉴스",
    # ── 인터넷 매체 ──
    "ajunews": "아주경제", "breaknews": "브레이크뉴스", "cnbnews": "CNB뉴스",
    "enewstoday": "이뉴스투데이", "gukjenews": "국제뉴스",
    "huffingtonpost": "허프포스트코리아", "inews24": "아이뉴스24",
    "insight": "인사이트", "kukinews": "쿠키뉴스", "naeil": "내일신문",
    "newdaily": "뉴데일리", "nocutnews": "노컷뉴스", "ohmynews": "오마이뉴스",
    "pennmike": "펜앤드마이크", "pressian": "프레시안",
    "sisajournal": "시사저널", "sisajournal-e": "시사저널e", "sisain": "시사IN",
    "wikitree": "위키트리", "newstomato": "뉴스토마토", "mediatoday": "미디어오늘",
    "mediapen": "미디어펜", "pinpointnews": "핀포인트뉴스", "polinews": "폴리뉴스",
    "rapportian": "라포르시안", "straightnews": "스트레이트뉴스",
    "theviewers": "더뷰어스", "thescoop": "더스쿠프", "thepublic": "더퍼블릭",
    "thefirstmedia": "더퍼스트미디어", "theguru": "더구루",
    "thefairnews": "더페어뉴스", "the-pr": "더피알", "the-biz": "더비즈",
    "thebilliards": "더빌리어즈", "thepowernews": "더파워뉴스",
    "sisacast": "시사캐스트", "sisafocus": "시사포커스", "sisaon": "시사온",
    "sisaweek": "시사위크", "sisunnews": "시선뉴스",
    "newsquest": "뉴스퀘스트", "newsworker": "뉴스워커",
    "newstopkorea": "뉴스탑코리아", "newsverse": "뉴스버스",
    "newsland": "뉴스랜드", "newsclaim": "뉴스클레임", "newsdream": "뉴스드림",
    "newsfreezone": "뉴스프리존", "newsjeju": "뉴스제주", "newslock": "뉴스락",
    "newsmaker": "뉴스메이커", "newspost": "뉴스포스트", "newsprime": "뉴스프라임",
    "newsroad": "뉴스로드", "newstown": "뉴스타운", "newswatch": "뉴스와치",
    "newswell": "뉴스웰", "news2day": "뉴스투데이",
    "factin": "팩트인", "fieldnews": "필드뉴스", "dynews": "동양뉴스",
    "gobalnews": "글로벌뉴스", "goodkyung": "굿모닝경제",
    "inthenews": "인더뉴스", "issuenbiz": "이슈앤비즈",
    "opinionnews": "오피니언뉴스", "popcornnews": "팝콘뉴스",
    "pointdaily": "포인트데일리", "pointe": "포인트경제",
    "press9": "프레스9", "public25": "퍼블릭뉴스",
    "safetimes": "세이프타임즈", "safetynews": "안전신문",
    "seouland": "서울앤", "seoultimes": "서울타임스", "seoulwire": "서울와이어",
    "sidae": "시대일보", "startuptoday": "스타트업투데이",
    "todaykorea": "투데이코리아", "updownnews": "업다운뉴스",
    "viewsnnews": "뷰스앤뉴스", "weeklytoday": "위클리투데이",
    "wikileaks-kr": "위키리크스한국", "wolyo": "월요신문",
    "womennews": "여성뉴스", "womentimes": "여성타임스",
    "youthdaily": "청년일보", "ziksir": "직썰",
    "korea": "대한민국정책브리핑", "koreadaily": "코리아데일리",
    "koreareport": "코리아리포트", "apnews": "AP뉴스코리아",
    "iminju": "민주신문", "netongs": "네통스",
    "mediafine": "미디어파인", "mediaus": "미디어US",
    "mediajeju": "미디어제주", "mediawatch": "미디어워치",
    "mhns": "문화뉴스", "wsobi": "문화저널21", "mhj21": "문화저널21",
    # ── 코인·블록체인 ──
    "coinreaders": "코인리더스", "tokenpost": "토큰포스트",
    # ── 기타 전문지 ──
    "bntnews": "BNT뉴스", "bravo": "브라보마이라이프", "bosa": "보험신보",
    "bizhankook": "비즈한국", "biztribune": "비즈트리뷴",
    "consumernews": "소비자뉴스", "dailycar": "데일리카",
    "dentalnews": "덴탈뉴스", "discoverynews": "디스커버리뉴스",
    "dnews": "디펜스뉴스", "ekoreanews": "이코리아",
    "ilyoseoul": "일요서울", "ilyosisa": "일요시사", "ilyo": "일요신문",
    "insnews": "보험뉴스", "kookbang": "국방일보", "koscaj": "코스카저널",
    "ppss": "월간조선", "radiokorea": "라디오코리아",
    "smartbizn": "스마트비즈니스", "smarttimes": "스마트타임즈",
    "smarttoday": "스마트투데이", "socialvalue": "소셜밸류",
    "tournews21": "투어뉴스21", "vegannews": "비건뉴스",
    "whitepaper": "화이트페이퍼", "widedaily": "와이드뉴스",
    "bigtanews": "빅타뉴스", "bizwnews": "비즈W뉴스", "bizwork": "비즈워크",
    "bokuennews": "복은뉴스", "enetnews": "이넷뉴스",
    "danbinews": "단비뉴스", "djtimes": "DJ타임즈",
    "fins": "핀스", "ftoday": "에프투데이", "ggilbo": "경기일보(지역)",
    "hg-times": "HG타임스", "joygm": "조이지엠",
    "kdfnews": "KDF뉴스", "kizmom": "키즈맘",
    "livebiz": "라이브비즈", "livesnews": "라이브뉴스",
    "mynews": "마이뉴스", "mygoyang": "마이고양",
    "ibabynews": "베이비뉴스", "ibuan": "부안독립신문",
    "gocj": "고창뉴스", "gokorea": "고코리아", "handmk": "핸드메이커",
    "00news": "공공뉴스", "100ssd": "백세시대", "1conomynews": "일코노미뉴스",
    "4th": "4차산업뉴스", "aflnews": "AFL뉴스", "areyou": "아르유",
    "asiaa": "아시아A", "beyondpost": "비욘드포스트",
    "cts": "CTS기독교TV", "daily365": "데일리365", "dailypop": "데일리팝",
    "dailysmart": "데일리스마트", "delighti": "딜라이트", "domin": "도민일보",
    "inews365": "아이뉴스365", "job-post": "잡포스트",
    "srtimes": "SR타임스", "tfmedia": "TF미디어",
    "yonhapmidas": "연합뉴스 미다스", "kpenews": "KPE뉴스", "kpinews": "KPI뉴스",
    "lcnews": "LC뉴스", "m-i": "마켓인사이트",
    "bzeronews": "비제로뉴스", "dizzotv": "디조TV", "dongponews": "동포뉴스",
    "tleaves": "티리브스", "ttlnews": "TTL뉴스",
    "samdailbo": "삼달일보", "sjsori": "새전북소리",
}


# ══════════════════════════════════════════════════════════════════════════════
# 본문 추출
# ══════════════════════════════════════════════════════════════════════════════

def _is_naver_viewer(url: str) -> bool:
    """n.news.naver.com 또는 news.naver.com 뷰어 URL 여부"""
    host = urlparse(url).netloc
    return "naver.com" in host and "news" in host


def _extract_naver_viewer(html: str) -> str:
    """네이버 뷰어: #dic_area → 고정 셀렉터"""
    soup = BeautifulSoup(html, "lxml")
    el = (
        soup.select_one("#dic_area")
        or soup.select_one("#articeBody")
        or soup.select_one(".newsct_article")
    )
    if el:
        text = el.get_text(separator="\n", strip=True)
        if len(text) > 50:
            return text
    return ""


def _extract_general(html: str, url: str = "") -> str:
    """
    일반 언론사 URL 본문 추출:
    1) trafilatura  (가장 정확)
    2) newspaper3k
    3) BeautifulSoup 멀티셀렉터 폴백
    """
    # 1) trafilatura
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if text and len(text) > 100:
            return text.strip()
    except ImportError:
        pass

    # 2) newspaper3k
    try:
        from newspaper import Article as NewsArticle
        art = NewsArticle(url, language="ko")
        art.set_html(html)
        art.parse()
        if art.text and len(art.text) > 100:
            return art.text.strip()
    except Exception:
        pass

    # 3) BeautifulSoup — 주요 언론사 셀렉터 + 범용 셀렉터
    soup = BeautifulSoup(html, "lxml")
    SELECTORS = [
        # 네이버 뷰어
        "#dic_area", "#articeBody", ".newsct_article",
        # 조선·중앙·동아
        "#news_body_id", "#article_body", ".article-text",
        # 한겨레
        ".article-text-block", ".article_body",
        # 경향
        ".art_body",
        # 매일경제·한국경제
        "#articleBody", "#article_body_content",
        # 연합뉴스
        "#articleWrap", ".story-news",
        # SBS·MBC·KBS
        "#content_area", ".article_body_content",
        # 범용
        "article", "[class*='article-body']", "[class*='article_body']",
        "[id*='articleBody']", "[id*='article_body']",
        ".view_txt", ".view_content", ".news-con", "#newsContent",
        "#cont_newsBodyArea", ".view-article",
    ]
    for sel in SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 100:
                return text

    return ""


def fetch_article_body_smart(url: str, session: requests.Session) -> str:
    """URL 유형에 따라 분기하여 본문 추출"""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception as e:
        logger.debug(f"[Naver] fetch 실패 [{url}]: {e}")
        return ""

    if _is_naver_viewer(url):
        text = _extract_naver_viewer(html)
        if text:
            return text
        # 네이버 뷰어지만 실패 → 폴백
        return _extract_general(html, url)
    else:
        return _extract_general(html, url)


# ══════════════════════════════════════════════════════════════════════════════
# GPT 정책 관련성 필터
# ══════════════════════════════════════════════════════════════════════════════

class PolicyRelevanceFilter:
    """
    GPT로 기사 본문이 해당 정책과 실질적으로 관련 있는지 판별.
    키워드만 스쳐지나가는 기사를 제거.
    """

    def __init__(self):
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY 환경변수를 설정하세요.")
        self.client = OpenAI(api_key=OPENAI_API_KEY)

    def filter(self, articles: List[Article], policy_name: str) -> List[Article]:
        """
        articles: 단일 정책 쿼리로 수집된 기사 리스트
        policy_name: 해당 정책명
        반환: 관련 있다고 판정된 기사만
        """
        if not articles:
            return []

        logger.info(f"[GPT필터] '{policy_name}' 기사 {len(articles)}건 관련성 판별 시작")
        passed = []
        total_batches = (len(articles) + GPT_BATCH_SIZE - 1) // GPT_BATCH_SIZE

        for batch_no, start in enumerate(range(0, len(articles), GPT_BATCH_SIZE), 1):
            chunk = articles[start: start + GPT_BATCH_SIZE]
            rows = [
                {
                    "idx":     i,
                    "title":   a.title[:80],
                    "content": a.content[:500],  # 앞 500자만 판별에 사용
                }
                for i, a in enumerate(chunk)
            ]
            try:
                flags = self._gpt_relevance_batch(rows, policy_name)
            except Exception as e:
                logger.warning(f"[GPT필터] 배치 {batch_no} 오류: {e} → 전체 통과 처리")
                flags = [True] * len(chunk)

            for flag, article in zip(flags, chunk):
                if flag:
                    passed.append(article)

            logger.info(
                f"[GPT필터] '{policy_name}' 배치 {batch_no}/{total_batches} "
                f"→ {sum(flags)}/{len(chunk)}건 통과"
            )
            time.sleep(0.3)

        logger.info(f"[GPT필터] '{policy_name}' 최종 {len(passed)}/{len(articles)}건 통과")
        return passed

    def _gpt_relevance_batch(self, rows: List[dict], policy_name: str) -> List[bool]:
        system_prompt = (
            f"당신은 뉴스 기사가 특정 정책과 실질적으로 관련 있는지 판별하는 전문가입니다.\n"
            f"판별 대상 정책: 『{policy_name}』\n\n"
            "관련 있음(true) 기준:\n"
            "  - 해당 정책의 내용, 신청 방법, 지원 대상, 예산, 변경사항 등을 직접 다룸\n"
            "  - 해당 정책에 영향을 미치는 법안·예산·제도 변화를 다룸\n"
            "  - 해당 정책의 수혜자 사례, 효과, 비판을 다룸\n\n"
            "관련 없음(false) 기준:\n"
            "  - 정책명 키워드가 등장하지만 전혀 다른 주제의 기사\n"
            "  - 정책명 단어 중 일부만 우연히 포함된 기사\n"
            "반드시 아래 JSON 배열만 반환하세요. 설명 없이:\n"
            '[{"idx": 0, "relevant": true}, {"idx": 1, "relevant": false}, ...]'
        )

        response = self.client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": json.dumps(rows, ensure_ascii=False)},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match  = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(match.group()) if match else []

        if not isinstance(parsed, list):
            return [True] * len(rows)

        idx_map = {item["idx"]: item.get("relevant", True) for item in parsed}
        return [bool(idx_map.get(r["idx"], True)) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# NaverCollector
# ══════════════════════════════════════════════════════════════════════════════

class NaverCollector:

    def __init__(self, use_gpt_filter: bool = True):
        """
        use_gpt_filter: True면 수집 후 GPT 정책 관련성 필터 적용
        """
        self.session = make_session()
        self.session.headers.update({
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        })
        self.use_gpt_filter = use_gpt_filter
        self._gpt_filter: Optional[PolicyRelevanceFilter] = None
        if use_gpt_filter:
            try:
                self._gpt_filter = PolicyRelevanceFilter()
            except ValueError as e:
                logger.warning(f"[Naver] GPT 필터 비활성화: {e}")
                self.use_gpt_filter = False

    def collect(
        self,
        queries: Optional[List[str]] = None,
        skip_filter: bool = False,
    ) -> List[Article]:
        """
        queries: 정책명 리스트 (gov24 top5에서 받은 쿼리)
        skip_filter: True면 GPT 관련성 필터 건너뜀
        반환: 수집된 Article 리스트 (GPT 필터 적용 후)
        """
        if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
            logger.error("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수를 설정하세요.")
            return []

        queries = queries or []
        if not queries:
            logger.error("[Naver] 검색 쿼리가 없습니다.")
            return []

        all_results: List[Article] = []

        for query in queries:
            logger.info(f"[Naver] ▶ 쿼리: {query!r}")

            # 1) 최대 1000건 수집
            raw_articles = self._collect_query(query)
            logger.info(f"[Naver] {query!r} — 수집 {len(raw_articles)}건")

            # 2) GPT 관련성 필터
            if self.use_gpt_filter and not skip_filter and self._gpt_filter and raw_articles:
                filtered = self._gpt_filter.filter(raw_articles, policy_name=query)
            else:
                filtered = raw_articles

            logger.info(f"[Naver] {query!r} — 최종 {len(filtered)}건 (필터 후)")
            all_results.extend(filtered)

        logger.info(f"[Naver] 전체 {len(all_results)}건 수집 완료")
        return all_results

    def _collect_query(self, query: str) -> List[Article]:
        """
        네이버 API start 파라미터로 페이징.
        한 번에 display=100, start=1/101/201/.../901 → 최대 1000건.
        """
        articles: List[Article] = []
        start = 1

        while start <= MAX_PER_QUERY:
            items = self._search(query, start=start)
            if not items:
                break

            for item in items:
                article = self._parse_item(item, query)
                if article:
                    articles.append(article)
                    mark_seen(article.url)
                polite_sleep()

            logger.debug(f"[Naver] {query!r} start={start} → {len(items)}건")

            if len(items) < DISPLAY_COUNT:
                break

            start += DISPLAY_COUNT

        return articles

    @with_retry
    def _search(self, query: str, start: int = 1) -> Optional[List[dict]]:
        params = {
            "query":   query,
            "display": DISPLAY_COUNT,
            "start":   start,
            "sort":    "date",  # 최신순
        }
        resp = self.session.get(NAVER_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def _parse_item(self, item: dict, query: str) -> Optional[Article]:
        title        = clean_html_tags(item.get("title", ""))
        naver_link   = item.get("link", "")
        original_url = item.get("originallink") or naver_link

        if not title or not original_url:
            return None

        if is_duplicate(original_url):
            logger.debug(f"[Naver] 중복: {original_url}")
            return None

        pub_date = parse_datetime(item.get("pubDate", "")) or datetime.now()

        # 본문 추출: 네이버 뷰어 URL 우선
        fetch_url = naver_link if naver_link else original_url
        content   = fetch_article_body_smart(fetch_url, self.session)

        # 네이버 뷰어 실패 시 원문 URL 재시도
        if not content and naver_link and original_url != naver_link:
            content = fetch_article_body_smart(original_url, self.session)

        if not content:
            logger.debug(f"[Naver] 본문 추출 실패: {original_url}")
            return None

        publisher = self._extract_publisher(original_url)

        return Article(
            title=title,
            content=content,
            publisher=publisher,
            published_at=pub_date,
            url=original_url,
            source=SOURCE_NAME,
            keyword_matched=query,
        )

    @staticmethod
    def _extract_publisher(url: str) -> str:
        domain = urlparse(url).netloc.replace("www.", "")
        key = domain.split(".")[0]
        return PUBLISHER_DOMAIN_MAP.get(key, key)