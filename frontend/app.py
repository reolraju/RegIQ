"""RegIQ frontend — serves the React/HTML single-page app and proxies
queries to the FastAPI backend so the browser only talks to one origin.

The static assets in `static/` are a port of the RegIQ design system's
web UI kit (see https://github.com/reolraju/RegIQ design system bundle).
"""
import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
STATIC_DIR = Path(__file__).parent / "static"

# Anonymous usage analytics (PostHog). Configured purely via env vars so the
# project key stays out of the repo and is optional — when unset, the frontend
# analytics layer stays dormant. POSTHOG_KEY is a client-side project key and
# is meant to be public, so exposing it to the browser is expected.
POSTHOG_KEY = os.getenv("POSTHOG_KEY", "")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")

app = FastAPI(title="RegIQ Frontend", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/query")
async def proxy_query(request: Request):
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{BACKEND_URL}/query", json=payload)
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to the backend. Make sure the backend service is running.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Backend request timed out. The model may be busy — please try again.",
        )

    return JSONResponse(
        status_code=response.status_code,
        content=response.json() if response.headers.get("content-type", "").startswith("application/json") else {"detail": response.text},
    )


@app.get("/env.js")
async def env_js():
    """Expose runtime config (PostHog key/host) to the browser as a tiny JS
    file. Defined before the SPA catch-all so it isn't shadowed by it."""
    config = {"posthogKey": POSTHOG_KEY, "posthogHost": POSTHOG_HOST}
    body = f"window.REGIQ_ANALYTICS = {json.dumps(config)};"
    return Response(content=body, media_type="application/javascript")


@app.get("/{full_path:path}")
async def spa(full_path: str):
    """Serve index.html for the root and any unknown path (SPA fallback)."""
    return FileResponse(STATIC_DIR / "index.html")
