# GTM Intelligence Platform — runtime image (Streamlit app).
# Single service; runs behind Caddy (TLS + password) in docker-compose. Secrets are injected at
# runtime via env — never baked in. Playwright and dev tooling are intentionally excluded.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (better layer caching).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code only (see .dockerignore for what's excluded).
# legacy_qualification.py sits at the repo root but is registered as a page in app.py, so it must
# be copied alongside app.py — omitting it crashes st.navigation() at startup.
COPY app.py legacy_qualification.py ./
COPY pipeline ./pipeline
COPY pages ./pages
COPY prompts ./prompts
COPY assets ./assets
COPY .streamlit ./.streamlit

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status==200 else 1)"

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
