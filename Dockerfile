FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# En producción puede precargarse e5: --build-arg CACHE_E5=true
ARG CACHE_E5=false
RUN if [ "$CACHE_E5" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small', cache_folder='/app/models')"; \
    fi

ENV HF_HOME=/app/models \
    PYTHONUNBUFFERED=1

COPY . .

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
