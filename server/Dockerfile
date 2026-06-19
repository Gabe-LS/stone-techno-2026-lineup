FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py .
RUN useradd -r -s /usr/sbin/nologin app && chown -R app:app /app
USER app
EXPOSE 8080
# Caddy (on the same Docker network) is the only ingress; it strips and re-sets forwarded headers
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
