FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install pimemento with postgres + local embeddings
COPY pyproject.toml README.md requirements.txt requirements-postgres.txt ./
COPY src/ src/
RUN pip install --no-cache-dir ".[postgres,embeddings-local]"

# Default environment
ENV MEMORY_BACKEND=postgres
ENV DATABASE_URL=postgresql://pimemento:pimemento@postgres:5432/pimemento
ENV MEMORY_HOST=0.0.0.0
ENV MEMORY_PORT=8770

EXPOSE 8770

CMD ["pimemento", "--transport", "streamable-http"]
