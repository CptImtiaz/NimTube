FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x scripts/railway_run.sh
ENV DATA_DIR=/data OUTPUT_DIR=/data/output PYTHONUNBUFFERED=1
# Railway runs this on the cron schedule (service setting), then the container exits.
CMD ["sh", "scripts/railway_run.sh"]
