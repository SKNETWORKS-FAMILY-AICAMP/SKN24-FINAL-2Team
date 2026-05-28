"""
embedding_hf/chunkers.py
청킹 전략 모듈 (chromadb 등 외부 의존성 없음)

기존 pipeline/utils.py 의 청킹 함수를 독립적으로 재구현
- chunk_fixed    : 고정 길이 청킹
- chunk_sentence : 문장 단위 청킹 (한국어)
- get_chunker    : 전략 이름 → 함수 반환
"""
from __future__ import annotations

import re
from typing import List, Callable

# ── 기본 설정값 (기존 config.py와 동일) ──────────────────────────────────────
FIXED_CHUNK_SIZE    = 500
FIXED_CHUNK_OVERLAP = 50
SENTENCE_MAX_CHARS  = 600


def chunk_fixed(
    text: str,
    size: int = FIXED_CHUNK_SIZE,
    overlap: int = FIXED_CHUNK_OVERLAP,
) -> List[str]:
    """고정 길이 청킹 (overlap 포함)"""
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start: start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def _split_sentences_ko(text: str) -> List[str]:
    """한국어 문장 분리 (regex 기반)"""
    pattern = r'(?<=[.!?])\s+|(?<=[다요까야죠네군])\s+'
    parts = re.split(pattern, text)
    return [p.strip() for p in parts if p.strip()]


def chunk_sentence(
    text: str,
    max_chars: int = SENTENCE_MAX_CHARS,
) -> List[str]:
    """문장 단위 청킹 (max_chars 이내로 묶음)"""
    sentences = _split_sentences_ko(text)
    chunks, current = [], ""
    for sent in sentences:
        if len(current) + len(sent) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sent
        else:
            current += (" " if current else "") + sent
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]


def get_chunker(strategy: str) -> Callable[[str], List[str]]:
    """
    전략 이름 → 청킹 함수 반환

    Parameters
    ----------
    strategy : "fixed" | "sentence"
        (semantic은 OpenAI API 필요 → 여기서는 미지원)
    """
    if strategy == "fixed":
        return chunk_fixed
    elif strategy == "sentence":
        return chunk_sentence
    else:
        raise ValueError(
            f"지원하지 않는 청킹 전략: '{strategy}'\n"
            f"사용 가능: 'fixed', 'sentence'"
        )
