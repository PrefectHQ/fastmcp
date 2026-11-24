# Build from the repository root:
#   docker build -f scripts/floor_receiver/Dockerfile .
# The sandbox runs it on App Engine instead (deploy-appengine.sh): org policy
# keeps Cloud Run private.
FROM python:3.15-slim
WORKDIR /app
COPY scripts/triage_floor_feed.py scripts/floor_receiver/main.py ./
ENV PYTHONUNBUFFERED=1
USER nobody
CMD ["python", "main.py"]
