# Imagen de la aplicacion (API + reconocimiento facial + panel web)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=America/Bogota

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server
COPY server/requirements.txt .
# En el servidor no hay pantalla: OpenCV "headless" pesa menos y no necesita librerias graficas
RUN sed -i 's/^opencv-contrib-python\b/opencv-contrib-python-headless/' requirements.txt \
 && pip install --no-cache-dir -r requirements.txt

COPY server/ /app/server/
COPY frontend/ /app/frontend/

RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p /datos && chown app /datos
USER app

EXPOSE 8000
# Aplica el esquema de la base (idempotente) y arranca el servidor
CMD ["sh", "-c", "python preparar_bd.py --auto && exec python app.py"]
