from .debate import debate_router
from .chatbot import router as chatbot_router
from .cards import router as cards_router
from .embed import router as embed_router

__all__ = ["debate_router", "chatbot_router", "cards_router", "embed_router"]
