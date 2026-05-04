# FlasFutbool Bot — Deployment Geçmişi

**Tarih:** 5 Mayıs 2026  
**Hesap:** @FlasFutbool  
**VM:** instance-20260504-131826, us-central1-f (Ubuntu 22.04, e2-micro)  
**VM dizini:** ~/tweetbot/

---

## Proje Nedir?

X (Twitter) üzerinde otomatik spor haberi paylaşan bir bot. Türkiye'nin 4 büyük futbol kulübü (Galatasaray, Fenerbahçe, Beşiktaş, Trabzonspor) ile ilgili haberleri takip listesinden topluyor, AI ile filtreler ve otomatik tweet atıyor.

**Temel mimari:**
- **Collector** (her 15 dakika): GetXAPI ile belirlenen Twitter List'inden tweet çeker → beğeni/yaş/mükerrer filtresi → Gemini AI ile içerik değerlendirmesi → SQLite kuyruğuna ekler
- **Publisher** (her 3 dakika): Kuyruktan en eski "Bekliyor" tweet'i alır → GetXAPI ile X'e atar → durumu günceller
- **AI:** Gemini `gemini-flash-latest` modeli (günlük 1500 ücretsiz istek)
- **DB:** SQLite (`sports_bot.db`) — tweet hash'leri, durum takibi

---

## Neden GCP'ye Taşındı?

Mac'te çalışan eski bot hatalıydı ve sürekli bakım gerektiriyordu:
- Mac kapandığında/uyuduğunda bot duruyordu
- Çeşitli parse hataları ve API sorunları vardı
- `screen` ile yönetim kötüydü, crash sonrası elle başlatmak gerekiyordu

GCP e2-micro VM seçildi çünkü:
- 7/24 çalışır, Mac'e bağımlılık yok
- Ücretsiz katman (f1-micro / e2-micro Always Free kapsamında)
- systemd ile otomatik yeniden başlatma ve VM reboot'ta otomatik açılma

---

## Faz 1 Güncellemeleri Nelerdi?

`~/Desktop/faz1_deploy/` içindeki 7 dosya eski `~/Desktop/tweetbot/` dosyalarının yerini aldı.

| Dosya | Değişiklik |
|---|---|
| `main.py` | Tarih parser düzeltildi, çift collector kaldırıldı, AIQuotaExceeded yakalanıyor |
| `database.py` | `attempt_count` ve `content_hash` kolonları, UNIQUE INDEX |
| `twitter_manager.py` | Daha iyi hata loglama, timeout artırıldı |
| `ai_manager.py` | AI provider seçimi (Gemini/Groq), içerik güvenliği kuralları |
| `requirements.txt` | Güncel bağımlılıklar |
| `migrate_and_clean.py` | DB şema güncellemesi için tek seferlik script |
| `FAZ1_DEPLOY.md` | Faz 1 için deploy notları |

---

## Ne Yaptık — Adım Adım

### 1. Yedek
Mac'teki `~/Desktop/tweetbot/` → `~/Desktop/tweetbot_backup_20260505/`

### 2. VM Sistem Hazırlığı
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
mkdir -p ~/tweetbot
```

### 3. Dosya Kopyalama (gcloud compute scp)
- faz1_deploy/ içindeki 7 dosya
- `.env` (Mac'ten, API anahtarlarıyla)
- `sports_bot.db` (29 kayıt, hash geçmişini korumak için)
- `test_tweet.py`

### 4. .env'ye Yeni Değişkenler
```
AI_PROVIDER=gemini          # Groq günlük limiti dolmuştu
GEMINI_MODEL=gemini-flash-latest
GROQ_MODEL=llama-3.3-70b-versatile
MAX_TWEET_AGE_HOURS=3
COLLECTOR_INTERVAL_MIN=15
PUBLISHER_INTERVAL_MIN=3
```

### 5. Bağımlılık Kurulumu
```bash
pip3 install -r requirements.txt --user
pip3 install 'google-genai>=1.0.0' --user  # Sonradan eklendi
```

### 6. Migration
```bash
python3 migrate_and_clean.py --no-confirm
```
- `attempt_count` ve `content_hash` kolonları eklendi
- 1 duplicate kayıt temizlendi
- UNIQUE INDEX oluşturuldu
- 32 eski "Bekliyor" tweet "Iptal" yapıldı

### 7. GetXAPI Testi
```bash
python3 test_tweet.py
```
İlk denemede `auth_token` süresi dolmuştu. Tarayıcıdan yeni token alındı ve `.env` güncellendi.

**Token yenileme komutu:**
```bash
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f \
  --command="sed -i 's/X_AUTH_TOKEN=.*/X_AUTH_TOKEN=YENİ_TOKEN/' ~/tweetbot/.env && sudo systemctl restart flasbot"
```

### 8. systemd Service
`/etc/systemd/system/flasbot.service` oluşturuldu:
```ini
[Unit]
Description=FlasBot - X Sports News Bot
After=network.target

[Service]
Type=simple
User=hch7
WorkingDirectory=/home/hch7/tweetbot
ExecStart=/usr/bin/python3 /home/hch7/tweetbot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Önemli:** `StandardOutput/StandardError=append:bot.log` YAZILMADI. Nedeni: systemd dosyayı root olarak açıyor, ama servis `User=hch7` olarak çalışıyor → Permission denied. `main.py` zaten kendi FileHandler'ı ile bot.log'a yazıyor.

```bash
sudo systemctl daemon-reload
sudo systemctl enable flasbot   # VM reboot'ta otomatik açılır
sudo systemctl start flasbot
```

---

## Yolda Çıkan Hatalar ve Çözümleri

### Hata 1: Migration — UNIQUE constraint failed
**Neden:** DB'de aynı `content_hash`'e sahip duplicate kayıtlar vardı.  
**Çözüm:** `migrate_and_clean.py`'a duplicate temizleme adımı eklendi:
```sql
DELETE FROM Bekleyen_Tweetler
WHERE rowid NOT IN (SELECT MIN(rowid) FROM Bekleyen_Tweetler GROUP BY content_hash)
```

### Hata 2: auth_token geçersiz
**Neden:** GetXAPI browser session cookie'si süresi dolmuştu.  
**Çözüm:** x.com → F12 → Application → Cookies → `auth_token` değeri alınıp .env güncellendi.  
**Kalıcılık:** Her logout veya cookie silmede değişir, aylık 1-2 kez gerekebilir.

### Hata 3: get_list_tweets — "Missing required query param: q"
**Neden:** Faz 1 kodu `"query"` parametresi kullanıyordu ama GetXAPI `"q"` bekliyor.  
**Çözüm:** `twitter_manager.py`'da `query` → `q`, `limit` → `count` olarak düzeltildi.

### Hata 4: Çift loglama
**Neden:** systemd `StandardOutput=append:bot.log` + `main.py FileHandler` aynı dosyaya yazıyordu.  
**Çözüm:** systemd service dosyasından `StandardOutput/StandardError` satırları kaldırıldı.

### Hata 5: google.generativeai SDK — max_output_tokens çalışmıyor
**Neden:** `google-generativeai>=0.8.3` (deprecated SDK) ile `max_output_tokens` parametresi
düzgün uygulanmıyor. Gemini yanıtı ~260-280 char'da (FinishReason.MAX_TOKENS) kesiliyordu.
Prompt token sayısı yüksek (1961 token) ve SDK bunu override ediyor gibi davranıyordu.  
**Çözüm:** Yeni `google-genai>=1.0.0` SDK'ya geçildi:
```python
# Eski (çalışmıyor):
import google.generativeai as genai
model = genai.GenerativeModel(GEMINI_MODEL)
response = model.generate_content(prompt, generation_config={"max_output_tokens": 2000})

# Yeni (çalışıyor):
from google import genai as genai_new
client = genai_new.Client(api_key=GEMINI_API_KEY)
response = client.models.generate_content(
    model=GEMINI_MODEL, contents=prompt,
    config=genai_types.GenerateContentConfig(temperature=0.7, max_output_tokens=4000)
)
```

### Hata 6: Truncated JSON parse
**Neden:** Gemini bazen yanıtı ortasında kesebiliyor (özellikle uzun tweet metinlerinde).  
**Çözüm:** `ai_manager.py`'da 3 aşamalı parse:
1. Direkt `json.loads`
2. Greedy regex ile `[...]` bul, parse et
3. Partial parse: tamamlanmış JSON object'leri regex ile topla, birleştir

---

## Günlük Bakım

### Log izleme:
```bash
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f \
  --command="tail -50 ~/tweetbot/bot.log"
```

### Servis durumu:
```bash
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f \
  --command="sudo systemctl status flasbot"
```

### Bot restart:
```bash
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f \
  --command="sudo systemctl restart flasbot"
```

### auth_token yenileme (x.com'dan çıkış/cookie silme sonrası):
```bash
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f \
  --command="sed -i 's/X_AUTH_TOKEN=.*/X_AUTH_TOKEN=YENİ_TOKEN/' ~/tweetbot/.env && sudo systemctl restart flasbot"
```

### Kod güncelleme (Mac'ten dosya kopyalama):
```bash
gcloud compute scp ~/Desktop/tweetbot/DOSYA.py instance-20260504-131826:~/tweetbot/ --zone=us-central1-f
gcloud compute ssh instance-20260504-131826 --zone=us-central1-f --command="sudo systemctl restart flasbot"
```

---

## Limitler ve Dikkat Edilecekler

| Servis | Limit | Durum |
|---|---|---|
| Gemini Flash (ücretsiz) | 1500 istek/gün | Collector her 15dk → ~96 istek/gün, rahat |
| GetXAPI | Plana bağlı | tweet atma + okuma |
| X auth_token | Oturum açık kaldığı sürece | Logout/cookie sil → yenile |
| GCP e2-micro | Always Free | Bölge: us-central1 kalmalı |
