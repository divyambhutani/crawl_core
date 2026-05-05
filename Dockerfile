FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 libxrandr2 libxcomposite1 libxdamage1 \
    && rm -rf /var/lib/apt/lists/*

# Python deps (no torch/transformers — classification via Vertex AI)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download browser
RUN playwright install chromium && playwright install-deps chromium

COPY ./app ./app

EXPOSE 8000

ENV CLASSIFIER_BACKEND=vertex
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
