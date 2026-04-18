FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && pip install --no-cache-dir -e .
# TimesFM 2.5 (optional — only installed when ENABLE_TIMESFM=true at build time)
ARG INSTALL_TIMESFM=false
RUN if [ "$INSTALL_TIMESFM" = "true" ]; then pip install --no-cache-dir timesfm torch; fi

COPY . /app

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
