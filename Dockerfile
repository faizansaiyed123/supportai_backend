FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./

# Install core deps (excluding sentence-transformers which pulls huge CUDA torch)
RUN pip install --no-cache-dir \
    "fastapi>=0.115,<0.116" \
    "uvicorn[standard]>=0.32,<0.33" \
    "pydantic>=2.9,<3.0" \
    "pydantic-settings>=2.5,<3.0" \
    "sqlalchemy>=2.0,<2.1" \
    "alembic>=1.13,<1.14" \
    "asyncpg>=0.29,<0.30" \
    "psycopg[binary]>=3.2,<3.3" \
    "pgvector>=0.3,<0.4" \
    "argon2-cffi>=23.1,<24.0" \
    "pyjwt>=2.9,<3.0" \
    "procrastinate[psycopg]>=2.13,<3.0" \
    "httpx>=0.27,<0.28" \
    "python-multipart>=0.0.9" \
    "email-validator>=2.2,<3.0" \
    "structlog>=24.4,<25.0" \
    "pypdf>=5.0,<6.0" \
    "beautifulsoup4>=4.12,<5.0" \
    "markdown-it-py>=3.0,<4.0" \
    "python-magic>=0.4,<0.5" \
    "tiktoken>=0.8,<0.9" \
    -r requirements-dev.txt

# Install CPU-only torch (much smaller than CUDA build), then sentence-transformers separately
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir "sentence-transformers>=3.1,<4.0"

COPY . .
RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["sh", "scripts/start.sh"]
