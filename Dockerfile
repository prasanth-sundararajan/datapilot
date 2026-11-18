FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    MPLCONFIGDIR=/tmp/matplotlib MPLBACKEND=Agg DATAPILOT_IN_CONTAINER=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The container is the sandbox: AI-written code runs as an unprivileged user, only /app/sandbox is writable.
RUN useradd -m appuser && mkdir -p /app/sandbox && chown -R appuser /app/sandbox
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s CMD python -c "import os,urllib.request;p=os.environ.get('STREAMLIT_SERVER_BASE_URL_PATH','').strip('/');urllib.request.urlopen('http://localhost:8501/'+(p+'/' if p else '')+'_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
