# syntax=docker/dockerfile:1

# Imagen ligera de Python para ejecutar la API del backend.
FROM python:3.14-slim

# Ajustes del proceso Python, pip y las ubicaciones de caché de modelos.
# La caché queda bajo el directorio del usuario no privilegiado creado abajo.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/appuser \
    XDG_CACHE_HOME=/home/appuser/.cache \
    HF_HOME=/home/appuser/.cache/huggingface

# Dependencias nativas usadas por Docling/OpenCV (libGL y libglib) y PyTorch (libgomp).
# Se limpian las listas de apt al terminar para reducir el tamaño de la imagen.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Se copian primero las dependencias para reutilizar esta capa si solo cambia el código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Usuario sin inicio de sesión interactivo para ejecutar la aplicación sin privilegios de root.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

RUN mkdir -p /app/data /home/appuser/.cache \
    && chown -R appuser:appuser /app /home/appuser

COPY --chown=appuser:appuser . .

USER appuser

# Puerto HTTP que escucha Uvicorn dentro del contenedor; no publica el puerto del host.
EXPOSE 8000

# Arranca la API en todas las interfaces del contenedor. Se usa un solo worker porque
# los grafos y supervisores de sesión mantienen una caché en memoria por proceso.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
