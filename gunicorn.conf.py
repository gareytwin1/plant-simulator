"""Production serving config: `gunicorn -c gunicorn.conf.py app.main:app`.

Exactly one worker process (R7). `SessionRegistry`, the schedulers and the
API rate limiter all live in process memory, so a second process would split
one browser's session across two plants and double the session bound. Scale
with threads instead. Do not add `preload_app`: the registry is created at
import and the scheduler threads must start in the serving process.

`timeout` is Gunicorn's worker-heartbeat limit, not a request limit: a gthread
worker's main thread keeps the heartbeat alive while request threads block, so
a long-lived stream is not killed by it. Each open stream holds one request
thread for its whole life, so `threads` must exceed streams plus ordinary
requests. The scheduler threads are separate from this pool.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 1
worker_class = "gthread"
threads = 64
accesslog = "-"
