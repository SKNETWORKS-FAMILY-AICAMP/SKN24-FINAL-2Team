import multiprocessing

# 바인딩
bind        = "0.0.0.0:8000"

# 워커 수 — CPU 코어 * 2 + 1 (컨테이너 환경에서 2~4가 적당)
workers      = min(multiprocessing.cpu_count() * 2 + 1, 4)
worker_class = "uvicorn.workers.UvicornWorker"  # SSE async 지원
threads      = 1              # UvicornWorker는 이벤트루프 기반, threads=1
timeout      = 300            # SSE 장기연결 + AI 응답 대기
graceful_timeout = 30
keepalive   = 5

# 로깅
accesslog   = "-"            # stdout
errorlog    = "-"            # stderr
loglevel    = "info"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'

# 프로세스
preload_app = False          # UvicornWorker와 함께 쓸 때 fork 후 이벤트루프 꼬임 방지
max_requests        = 1000   # 메모리 누수 방지용 워커 재시작
max_requests_jitter = 100