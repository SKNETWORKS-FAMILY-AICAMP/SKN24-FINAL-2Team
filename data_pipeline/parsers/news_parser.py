#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


TITLE_KEYS = {
    "title",
    "headline",
    "news_title",
    "article_title",
    "subject",
}

CONTENT_KEYS = {
    "content",
    "body",
    "text",
    "article",
    "article_body",
    "article_text",
    "description",
    "summary",
    "snippet",
}

URL_KEYS = {"url", "link", "href", "article_url", "origin_url", "source_url"}
PUBLISHER_KEYS = {"publisher", "press", "media", "source_name", "site"}
DATE_KEYS = {"published_at", "published", "pubdate", "date", "created_at", "updated_at"}

SECTION_WORDS = {
    "뉴스",
    "정치",
    "경제",
    "사회",
    "문화",
    "스포츠",
    "국제",
    "전국",
    "지역",
    "금융",
    "증권",
    "산업",
    "부동산",
    "교육",
    "오피니언",
    "사설",
    "칼럼",
    "인터뷰",
    "포토",
    "영상",
    "전체",
}

EXACT_NOISE_WORDS = {
    "로그인",
    "로그아웃",
    "회원가입",
    "마이페이지",
    "구독",
    "제보",
    "검색",
    "기사검색",
    "전체기사",
    "뉴스홈",
    "바로가기",
    "복사하기",
    "스크롤 이동 상태바",
    "닫기",
    "더보기",
    "비밀번호",
    "수정",
    "삭제",
    "등록",
    "작성자",
    "기자명",
    "사진",
    "가",
    "나",
    "다",
    "라",
    "페이스북",
    "트위터",
    "카카오톡",
    "카카오스토리",
    "밴드",
    "홈페이지",
    "즐겨찾기",
    "회사소개",
    "매체소개",
    "고객센터",
    "공지사항",
    "광고안내",
    "기사제보",
    "독자투고",
    "이용약관",
    "회원약관",
    "편집규약",
    "윤리강령",
    "청소년보호정책",
    "개인정보취급방침",
    "개인정보처리방침",
    "이메일무단수집거부",
    "고충처리",
    "고충처리인",
    "다른기사 보기",
    "이전 기사보기",
    "다음 기사보기",
    "본문 글씨 줄이기",
    "본문 글씨 키우기",
    "공유하기",
    "이 기사를 공유합니다",
    "주요기사",
    "주요뉴스",
    "인기기사",
    "추천기사",
    "최신뉴스",
    "많이 본 기사",
    "관련기사",
    "오늘의 운세",
    "신문 구독",
    "뉴스레터 구독",
    "기사 구매 안내",
    "관련뉴스",
    "관련 뉴스",
    "관련 기사",
    "관련 키워드",
    "기자 전체보기",
    "기사전체보기",
    "#Tag",
    "알림",
    "알림서비스는 로그인 후 이용 가능합니다",
    "전체 보기",
    "님",
    "마이 콘텐츠",
    "회원정보",
    "통합검색 & 사이트맵",
    "사이트맵",
    "사이트맵 닫기",
    "RSS",
    "뉴스 듣기",
    "글자 크기",
    "글자 크기 설정",
    "기사 공유",
    "기사공유",
    "주소복사",
    "북마크",
    "다크모드",
    "프린트",
    "네이버 채널구독",
    "다음 채널구독",
    "이 기사를 추천합니다.",
    "좋아요",
    "많이 본 뉴스",
    "단독",
}

NOISE_SUBSTRINGS = (
    "(으)로 기사보내기",
    "URL복사",
    "무단 전재",
    "무단전재",
    "재배포 금지",
    "All Rights Reserved",
    "Copyright",
    "인터넷신문등록번호",
    "신문등록번호",
    "등록번호",
    "등록일",
    "발행일",
    "발행인",
    "편집인",
    "편집국장",
    "청소년보호",
    "개인정보",
    "대표전화",
    "사업자등록번호",
    "통신판매업신고번호",
    "주사무소",
    "본 사이트",
    "판권",
    "광고문의",
    "제휴문의",
    "댓글 내용입력",
    "삭제한 댓글",
    "그래도 삭제",
    "관련 키워드",
    "여러분의 제보",
    "뉴스가 됩니다",
    "카카오톡 :",
    "기사를 추천합니다",
    "AI 학습",
    "기사 바로가기",
)

BOUNDARY_SUBSTRINGS = (
    "댓글",
    "주요기사",
    "주요뉴스",
    "인기기사",
    "추천기사",
    "최신뉴스",
    "많이 본 기사",
    "관련기사",
    "관련 기사",
    "관련뉴스",
    "관련 뉴스",
    "관련 키워드",
    "#Tag",
    "기자 전체보기",
    "기사전체보기",
    "뉴스 듣기",
    "기사 공유",
    "이 기사를 추천합니다",
    "많이 본 뉴스",
    "기사 바로가기",
    "회사소개",
    "매체소개",
    "고객센터",
    "개인정보",
    "청소년보호",
    "이용약관",
    "저작권",
    "Copyright",
    "All Rights Reserved",
)

NEWS_VERB_HINTS = (
    "밝혔다",
    "말했다",
    "전했다",
    "설명했다",
    "덧붙였다",
    "강조했다",
    "추진",
    "지원",
    "모집",
    "선발",
    "개최",
    "운영",
    "시행",
    "참여",
    "대상",
    "오는",
    "지난",
    "이번",
)

HTML_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|br|div|section|article|header|footer|li|ul|ol|h[1-6]|tr|td|blockquote)[^>]*>",
    re.I,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
KOREAN_RE = re.compile(r"[가-힣]")
LETTER_OR_DIGIT_RE = re.compile(r"[0-9A-Za-z가-힣]")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\d{2,4}[-)]?\s*)?\d{3,4}-\d{4}\b")
DATE_ONLY_RE = re.compile(r"^(?:\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{1,2}:\d{2})(?:\s.*)?$")
AUTHOR_RE = re.compile(r"^[./\s]*(?:[가-힣]{2,5}|[A-Za-z][A-Za-z .-]{1,30})\s*(?:인턴)?기자(?:\s+.*)?$")
INPUT_META_RE = re.compile(r"^(입력|수정|승인|최종수정)\s*[:：]?\s*")
PHOTO_CAPTION_RE = re.compile(
    r"^(?:[▲△▶▷■□◆◇]\s*)?(?:\[[^\]]*(?:사진|자료|제공)[^\]]*\]|(?:사진|자료사진|이미지|그래픽|CG|표)\s*[=:：])",
    re.I,
)
SENTENCE_END_RE = re.compile(
    r"(?:[.!?。][\"”’)]?$|(?:밝혔다|말했다|전했다|설명했다|강조했다|덧붙였다|전망했다|예정이다|계획이다|방침이다|나섰다|했다|됐다|된다|있다|없다|한다|이다|다)[\"”’)]?$)"
)


@dataclass
class FieldChoice:
    value: str
    key: str
    score: float


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


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def iter_string_fields(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for key, child in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_string_fields(child, next_prefix)
    elif isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            yield prefix, "\n".join(value)
        else:
            for idx, child in enumerate(value):
                next_prefix = f"{prefix}[{idx}]"
                yield from iter_string_fields(child, next_prefix)


def choose_field(record: dict[str, Any], key_hints: set[str], purpose: str) -> FieldChoice:
    choices: list[FieldChoice] = []
    for key, value in iter_string_fields(record):
        text = normalize_text(value)
        if not text:
            continue
        key_name = normalize_key(key.split(".")[-1].split("[")[0])
        score = 0.0
        if key_name in key_hints:
            score += 100.0
        if purpose == "content":
            score += min(len(text) / 100.0, 30.0)
            score += article_likeness(text) * 3.0
            if key_name in TITLE_KEYS or key_name in URL_KEYS:
                score -= 80.0
        elif purpose == "title":
            score += 30.0 if 5 <= len(text) <= 180 else -20.0
            score -= min(max(len(text) - 180, 0) / 10.0, 30.0)
        else:
            score += min(len(text), 100) / 100.0
        choices.append(FieldChoice(text, key, score))
    if not choices:
        return FieldChoice("", "", -1.0)
    return max(choices, key=lambda item: item.score)


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = html.unescape(text)
    text = text.replace("\ufeff", "")
    text = text.replace("\u200b", "")
    text = text.replace("\xa0", " ")
    if "<" in text and ">" in text:
        text = html_to_text(text)
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return MULTI_NEWLINE_RE.sub("\n\n", text).strip()


def html_to_text(text: str) -> str:
    text = SCRIPT_STYLE_RE.sub(" ", text)
    text = HTML_BLOCK_TAG_RE.sub("\n", text)
    text = HTML_TAG_RE.sub(" ", text)
    return html.unescape(text)


def split_lines(text: str) -> list[str]:
    normalized = normalize_text(text)
    lines: list[str] = []
    for part in normalized.split("\n"):
        part = part.strip(" \t|")
        if not part:
            continue
        if " | " in part and len(part) > 250:
            lines.extend(chunk.strip() for chunk in part.split(" | ") if chunk.strip())
        else:
            lines.append(part)
    return lines


def compact_for_compare(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize_text(text)).lower()


def text_similarity(a: str, b: str) -> float:
    ca = compact_for_compare(a)
    cb = compact_for_compare(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    if len(ca) > 180 or len(cb) > 180:
        ca = ca[:180]
        cb = cb[:180]
    return SequenceMatcher(None, ca, cb).ratio()


def article_likeness(text: str) -> float:
    lines = split_lines(text)
    if not lines:
        return 0.0
    scores = [line_article_score(line, Counter(), Counter(), Counter(), "") for line in lines]
    return sum(max(score, 0.0) for score in scores) / max(len(lines), 1)


def build_line_stats(records: list[dict[str, Any]]) -> tuple[Counter[str], Counter[str], Counter[str]]:
    doc_freq: Counter[str] = Counter()
    start_freq: Counter[str] = Counter()
    end_freq: Counter[str] = Counter()
    for record in records:
        content = choose_field(record, CONTENT_KEYS, "content").value
        lines = [line for line in split_lines(content) if line]
        doc_freq.update(set(lines))
        start_freq.update(lines[:50])
        end_freq.update(lines[-50:])
    return doc_freq, start_freq, end_freq


def is_common_boilerplate(
    line: str,
    doc_freq: Counter[str],
    start_freq: Counter[str],
    end_freq: Counter[str],
    total_docs: int,
) -> bool:
    if total_docs < 8:
        return False
    threshold = max(4, int(total_docs * 0.06))
    edge_threshold = max(3, int(total_docs * 0.04))
    if len(line) <= 45 and doc_freq[line] >= threshold:
        return True
    if len(line) <= 70 and (start_freq[line] >= edge_threshold or end_freq[line] >= edge_threshold):
        return True
    return False


def is_boundary_line(line: str) -> bool:
    if line.startswith("#"):
        return True
    if any(token in line for token in BOUNDARY_SUBSTRINGS):
        return True
    if line in {"닫기", "더보기", "내 댓글 모음"}:
        return True
    return False


def is_hard_noise_line(
    line: str,
    title: str,
    doc_freq: Counter[str],
    start_freq: Counter[str],
    end_freq: Counter[str],
    total_docs: int,
) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith(("{", "[{")) and (stripped.count(":") >= 3 or "CODE_TYPE" in stripped):
        return True
    if stripped in SECTION_WORDS or stripped in EXACT_NOISE_WORDS:
        return True
    if len(stripped) <= 2 and not KOREAN_RE.search(stripped):
        return True
    if re.fullmatch(r"[\d\s./|·ㆍㅣ-]+", stripped):
        return True
    if INPUT_META_RE.match(stripped):
        return True
    if DATE_ONLY_RE.match(stripped):
        return True
    if AUTHOR_RE.match(stripped):
        return True
    if PHOTO_CAPTION_RE.match(stripped):
        return True
    if stripped[0] in "▲△▶▷■□◆◇" and len(stripped) <= 120:
        return True
    if ("[사진" in stripped or "사진=" in stripped or "사진제공" in stripped) and len(stripped) < 180:
        return True
    if EMAIL_RE.search(stripped) and len(stripped) < 80:
        return True
    if PHONE_RE.search(stripped) and len(stripped) < 90:
        return True
    if any(token in stripped for token in NOISE_SUBSTRINGS):
        return True
    if is_common_boilerplate(stripped, doc_freq, start_freq, end_freq, total_docs):
        return True
    if title and text_similarity(stripped, title) >= 0.92:
        return True
    return False


def is_sentence_like(line: str) -> bool:
    return bool(SENTENCE_END_RE.search(line.strip()))


def is_headline_like(line: str) -> bool:
    stripped = line.strip()
    compact_len = len(compact_for_compare(stripped))
    if compact_len < 14 or compact_len > 140:
        return False
    if is_sentence_like(stripped):
        return False
    if "…" in stripped or "..." in stripped or ". . ." in stripped:
        return True
    if stripped.startswith("[") or stripped.startswith('"') or stripped.startswith("“"):
        return True
    if stripped.count("·") >= 1 and compact_len < 90:
        return True
    return False


def line_article_score(
    line: str,
    doc_freq: Counter[str],
    start_freq: Counter[str],
    end_freq: Counter[str],
    title: str,
    total_docs: int = 1,
) -> float:
    line = line.strip()
    if is_hard_noise_line(line, title, doc_freq, start_freq, end_freq, total_docs):
        return -8.0
    score = 0.0
    compact = re.sub(r"\s+", "", line)
    length = len(compact)
    if length >= 25:
        score += 1.2
    if length >= 45:
        score += 1.2
    if length >= 80:
        score += 1.0
    if length >= 140:
        score += 0.7
    korean_count = len(KOREAN_RE.findall(line))
    symbol_count = max(len(LETTER_OR_DIGIT_RE.findall(line)), 1)
    korean_ratio = korean_count / symbol_count
    if korean_ratio >= 0.45:
        score += 1.0
    if re.search(r"[.!?。]$|[다요죠음임됨됨니다했다였다된다]$|[\"”’]$", line):
        score += 1.0
    if any(hint in line for hint in NEWS_VERB_HINTS):
        score += 0.7
    if is_sentence_like(line):
        score += 0.8
    if is_headline_like(line):
        score -= 2.0
    if EMAIL_RE.search(line):
        score -= 1.0
    if len(line) <= 12:
        score -= 2.0
    if " | " in line:
        score -= 1.0
    return score


def clean_article_content(
    raw_content: str,
    title: str,
    doc_freq: Counter[str],
    start_freq: Counter[str],
    end_freq: Counter[str],
    total_docs: int,
    min_chars: int,
) -> CleanResult:
    lines = split_lines(raw_content)
    raw_char_count = len(normalize_text(raw_content))
    segments: list[tuple[int, list[tuple[str, float]]]] = []
    current: list[tuple[str, float]] = []
    current_start = 0
    soft_gap = 0

    def flush() -> None:
        nonlocal current, current_start, soft_gap
        if current:
            segments.append((current_start, current))
        current = []
        current_start = 0
        soft_gap = 0

    seen_short_noise: Counter[str] = Counter()
    for idx, line in enumerate(lines):
        if is_boundary_line(line):
            flush()
            continue
        hard_noise = is_hard_noise_line(line, title, doc_freq, start_freq, end_freq, total_docs)
        if hard_noise:
            seen_short_noise[line] += 1
            if current:
                soft_gap += 1
                if soft_gap >= 4:
                    flush()
            continue
        score = line_article_score(line, doc_freq, start_freq, end_freq, title, total_docs)
        if score >= 0.5 or len(compact_for_compare(line)) >= 28:
            if not current:
                current_start = idx
            current.append((line, score))
            soft_gap = 0
        elif current:
            soft_gap += 1
            if soft_gap >= 4:
                flush()
    flush()

    if not segments:
        return CleanResult("", "drop", "no_article_segment", len(lines), 0, raw_char_count, 0, 0.0)

    def segment_score(item: tuple[int, list[tuple[str, float]]]) -> float:
        start_idx, segment = item
        text = "\n".join(line for line, _ in segment)
        char_count = len(compact_for_compare(text))
        sentence_lines = sum(1 for line, _ in segment if is_sentence_like(line))
        headline_lines = sum(1 for line, _ in segment if is_headline_like(line))
        line_count = max(len(segment), 1)
        score = sum(score for _, score in segment)
        score += min(char_count / 250.0, 8.0)
        score += min(sentence_lines, 10) * 1.6
        score -= headline_lines * 1.7
        if sentence_lines == 0:
            score -= 20.0
        if line_count >= 6 and sentence_lines / line_count < 0.35:
            score -= line_count * 1.5
        if headline_lines / line_count > 0.55:
            score -= line_count * 2.0
        score -= start_idx * 0.015
        return score

    best = max(segments, key=segment_score)
    output_lines: list[str] = []
    seen_lines: set[str] = set()
    for line, _ in best[1]:
        key = compact_for_compare(line)
        if not key:
            continue
        if key in seen_lines and len(key) < 90:
            continue
        seen_lines.add(key)
        output_lines.append(line)

    while output_lines and is_hard_noise_line(output_lines[0], title, doc_freq, start_freq, end_freq, total_docs):
        output_lines.pop(0)
    while output_lines and is_hard_noise_line(output_lines[-1], title, doc_freq, start_freq, end_freq, total_docs):
        output_lines.pop()

    clean_text = "\n".join(output_lines).strip()
    clean_chars = len(compact_for_compare(clean_text))
    quality = segment_score(best)
    if clean_chars < min_chars:
        return CleanResult(
            clean_text,
            "review" if clean_chars >= 70 else "drop",
            "too_short_after_cleaning",
            len(lines),
            len(output_lines),
            raw_char_count,
            len(clean_text),
            quality,
        )
    if len(output_lines) == 1 and clean_chars < 220:
        return CleanResult(
            clean_text,
            "review",
            "snippet_like_single_line",
            len(lines),
            len(output_lines),
            raw_char_count,
            len(clean_text),
            quality,
        )
    return CleanResult(
        clean_text,
        "keep",
        "article_body_extracted",
        len(lines),
        len(output_lines),
        raw_char_count,
        len(clean_text),
        quality,
    )


def normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    title = choose_field(record, TITLE_KEYS, "title").value
    content_choice = choose_field(record, CONTENT_KEYS, "content")
    url = choose_field(record, URL_KEYS, "meta").value
    publisher = choose_field(record, PUBLISHER_KEYS, "meta").value
    published_at = choose_field(record, DATE_KEYS, "meta").value
    return {
        "title": normalize_text(title),
        "content": normalize_text(content_choice.value),
        "url": normalize_text(url),
        "publisher": normalize_text(publisher),
        "published_at": normalize_text(published_at),
        "content_field": content_choice.key,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append(
                {
                    "_load_error": "json_decode_error",
                    "_line_no": line_no,
                    "_error": str(exc),
                    "content": line,
                }
            )
            continue
        if isinstance(value, dict):
            value["_input_file"] = str(path)
            value["_line_no"] = line_no
            records.append(value)
    return records


def make_content_hash(title: str, content: str) -> str:
    key = compact_for_compare(title) + "\n" + compact_for_compare(content)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
            count += 1
    return count


def write_report_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "status",
        "reason",
        "title",
        "publisher",
        "published_at",
        "url",
        "raw_char_count",
        "clean_char_count",
        "raw_line_count",
        "clean_line_count",
        "quality_score",
        "input_file",
        "line_no",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def preprocess(input_dir: Path, pattern: str, output_dir: Path, min_chars: int) -> dict[str, Any]:
    generated_names = {
        "cleaned_news.jsonl",
        "review_news.jsonl",
        "dropped_news.jsonl",
    }
    input_paths = sorted(
        path
        for path in input_dir.rglob(pattern)
        if path.is_file() and path.name not in generated_names and not is_under(path, output_dir)
    )
    raw_records: list[dict[str, Any]] = []
    for path in input_paths:
        raw_records.extend(load_jsonl(path))

    doc_freq, start_freq, end_freq = build_line_stats(raw_records)
    total_docs = max(len(raw_records), 1)

    output_dir.mkdir(parents=True, exist_ok=True)
    kept_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    dropped_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()

    for record in raw_records:
        norm = normalized_record(record)
        result = clean_article_content(
            norm["content"],
            norm["title"],
            doc_freq,
            start_freq,
            end_freq,
            total_docs,
            min_chars,
        )
        row = {
            "title": norm["title"],
            "content": result.content,
            "publisher": norm["publisher"],
            "published_at": norm["published_at"],
            "url": norm["url"],
            "source": record.get("source", ""),
            "keyword_matched": record.get("keyword_matched", ""),
            "category": record.get("category", ""),
            "file_path": record.get("file_path", ""),
            "input_file": record.get("_input_file", ""),
            "line_no": record.get("_line_no", ""),
            "preprocess": {
                "status": result.status,
                "reason": result.reason,
                "content_field": norm["content_field"],
                "raw_line_count": result.raw_line_count,
                "clean_line_count": result.clean_line_count,
                "raw_char_count": result.raw_char_count,
                "clean_char_count": result.clean_char_count,
                "quality_score": round(result.quality_score, 3),
            },
        }

        report_row = {
            "status": result.status,
            "reason": result.reason,
            "title": norm["title"],
            "publisher": norm["publisher"],
            "published_at": norm["published_at"],
            "url": norm["url"],
            "raw_char_count": result.raw_char_count,
            "clean_char_count": result.clean_char_count,
            "raw_line_count": result.raw_line_count,
            "clean_line_count": result.clean_line_count,
            "quality_score": round(result.quality_score, 3),
            "input_file": record.get("_input_file", ""),
            "line_no": record.get("_line_no", ""),
        }

        if result.status == "keep":
            url_key = norm["url"].strip()
            content_hash = make_content_hash(norm["title"], result.content)
            if url_key and url_key in seen_urls:
                row["preprocess"]["status"] = "drop"
                row["preprocess"]["reason"] = "duplicate_url"
                report_row["status"] = "drop"
                report_row["reason"] = "duplicate_url"
                dropped_rows.append(row)
                continue
            if content_hash in seen_hashes:
                row["preprocess"]["status"] = "drop"
                row["preprocess"]["reason"] = "duplicate_content"
                report_row["status"] = "drop"
                report_row["reason"] = "duplicate_content"
                dropped_rows.append(row)
                continue
            if url_key:
                seen_urls.add(url_key)
            seen_hashes.add(content_hash)
            kept_rows.append(row)
        elif result.status == "review":
            review_rows.append(row)
        else:
            dropped_rows.append(row)

    report_rows = []
    for row in kept_rows + review_rows + dropped_rows:
        prep = row.get("preprocess", {})
        report_rows.append(
            {
                "status": prep.get("status", ""),
                "reason": prep.get("reason", ""),
                "title": row.get("title", ""),
                "publisher": row.get("publisher", ""),
                "published_at": row.get("published_at", ""),
                "url": row.get("url", ""),
                "raw_char_count": prep.get("raw_char_count", ""),
                "clean_char_count": prep.get("clean_char_count", ""),
                "raw_line_count": prep.get("raw_line_count", ""),
                "clean_line_count": prep.get("clean_line_count", ""),
                "quality_score": prep.get("quality_score", ""),
                "input_file": row.get("input_file", ""),
                "line_no": row.get("line_no", ""),
            }
        )

    kept_path = output_dir / "cleaned_news.jsonl"
    review_path = output_dir / "review_news.jsonl"
    dropped_path = output_dir / "dropped_news.jsonl"
    report_path = output_dir / "preprocess_report.csv"

    def _slim(row: dict) -> dict:
        return {
            "keyword_matched": row.get("keyword_matched", ""),
            "category":        row.get("category", ""),
            "title":           row.get("title", ""),
            "content":         row.get("content", ""),
            "publisher":       row.get("publisher", ""),
            "published_at":    row.get("published_at", ""),
            "url":             row.get("url", ""),
            "source":          row.get("source", ""),
        }

    write_jsonl(kept_path, (_slim(r) for r in kept_rows))
    write_jsonl(review_path, review_rows)
    write_jsonl(dropped_path, dropped_rows)
    write_report_csv(report_path, report_rows)

    return {
        "input_files": [str(path) for path in input_paths],
        "input_records": len(raw_records),
        "kept": len(kept_rows),
        "review": len(review_rows),
        "dropped": len(dropped_rows),
        "output_dir": str(output_dir),
        "files": {
            "cleaned": str(kept_path),
            "review": str(review_path),
            "dropped": str(dropped_path),
            "report": str(report_path),
        },
        "drop_reasons": Counter(row["preprocess"]["reason"] for row in dropped_rows),
        "review_reasons": Counter(row["preprocess"]["reason"] for row in review_rows),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean crawled news JSONL into article-only JSONL.")
    parser.add_argument("--input-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--pattern", default="news_*.jsonl")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "preprocessed")
    parser.add_argument("--min-chars", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    summary = preprocess(args.input_dir, args.pattern, args.output_dir, args.min_chars)
    printable = dict(summary)
    printable["drop_reasons"] = dict(summary["drop_reasons"])
    printable["review_reasons"] = dict(summary["review_reasons"])
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))