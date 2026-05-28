"""
pipeline/pipelines/policy_card/tools.py
"""
import logging
from ddgs import DDGS

logger = logging.getLogger(__name__)

def web_search(query: str, max_results: int = 3) -> str:
    """덕덕고(DuckDuckGo)를 이용해 웹 검색을 수행하고 결과를 문자열로 반환합니다."""
    logger.info(f"🔍 [에이전트 검색 실행]: '{query}'")
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        
        if not results:
            return "검색 결과가 없습니다."
            
        formatted_results = []
        for i, res in enumerate(results):
            formatted_results.append(f"[{i+1}] {res.get('title', '')}\n내용: {res.get('body', '')}")
        
        return "\n\n".join(formatted_results)
        
    except Exception as e:
        logger.error(f"검색 툴 실행 중 오류 발생: {e}")
        return f"검색 실패: {str(e)}"

# LLM에게 쥐여줄 도구 설명서 (OpenAI Function Calling 규격)
SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "정책이나 법안과 관련된 최신 통계, 여론, 부작용, 비판 기사 등을 웹에서 검색할 때 사용합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색할 구체적인 키워드 (예: '청년도약계좌 단점', '전세보증금 지원 실효성')"
                }
            },
            "required": ["query"]
        }
    }
}