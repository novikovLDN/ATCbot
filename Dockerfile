FROM python:3.11.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# SECURITY: Create non-root user before copying files (principle of least privilege)
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home appuser

WORKDIR /app

COPY --chown=appuser:appuser requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

# Explicit copy so migrations are never excluded by build context or future .dockerignore
COPY --chown=appuser:appuser migrations/ ./migrations/

USER appuser

CMD ["python", "main.py"]
