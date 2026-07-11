# Use a slim, lightweight official Python image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Install system dependencies (if any are needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies directly
RUN pip install --no-cache-dir \
    mcp \
    starlette \
    uvicorn

# Copy the server source and data directories
COPY server.py /app/server.py
COPY data/ /app/data/

# Expose default Cloud Run port (8080)
EXPOSE 8080

# Environment variables (fallbacks)
ENV PORT=8080
ENV INVOICES_FILE_PATH=/app/data/hotel_invoices.json

# Run the MCP server
CMD ["python", "server.py"]
