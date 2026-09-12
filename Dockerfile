FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENMETRIC_HOST=0.0.0.0 \
    OPENMETRIC_PORT=8099 \
    OPENMETRIC_DATABASE_URL=sqlite:////data/openmetric.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The database lives on a volume so your history survives a container rebuild.
RUN mkdir -p /data && useradd --create-home --uid 10001 openmetric && chown -R openmetric /data /app
USER openmetric
VOLUME ["/data"]

EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8099/api/health')"

CMD ["openmetric", "serve"]
