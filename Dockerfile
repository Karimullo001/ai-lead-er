FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY core ./core
COPY api ./api
COPY demo ./demo
RUN pip install --no-cache-dir -e .
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
