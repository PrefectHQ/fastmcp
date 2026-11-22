# Build from the repository root:
#   gcloud run deploy floor-receiver --source . (with this Dockerfile), or
#   docker build -f scripts/floor_receiver/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY scripts/triage_floor_feed.py scripts/floor_receiver/main.py ./
ENV PYTHONUNBUFFERED=1
USER nobody
CMD ["python", "main.py"]
