FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libopus0 ffmpeg locales && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose ports (8000 for WebSocket, 8003 for HTTP/OTA)
EXPOSE 8000 8003

CMD ["python", "app.py"]
