FROM python:3.11-slim

WORKDIR /app

# Install build deps for packages that compile C extensions (asyncpg, spacy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Download spaCy model
RUN python -m spacy download en_core_web_lg

# Copy source
COPY napyclaw.toml .
COPY napyclaw/ napyclaw/

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "napyclaw"]
