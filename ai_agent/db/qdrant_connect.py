"""
db/qdrant_connect.py
Qdrant 연결 모드 선택 — 로컬 파일 / 서버 모드 둘 다 지원

테스트 및 debate 에이전트 등 로컬/서버를 모두 써야 하는 경우 이 파일 사용.
일반 팀원들(카드 임베딩 등)은 db/qdrant_client.py 사용.

사용법:
    from db.qdrant_connect import get_qdrant_client

    client = get_qdrant_client()            # .env QDRANT_MODE 기준 자동
    client = get_qdrant_client("local")     # 로컬 파일
    client = get_qdrant_client("server")    # Qdrant 서버
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

_DEFAULT_LOCAL_PATH = str(Path(__file__).parent.parent / "qdrant_storage")
_DEFAULT_SERVER_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
_DEFAULT_MODE       = os.getenv("QDRANT_MODE", "local")


def get_qdrant_client(
    mode: str = "auto",
    local_path: str | None = None,
    server_url: str | None = None,
) -> QdrantClient:
    """
    Parameters
    ----------
    mode       : "local" | "server" | "auto"  (.env QDRANT_MODE 기본 "local")
    local_path : 로컬 저장 경로 (None → qdrant_storage/)
    server_url : 서버 URL (None → QDRANT_URL 환경변수)
    """
    resolved = _DEFAULT_MODE if mode == "auto" else mode

    if resolved == "server":
        url = server_url or _DEFAULT_SERVER_URL
        print(f"[Qdrant] 서버 모드: {url}")
        return QdrantClient(url=url)

    # 로컬 파일 모드
    path = local_path or os.getenv("QDRANT_PATH", _DEFAULT_LOCAL_PATH)
    Path(path).mkdir(parents=True, exist_ok=True)

    # 비정상 종료 락 파일 제거
    lock_file = Path(path) / ".lock"
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass

    # 단일 프로세스 환경 락 비활성화
    try:
        import portalocker.portalocker as _ppl
        _ppl.PosixLocker.lock   = lambda self, f, fl: None
        _ppl.PosixLocker.unlock = lambda self, f, fl: None
    except Exception:
        pass

    print(f"[Qdrant] 로컬 모드: {path}")
    return QdrantClient(path=path)
