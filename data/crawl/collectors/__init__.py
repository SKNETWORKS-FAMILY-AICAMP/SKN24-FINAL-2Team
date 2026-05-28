# collectors 패키지
# 새 수집기 추가 시 여기에 import 추가
from .naver import NaverCollector
from .gov24 import Gov24Collector

__all__ = ["NaverCollector", "Gov24Collector"]
