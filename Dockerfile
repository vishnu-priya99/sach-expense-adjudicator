# Use official lightweight Python image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install production dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir fastapi uvicorn google-cloud-bigquery google-genai python-dotenv pillow python-multipart jinja2 mcp

# Copy entire project directory
COPY . .

# Expose target port
EXPOSE 8000

# Run FastAPI backend (which hosts the Agent Control Room frontend natively)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
