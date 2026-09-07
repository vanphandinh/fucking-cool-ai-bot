FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

# Cài dependencies trước để tận dụng layer cache
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade 'pip>=26.2' \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copy mã nguồn
COPY app ./app

# Chạy bằng user non-root
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "app.main"]
