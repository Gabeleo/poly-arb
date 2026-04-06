FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as non-root
RUN useradd --create-home appuser && \
    mkdir -p /cache && chown appuser:appuser /cache
USER appuser

ENV TRANSFORMERS_CACHE=/cache

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
