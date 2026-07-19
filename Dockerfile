# Active Impact app image. Self-contained: applies the schema (idempotent) then serves.
FROM python:3.12-slim
WORKDIR /srv
# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
# schema apply is idempotent (IF NOT EXISTS everywhere) -- safe on every boot.
# --proxy-headers trusts Caddy's X-Forwarded-Proto so request.url.scheme is https
# in prod (safe: the app has no published ports -- only Caddy can reach it).
CMD ["sh", "-c", "python -m app.db --init && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
