FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ARTIFACT_ROOT=/data/artifacts
WORKDIR /app
COPY requirements-runtime.lock ./
RUN pip install --no-cache-dir -r requirements-runtime.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home pipeline && mkdir -p /data/artifacts && chown -R pipeline:pipeline /data
USER pipeline
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "pipeline.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765"]
