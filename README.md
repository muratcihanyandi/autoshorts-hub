# AutoShorts Hub

YouTube kanallarınızı **7/24 izleyen**, istatistiklerini zaman serisi olarak
PostgreSQL'de toplayan ve **çok kanallı yayınları kural motoruyla yöneten**
pasif gelir asistanı. Raspberry Pi 5 (CasaOS) üzerinde tek Docker konteyneri
olarak çalışacak şekilde tasarlandı.

```
Imaj: ghcr.io/muratcihanyandi/autoshorts-hub:latest   (linux/arm64 + linux/amd64)
```

## Ne yapar?

- **Sürekli izleme** — Kanal abone/izlenme/video sayıları ve her videonun
  izlenme·begeni·yorum değerleri düzenli aralıklarla (varsayılan 30 dk) toplanır,
  hiçbir geçmiş veri üzerine yazılmaz.
- **Panel** — Abone ve izlenme artışı, günlük **tahmini gelir** grafiği
  (RPM yaklaşımı), son 7 günde öne çıkan videolar, arka plan görevlerinin
  sağlık durumu.
- **Çok kanal yönetimi** — İstediğiniz kadar kanal ekleyin (kanal ID veya @handle).
- **Yayın kural motoru** — *Aynı içerik iki kanala aynı gün gitmez.* Aynı
  içeriğin kanallar arasındaki yayını en az **7 gün** (ayarlanabilir) arayla
  yapılır; "Otomatik dağıt" modu seçilen kanallara kademeli plan kurar
  (1. kanal bugün, 2. kanal +7 gün, 3. kanal +14 gün…).
- **Kanala özel kurallar** — Yayın penceresi (ör. 17:00–21:00), günlük yükleme
  sınırı, RPM değeri kanal bazında ayarlanır.
- **YouTube'a doğrudan yükleme** — Kanal başına OAuth bağlantısı; planlanan
  içerik süresi geldiğinde gizli (private) olarak yüklenir.

## Mimari

| Katman | Teknoloji |
|---|---|
| Web arayüzü | Tek sayfa uygulama (vanilla JS + Chart.js, çevrimdışı çalışır) |
| API | FastAPI + Uvicorn |
| Veritabanı | PostgreSQL (SQLAlchemy 2.1, psycopg3) |
| Zamanlayıcı | APScheduler (toplama, yayın işleme, gece veri budama) |
| Harici servis | YouTube Data API v3 (okuma: API anahtarı, yükleme: OAuth) |

Konteyner durumsuzdur (stateless): tüm kalıcı veri PostgreSQL'de, yüklenen
video dosyaları `/data` birimindedir. Konteyner silinip yeniden oluşturulsa
bile hiçbir şey kaybolmaz.

## CasaOS (Raspberry Pi 5) kurulumu

1. **Veritabanı** — PostgreSQL sunucunuzda bir kez:
   ```sql
   CREATE DATABASE autoshorts_hub OWNER muratcihanyandi ENCODING 'UTF8';
   ```

2. **Dosyaları Pi'ye taşıyın** — `docker-compose.yml` ve `.env` dosyası aynı
   klasörde olsun. `.env` içine en az `DATABASE_URL` yazılmalı:
   ```env
   DATABASE_URL=postgresql://muratcihanyandi:********@192.168.2.170:5432/autoshorts_hub
   ```

3. **CasaOS → Uygulamalar → Özel uygulama yükle (compose)** — `docker-compose.yml`
   içeriğini yapıştırın ve açılışta ortam değişkenlerini/`.env` yolunu tanımlayın.
   Veya terminalden:
   ```bash
   docker compose up -d
   ```

4. **Açılış** — `http://<pi-adresi>:8080`. Parola istenmesini istiyorsanız
   `.env` içinde `APP_PASSWORD` belirleyin.

> Imaj herkese açık olduğu için `docker login` gerekmez. Özel repoya çevirirseniz
> Pi üzerinde `docker login ghcr.io` ile PAT tanımlamanız gerekir.

## YouTube API kurulumu

### İstatistik okuma (zorunlu)
1. [Google Cloud Console](https://console.cloud.google.com/) → yeni proje.
2. **YouTube Data API v3**'ü etkinleştirin.
3. **API anahtarı** oluşturun → `.env` içinde `YOUTUBE_API_KEY`.

### Video yükleme (opsiyonel)
1. Aynı projede **OAuth istemci kimliği** oluşturun (tür: *Web uygulaması*).
2. Yetkilendirilmiş yönlendirme URL'si olarak ekleyin:
   `http://<pi-adresi>:8080/api/oauth/callback`
3. `YOUTUBE_CLIENT_ID` ve `YOUTUBE_CLIENT_SECRET` değerlerini `.env`'e yazın,
   uygulamayı yeniden başlatın.
4. Arayüzde **Kanallar** → kanal kartındaki **YouTube'a bağla** ile kanal
   sahibi hesabıyla onay verin. Yalnızca `youtube.upload` izni istenir.

### Kota notu
| İşlem | Maliyet |
|---|---|
| Kanal istatistiği okuma | 1 birim |
| Video istatistiği okuma (50 video) | 1 birim |
| Video yükleme | 1600 birim |

Günlük varsayılan kota 10 000 birimdir. 30 dakikalık toplama döngüsü ve günde
3 yükleme bu bütçeye rahat sığar. Daha çok kanal izlemek isterseniz
`POLL_INTERVAL_MINUTES` değerini büyütün.

## Yayın kuralları

| Kural | Varsayılan | Nerede |
|---|---|---|
| Kanallar arası minimum boşluk | **7 gün** | Ayarlar (cross_channel_gap_days) |
| Aynı kanalda aynı içeriğin tekrarı | 90 gün | Ayarlar (same_channel_repeat_days) |
| Kanal başına günlük yükleme | 3 | Kanal ayarı |
| Yayın penceresi | 17:00–21:00 | Kanal ayarı |

"Aynı içerik" tespiti **parmak izi** ile yapılır:
- Dosya yüklendiyse dosyanın SHA-256 özeti (birebir aynı dosya = aynı içerik),
- Harici kayıtta ise normalize edilmiş başlık + açıklama özeti.

Kural ihlali olan planlamalar reddedilmez; **kurallara uyan en erken zamana
ertelenir** ve arayüzde nedeni Türkçe olarak gösterilir. Yükleme anında
kurallar bir kez daha denetlenir — aradaki zamanda oluşan çakışmalar da
yakalanır.

## Gelir tahmini

YouTube Analytics API yerine **RPM** (1000 izlenme başına gelir) yaklaşımı
kullanılır: her video için ardışık ölçümler arasındaki izlenme artışı, kanalın
RPM değeriyle çarpılır. Değerler *tahmin* olarak işaretlenir; kanal bazında
RPM'i (Shorts için ~0.10–0.15 $, uzun video için ~1.5–4 $) Ayarlar'dan
düzenleyin.

## Ortam değişkenleri

| Değişken | Zorunlu | Açıklama |
|---|:-:|---|
| `DATABASE_URL` | ✔ | PostgreSQL bağlantı adresi |
| `APP_PASSWORD` | — | Web arayüzü parolası (boş = parolasız) |
| `YOUTUBE_API_KEY` | — | Data API v3 anahtarı |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | — | OAuth istemcisi |
| `TZ` | — | Zaman dilimi (varsayılan Europe/Istanbul) |
| `POLL_INTERVAL_MINUTES` | — | Toplama döngüsü (varsayılan 30) |
| `PUBLISH_CHECK_INTERVAL_MINUTES` | — | Yayın kuyruğu sıklığı (varsayılan 5) |
| `DATA_DIR` | — | Dosya klasörü (varsayılan /data) |
| `OAUTH_REDIRECT_URL` | — | Ters vekil arkasında açık adres belirtmek için |
| `SCHEDULER_ENABLED` | — | `0` = zamanlayıcıyı kapat (test) |

## Geliştirme

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt -r requirements-dev.txt

# Testler (SQLite üzerinde, ağ gerektirmez)
set DATABASE_URL=sqlite:///./dev.db
set SCHEDULER_ENABLED=0
pytest tests/ -q

# Yerel çalıştırma
uvicorn app.main:app --reload --port 8080
```

## Proje yapısı

```
app/
  main.py           FastAPI uygulaması ve yaşam döngüsü
  config.py         Ortam değişkeni ayarları
  db.py             Engine/oturum + şema oluşturma
  models.py         SQLAlchemy modelleri (8 tablo)
  schemas.py        Pydantic istek modelleri + serileştiriciler
  auth.py           Oturum yönetimi
  timeutil.py       Naive/aware datetime yardımcıları
  routers/          auth, dashboard, channels, contents, publications, settings, oauth
  services/
    rules.py        Yayın kural motoru (bu uygulamanın kalbi)
    collector.py    İstatistik toplama + veri budama
    publisher.py    Yayın kuyruğu işleyici
    scheduler.py    APSScheduler görevleri
    youtube.py      Data API sarmalayıcısı + OAuth + yükleme
    revenue.py      RPM tabanlı gelir tahmini
web/                Tek sayfa arayüz (index.html, app.js, style.css, Chart.js)
tests/              23 test: kural motoru, gelir, API uçtan uca
```
