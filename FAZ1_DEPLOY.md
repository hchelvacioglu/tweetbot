# Faz 1 — Sunucuda Deploy Talimatı

> Bu adımları SSH ile e2-micro sunucuna bağlanıp çalıştır.

---

## 0. Mevcut botu durdur

Eğer screen ile çalışıyorsa:

```bash
screen -r flasbot   # veya hangi isim verdiysen
# Ctrl+C ile durdur
# Ctrl+A, D ile detach
```

Veya hızlıca PID bul:

```bash
pgrep -af "python3 main.py"
kill <PID>
```

---

## 1. Mevcut dosyaları yedekle (geri dönüş için)

```bash
cd ~/your-bot-directory   # Botun olduğu dizine git
mkdir -p backup_$(date +%Y%m%d)
cp main.py database.py twitter_manager.py ai_manager.py requirements.txt sports_bot.db backup_$(date +%Y%m%d)/
```

---

## 2. Yeni dosyaları yerleştir

Lokal makinenden 6 dosyayı `scp` ile yükle:

```bash
# LOKAL makinede çalıştır:
scp main.py database.py twitter_manager.py ai_manager.py requirements.txt migrate_and_clean.py \
    YOUR_USER@YOUR_VM_IP:~/your-bot-directory/
```

---

## 3. Bağımlılıkları güncelle

```bash
pip3 install -r requirements.txt --upgrade
```

---

## 4. .env dosyasına yeni ayarları ekle

```bash
nano .env
```

Aşağıdaki satırları ekle (henüz yoksa):

```env
# AI Provider seçimi: 'groq' veya 'gemini'
AI_PROVIDER=groq

# Groq ayarları
GROQ_MODEL=llama-3.3-70b-versatile

# Gemini ayarları (AI_PROVIDER=gemini ise)
GEMINI_MODEL=gemini-flash-latest

# Filtreleme
MIN_LIKES=40
MAX_TWEET_AGE_HOURS=3

# Zamanlama (dakika)
COLLECTOR_INTERVAL_MIN=15
PUBLISHER_INTERVAL_MIN=3
```

`AI_PROVIDER` değerini Groq'tan Gemini'ye geçmek istersen sadece bu satırı `gemini` yap, başka hiçbir değişiklik gerekmez.

---

## 5. Migration ve kuyruk temizliği

```bash
python3 migrate_and_clean.py
```

Script seni "Bekleyen kuyruğu iptal etmek için ENTER, korumak için n" diye soracak. **ENTER bas** — kuyruktaki saçma haberler temizlenecek.

---

## 6. Test (kritik): Tek bir tweet atabiliyor mu?

```bash
python3 test_tweet.py
```

Eğer "✅ TEST SUCCESSFUL!" görürsen GetXAPI bağlantısı çalışıyor demektir.

Eğer **"500 Server Error"** görürsen, `auth_token` muhtemelen geçersiz olmuştur — Chrome DevTools'tan yenisini al.

---

## 7. Botu screen içinde başlat

```bash
screen -S flasbot
python3 main.py

# Ctrl+A, D ile detach et — pencereyi kapatabilirsin
```

Tekrar bakmak için:

```bash
screen -r flasbot
```

---

## 8. İlk 30 dakika izle

Botun düzgün çalıştığından emin olmak için log'u canlı izle:

```bash
tail -f bot.log
```

Beklediğin akış:

```
... AI provider: Groq (llama-3.3-70b-versatile)
... [Collector] Başlıyor...
... List 2051245002500547039: 60 tweet alındı.
... [Collector] Filtreleme: 60 tweet → X aday (düşük beğeni: ..., eski: ..., mükerrer: ...)
... AI batch: 10 işlendi, 3 paylaşılacak.
... [Collector] + Kuyruğa: ...
... [Publisher] ✓ ID 30 paylaşıldı.
```

---

## Hata Senaryoları ve Çözümleri

### A) "post_tweet failed | status=500" görüyorsan
GetXAPI tarafında bir sorun var. Olası sebepler:
- `auth_token` süresi dolmuş veya geçersiz → yeni çerez al
- GetXAPI bakımda → 30 dk bekle, tekrar dene
- Tweet metni Twitter'ın kabul etmediği bir şey içeriyor → log'da `body=` kısmına bak

### B) "Groq quota exceeded" görüyorsan
Bot artık otomatik olarak 1 saat bekliyor, sonra tekrar deniyor. İstersen `.env`'de `AI_PROVIDER=gemini` yapıp 1 satır komutla Gemini'ye geçersin (Groq'a göre 5x kapasite).

### C) "get_list_tweets failed | status=400" görüyorsan
List ID muhtemelen yanlış. X'te listeye git, URL'deki ID'yi kopyala (`https://x.com/i/lists/XXXXXXXXX`) ve `.env`'deki `TWITTER_LIST_ID`'yi güncelle.

### D) Kuyrukta tweet birikiyor ama paylaşılmıyor
`SELECT id, attempt_count, status FROM Bekleyen_Tweetler WHERE status='Bekliyor';` ile bak. Hepsi `attempt_count >= 3` ise GetXAPI çalışmıyor demektir, yukarıdaki A maddesine bak.

DB'ye SQLite shell ile bakmak için:
```bash
sudo apt install sqlite3 -y
sqlite3 sports_bot.db "SELECT id, status, attempt_count, substr(title,1,50) FROM Bekleyen_Tweetler ORDER BY id DESC LIMIT 20;"
```
