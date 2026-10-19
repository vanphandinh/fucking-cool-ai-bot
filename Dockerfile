FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

# Cài dependencies trước để tận dụng layer cache.
# Cài từ requirements.lock.txt (pin ==): requirements.txt chỉ nêu range, nên rebuild
# cùng commit có thể lấy version mới hơn mà không có diff nào giải thích.
# --no-deps: lock đã liệt kê đủ closure, và pip sẽ không tự nâng cấp ngoài lock.
COPY requirements.lock.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade 'pip>=26.2' \
    && python -m pip install --no-cache-dir --no-deps -r requirements.lock.txt

# Copy mã nguồn và provider catalog runtime
COPY app ./app
COPY config ./config

# Chạy bằng user non-root
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "app.main"]
