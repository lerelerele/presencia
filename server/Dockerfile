FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 TZ=Europe/Madrid
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY festivos.example.txt .
COPY tools ./tools
VOLUME /srv/data
