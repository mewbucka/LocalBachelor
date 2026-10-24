"""FastAPI routers (architecture section 12, API contracts).

REST + SSE under ``/api``; session-cookie auth on every endpoint except
``/healthz``, login, and the first-run wizard.
"""
