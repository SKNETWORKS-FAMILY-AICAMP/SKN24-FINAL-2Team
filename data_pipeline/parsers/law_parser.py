"""
법령 전처리
law_collector.py가 저장한 law_grouped.json을 전처리하여 law_grouped_clean.json 저장

단독 실행: python law_parser.py
파이프라인: main.py에서 preprocess() 호출
"""
import json
import re
from pathlib import Path


def clean_basic_text(value):
    if value is None or not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None
    return re.sub(r"[ \t]+", " ", text)


def clean_article_text(value):
    if value is None or not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None

    # 줄바꿈 통일
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    # 과한 공백 정리
    text = re.sub(r"[ ]+", " ", text)

    # 1단계: 개정/신설/삭제 이력을 임시 토큰으로 보호
    placeholders = {}

    def replace_tag(m):
        key = f"__TAG{len(placeholders)}__"
        placeholders[key] = m.group(0)
        return key

    text = re.sub(r"<(?:개정|신설|삭제)[^>]*>", replace_tag, text)
    text = re.sub(r"\[[^\]]*(?:개정|신설|삭제|제목개정|전문개정)[^\]]*\]", replace_tag, text)

    # 2단계: 구조 복원
    # 항 번호 앞 줄바꿈
    text = re.sub(r"\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])", r"\n\1", text)

    # 숫자 호: 앞에 .이나 숫자 없을 때만 (날짜 안 깨지게)
    text = re.sub(
        r"(?<![.\d])\s+(?=(?:[1-9]|[1-9][0-9])\.\s*[가-힣A-Za-z\"\u201c\u300c])",
        "\n", text
    )
    text = re.sub(
        r"(?<![.\d])([가-힣A-Za-z0-9\)\]>。.])(?=(?:[1-9]|[1-9][0-9])\.\s*[가-힣A-Za-z\"\u201c\u300c])",
        r"\1\n", text
    )

    # 한글 목: 공백 있는 경우만
    text = re.sub(
    r"(?<!\n)\s*(?=([가-하]\.\s*[가-힣A-Za-z\"\u201c\u300c]))","\n",text)

    # 3단계: 보호했던 이력 복원
    for key, val in placeholders.items():
        text = text.replace(key, " " + val)

    # 줄 단위 정리
    lines = [re.sub(r"[ ]+", " ", l.strip()) for l in text.split("\n") if l.strip()]
    lines = [re.sub(r"^(\d+\.)\s+", r"\1 ", l) for l in lines]
    return "\n".join(lines)


def clean_law(law: dict) -> dict:
    if not isinstance(law, dict):
        return law
    cleaned = {}
    for key, value in law.items():
        if key == "관련정책" and isinstance(value, list):
            cleaned[key] = [
                {k: clean_basic_text(v) for k, v in p.items()}
                if isinstance(p, dict) else p
                for p in value
            ]
        elif key == "조문" and isinstance(value, list):
            cleaned[key] = [
                {k: (clean_article_text(v) if k == "조문내용" else clean_basic_text(v))
                 for k, v in a.items()}
                if isinstance(a, dict) else a
                for a in value
            ]
        else:
            cleaned[key] = clean_basic_text(value)
    return cleaned


def preprocess(input_path: Path, output_path: Path) -> dict:
    """
    policy_pipeline.py에서 호출하는 진입점.
    input_path:  law_grouped.json
    output_path: law_grouped_clean.json
    """
    laws = json.loads(input_path.read_text(encoding="utf-8"))
    cleaned = [clean_law(law) for law in laws]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "laws":     len(cleaned),
        "articles": sum(len(law.get("조문", [])) for law in cleaned),
        "output":   str(output_path),
    }


if __name__ == "__main__":
    from pathlib import Path
    BASE_DIR    = Path(__file__).resolve().parent
    input_path  = BASE_DIR / "data" / "laws" / "processed" / "law_grouped.json"
    output_path = BASE_DIR / "data" / "laws" / "processed" / "law_grouped_clean.json"
    result = preprocess(input_path, output_path)
    print(f"전처리 완료: {result['laws']}개 법령 / {result['articles']}개 조문 → {result['output']}")