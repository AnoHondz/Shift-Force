import os

bind = "0.0.0.0:" + os.getenv("PORT", "8000")
workers = 2
threads = 4
worker_class = "gthread"
timeout = 30
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
preload_app = False
accesslog = "-"
errorlog = "-"
# Endpoint telemetry is emitted by the app; access logs exclude queries and cookies.
access_log_format = "%(p)s %(m)s %(s)s %(L)s"
# Render's trusted forwarding is handled once by the app's explicit ProxyFix.
forwarded_allow_ips = ""
