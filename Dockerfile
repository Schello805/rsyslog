FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose Web dashboard port and Syslog ports
EXPOSE 8000
EXPOSE 5514/udp
EXPOSE 5514/tcp
EXPOSE 514/udp
EXPOSE 514/tcp

# Run FastAPI and Syslog server
CMD ["python", "main.py"]
