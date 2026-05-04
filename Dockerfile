FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 libxrandr2 libxcomposite1 libxdamage1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first (separate layer, cached)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Remaining Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download models into image
RUN playwright install chromium && playwright install-deps chromium
RUN python -m spacy download en_core_web_sm
RUN python -c "from transformers import pipeline; pipeline('zero-shot-classification', model='facebook/bart-large-mnli')"

COPY ./app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
