# Dockerfile (super simple)
FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app + data
COPY app.py .
COPY data ./data

# Railway will set $PORT; default to 8080 for local
ENV PORT=8080

# Start with gunicorn
CMD ["bash", "-lc", "gunicorn -w 2 -k gthread -b 0.0.0.0:${PORT} app:app"]
