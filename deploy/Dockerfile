# Backend, LLM gateway and the one-shot level seeder share this image.
FROM python:3.12-slim
WORKDIR /app
COPY autopilot-backend/requirements.txt /app/autopilot-backend/requirements.txt
RUN pip install --no-cache-dir -r /app/autopilot-backend/requirements.txt
COPY autopilot-backend /app/autopilot-backend
COPY integration /app/integration
COPY tools/seed_levels.py /app/tools/seed_levels.py
# The SDK builds that may play (written by deploy/package-downloads.sh; a checkout
# without one lets every SDK play). requirements.txt only keeps the COPY valid.
COPY autopilot-backend/requirements.txt autopilot-sdk-binary/bin/sdk-manifest.jso[n] /app/sdkinfo/
ENV PYTHONUNBUFFERED=1
