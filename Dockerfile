FROM python:3.13-slim

WORKDIR /app

# Accept proxy config as build args (for sandbox/CI environments)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG PROXY_CA_CERT_B64

# Install proxy CA cert if provided
RUN if [ -n "$PROXY_CA_CERT_B64" ]; then \
      echo "$PROXY_CA_CERT_B64" | base64 -d > /usr/local/share/ca-certificates/proxy-ca.crt && \
      update-ca-certificates && \
      export PIP_CERT=/usr/local/share/ca-certificates/proxy-ca.crt; \
    fi

COPY pyproject.toml .
COPY polyarb/ polyarb/

RUN pip install --no-cache-dir -e ".[dev,trade]"

# Run as non-root
RUN useradd --create-home appuser
USER appuser

EXPOSE 8080

ENTRYPOINT ["python", "-m", "polyarb"]
