FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Handle Requirements
COPY wsgi/requirements.txt wsgi/requirements.txt

RUN sed -i '/gdal/d' wsgi/requirements.txt

# Python Dependencies
RUN pip install --no-cache-dir -r wsgi/requirements.txt

# For Correct GDAL Version
RUN pip install "gdal==$(gdal-config --version)"

# 6. Copy Application Code
COPY wsgi/ wsgi/

EXPOSE 5000
CMD ["python", "wsgi/server.py"]