# AutoShorts Hub - Raspberry Pi 5 (arm64) hedefli imaj.
#
# Tum Python bagimliliklari linux/arm64 wheel olarak kurulur; imaj olusturma
# sirasinda derleme yapilmaz. Pi 5 uzerinde GHCR'dan cekilip dogrudan calisir.
#
# Yerel derleme:
#   docker build -t autoshorts-hub .
#   docker run -p 8080:8080 -e DATABASE_URL=... autoshorts-hub

FROM python:3.11-slim-bookworm

# Tek satirda birden fazla ENV: katman sayisini azaltir.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

WORKDIR /app

# tzdata: ZoneInfo("Europe/Istanbul") icin sistem saat dilimi veritabani.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Bagimliliklari once kur: kod degisse de bu katman önbellekten gelir.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# Yuklenen videolar bu klasore yazilir; compose ile birime baglanir.
RUN mkdir -p /data/uploads

EXPOSE 8080

# Saglik kontrolu: ek paket kurmamak icin curl yerine Python kullanilir.
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=8).status == 200 else 1)"]

# Tek isci: Pi 5'in 4 cekirdegi arka plan zamanlayici ve video yuklemeleri
# ile paylasilir; uvicorn'un coklu isci modu ayni zamanlayiciyi tekrar baslatirdi.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
