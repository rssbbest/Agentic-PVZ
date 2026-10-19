# SDK service for autopilot-backend (its docker-compose expects `sdk:8001`).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home sdk
USER sdk

EXPOSE 8001
CMD ["autopilot", "serve", "--host", "0.0.0.0", "--port", "8001"]
