FROM python:3.12-slim

# curl is used for outbound provider calls (some WAFs/Cloudflare reject
# library user-agents like Python-urllib but allow a normal client).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py
CMD ["python", "/app/app.py"]
