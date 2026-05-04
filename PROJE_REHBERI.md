# ⚽ X Spor Haberleri Otonom Botu — Proje Rehberi

## 📌 Projenin Amacı

Bu proje, **@FlasFutbool** X (Twitter) hesabını 7/24 otonom bir şekilde çalışan bir **spor haberleri derleme merkezi (curation hub)** haline getirmek için tasarlanmıştır.

Bot şunları yapar:
1. Türkiye'nin en büyük haber sitelerinin RSS beslemelerini (NTV Spor, TRT Spor, Fanatik, Fotomaç vb.) düzenli aralıklarla tarar.
2. Yapay zeka (Google Gemini) ile haberlerin **"4 Büyükler"** (Galatasaray, Fenerbahçe, Beşiktaş, Trabzonspor) ile ilgili olup olmadığını kontrol eder.
3. İlgili haberleri, daha önce paylaşılıp paylaşılmadığını kontrol ederek **mükerrer (duplicate) yayını engeller.**
4. Gemini ile etkileşim alacak şekilde tweet metni oluşturur.
5. Oluşturulan tweetleri bir **kuyruk (queue) sistemine** kaydeder.
6. Kuyruktaki tweetleri sırayla ve belirli aralıklarla X'e atar.

---

## 🏗️ Sistem Mimarisi

```
┌──────────────────────────────────────────────────────┐
│                    main.py (Ana Döngü)                │
│         schedule ile 30 dk / 15 dk aralıklarla        │
│                                                      │
│  ┌─────────────────┐       ┌──────────────────────┐  │
│  │  collector_job() │       │   publisher_job()    │  │
│  │  (Her 30 dk)     │       │   (Her 15 dk)        │  │
│  └────────┬────────┘       └──────────┬───────────┘  │
│           │                           │              │
│     RSS Sitelerini                Kuyruktan          │
│     Tarar (feedparser)           tweet çeker         │
│           │                           │              │
│     Gemini ile filtre            GetXAPI ile         │
│     (ai_manager.py)              X'e atar            │
│           │                 (twitter_manager.py)     │
│     SQLite'a kaydet                                  │
│     (database.py)                                    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## 📂 Dosya Yapısı

| Dosya | Açıklama |
|---|---|
| `main.py` | Ana giriş noktası. Zamanlayıcı (scheduler), Collector ve Publisher fonksiyonları burada. |
| `twitter_manager.py` | **GetXAPI** ile X API bağlantısı. Tweet okuma (kullanıcı tweetleri çekme) ve tweet atma (tekli & thread). |
| `ai_manager.py` | Google Gemini API entegrasyonu. Haber ilgililik kontrolü, mükerrer tespiti ve tweet metni üretimi. |
| `database.py` | SQLite veritabanı. Bekleyen tweetlerin kuyruğunu (queue) yönetir. |
| `.env` | ⚠️ **GİZLİ** — API anahtarları burada saklanır. Git'e eklenmemeli! |
| `.env.example` | `.env` dosyasının şablon hali. Hangi anahtarların gerektiğini gösterir. |
| `requirements.txt` | Python bağımlılıkları listesi. |
| `test_tweet.py` | API bağlantısını test etmek için tek seferlik deneme scripti. |
| `Twitter_Takip_Listesi.md` | Botun takip ettiği hesapların toplu listesi. |
| `PROJE_REHBERI.md` | 📖 Bu dosya. |

---

## 🔑 Gerekli API Anahtarları

Bot çalışmak için iki ayrı servisten API anahtarı gerektirir:

### 1. Google Gemini API (Ücretsiz)
- **Nereden alınır:** [Google AI Studio](https://aistudio.google.com/) → "Get API Key" → "Create API Key"
- **Ne için kullanılır:** Haberlerin 4 Büyükler ile ilgili olup olmadığını anlamak, mükerrer haberleri tespit etmek ve tweet metni oluşturmak.
- `.env` dosyasındaki karşılığı: `GEMINI_API_KEY`

### 2. GetXAPI (Ücretsiz Deneme Kredisi Mevcut)
- **Nereden alınır:** [getxapi.com/signup](https://www.getxapi.com/signup) — Google veya email ile kayıt.
- **Ne için kullanılır:** Twitter'dan tweet okuma ve @FlasFutbool hesabından tweet atma.
- **Fiyatlandırma:** Kayıtta $0.10 ücretsiz kredi. Sonrası $0.001/okuma isteği, $0.002/tweet atma isteği.
- `.env` dosyasındaki karşılıkları:
  - `GETXAPI_KEY` — GetXAPI'nin kendi API anahtarı (siteden alınır)
  - `X_AUTH_TOKEN` — Twitter hesabının oturum çerezi (cookie). Chrome DevTools → Application → Cookies → x.com → `auth_token` değeri.

### 3. X (Twitter) Developer API (Şu an kullanılmıyor, yedek)
- Resmi Twitter API anahtarları da `.env` dosyasında saklanıyor ancak aktif olarak kullanılmıyor.
- İleride resmi API'ye geçmek istenirse, sadece `twitter_manager.py` dosyasını değiştirmek yeterli olacak.

---

## 📡 Haber Kaynakları (RSS Beslemeleri)

Bot şu an aşağıdaki **12 farklı kaynağı** taramaktadır:

### Ana Akım Spor Kanalları
| Kaynak | RSS URL |
|---|---|
| NTV Spor | `ntvspor.net/rss` |
| TRT Spor | `trtspor.com.tr/rss/manset.xml` |
| A Spor | `aspor.com.tr/rss` |
| Habertürk Spor | `haberturk.com/rss/spor.xml` |

### Spor Gazeteleri
| Kaynak | RSS URL |
|---|---|
| Fanatik | `fanatik.com.tr/rss` |
| Fotomaç | `fotomac.com.tr/rss/anasayfa.xml` |
| Milliyet Skorer | `milliyet.com.tr/rss/rssnew/skorerRss.xml` |
| Hürriyet Spor | `hurriyet.com.tr/rss/spor` |

### Taraftar Siteleri
| Kaynak | Takım | RSS URL |
|---|---|---|
| Sporx | Genel | `sporx.com/rss` |
| Webaslan | Galatasaray | `webaslan.com/rss` |
| Orta Çizgi | Beşiktaş | `ortacizgi.com/rss` |
| Haber61 | Trabzonspor | `haber61.net/rss` |

---

## 🕐 Çalışma Döngüsü

| Görev | Sıklık | Açıklama |
|---|---|---|
| **Collector (Toplayıcı)** | Her 30 dakikada bir | RSS sitelerini tarar, Gemini ile filtreler, uygun haberleri veritabanına kaydeder. |
| **Publisher (Yayıncı)** | Her 15 dakikada bir | Veritabanındaki kuyruktan en eski bekleyen tweeti çeker ve X'e atar. |

---

## ⚙️ Kurulum ve Çalıştırma

### 1. Bağımlılıkları kur
```bash
pip3 install -r requirements.txt
```

### 2. `.env` dosyasını hazırla
```bash
# .env.example dosyasını kopyala
cp .env.example .env

# Ardından .env dosyasını aç ve API anahtarlarını doldur
```

### 3. Botu başlat
```bash
python3 main.py
```

### 4. (Opsiyonel) Tweet atma testini çalıştır
```bash
python3 test_tweet.py
```

---

## 📜 Geliştirme Süreci — Kronolojik Yol Haritası

Bu projenin bugünkü haline nasıl geldiğinin adım adım hikayesi:

### Aşama 1: İlk Tasarım ve Mimari (Başlangıç)
- Kullanıcı, 7/24 otonom çalışan bir X (Twitter) spor haberleri botu istedi.
- Modüler mimari planlandı: `main.py`, `twitter_manager.py`, `ai_manager.py`, `database.py`.
- Kuyruk (Queue) tabanlı sistem tercih edildi: "Haberi bul → Kuyruğa ekle → Sırayla at" mantığı.
- İlk versiyon RSS + Tweepy (resmi X API) + Gemini + SQLite ile tasarlandı.
- Gerekli kütüphaneler kuruldu: `tweepy`, `google-generativeai`, `schedule`, `python-dotenv`, `feedparser`.

### Aşama 2: "Sadece Twitter İçi" Derleme Denemesi
- Kullanıcının isteği üzerine RSS'ler kaldırıldı.
- Sistem, sadece Twitter içinde yaşayan bir **derleme hesabı** olarak yeniden tasarlandı.
- Belirli spor muhabirlerini (yagosabuncuoglu, ErtanSuzgun, FabrizioRomano) 100+ beğeni filtresiyle tarayan bir yapı kuruldu.
- Kaynak gösterme formatı belirlendi: tweet sonuna `(kullaniciadi)` eklenmesi kararlaştırıldı.

### Aşama 3: Twitter API Anahtarlarının Alınması
- Twitter Developer Portal'da **"SporHabercisi"** adıyla yeni bir uygulama oluşturuldu.
- Consumer Key, Consumer Secret ve Bearer Token üretildi.
- Uygulama izinleri **"Read"** → **"Read and Write"** olarak güncellendi.
- Access Token ve Access Token Secret üretildi.
- `.env` dosyasına tüm anahtarlar eklendi.

### Aşama 4: Twitter API Engelleri
- **401 Unauthorized hatası:** Twitter, "Read and Write" izin değişikliğinden sonra eski token'ları geçersiz kıldı. Token'lar yeniden üretilerek (Regenerate) çözüldü.
- **402 Payment Required hatası:** Twitter'ın 2026'da getirdiği **"Pay Per Use"** modeli nedeniyle ücretsiz tweet atma hakkı kalmadığı keşfedildi. Eski "Free Tier" (1500 tweet/ay) planı tamamen kaldırılmış.
- Resmi Twitter API ile tweet okuma da **paralı** olduğu için "Sadece Twitter içi derleme" fikri askıya alındı.

### Aşama 5: RSS'e Geri Dönüş
- Haber toplama sistemi tekrar RSS tabanlı yapıya döndürüldü (ücretsiz).
- `feedparser` kütüphanesi yeniden eklendi.
- Haber kaynakları 3'ten **12'ye** genişletildi (ulusal kanallar + spor gazeteleri + taraftar siteleri).

### Aşama 6: GetXAPI Keşfi ve Entegrasyonu
- Twitter API'nin pahalı olması nedeniyle alternatif arandı.
- **GetXAPI** (getxapi.com) keşfedildi — resmi API'ye kıyasla 5-10x daha ucuz, ücretsiz deneme kredisi var.
- `twitter_manager.py` tamamen yeniden yazıldı: Tweepy kaldırıldı, GetXAPI REST API entegre edildi.
- GetXAPI üzerinden hem tweet okuma hem tweet atma desteği eklendi.
- `.env` dosyasına `GETXAPI_KEY` ve `X_AUTH_TOKEN` (tarayıcı çerezi) eklendi.
- `X_AUTH_TOKEN` nasıl alınır: Chrome DevTools → Application → Cookies → x.com → `auth_token` değeri.
- **İlk deneme tweet'i başarıyla atıldı!** ✅ (Tweet ID: 2051065978520273243)

### Aşama 7: Gemini API Sorunları ve Çözümleri
- Bot ilk çalıştırıldığında Gemini **"API key not valid"** hatası verdi.
- **Neden:** Kod eski model adını (`gemini-1.5-pro-latest`) kullanıyordu. Google bu modeli kaldırmıştı.
- Model adı `gemini-2.0-flash`'a güncellendi → ama bu modelin ücretsiz kotası 0'dı.
- Sonunda **`gemini-2.5-flash`** modelinin ücretsiz planda çalıştığı keşfedildi ve koda uygulandı.
- **Kota tükenme sorunu:** İlk (hatalı) çalıştırmada bot, her başarısız istekte 5 kez tekrar denediği için (backoff sistemi) orijinal API key'in günlük kotası tükenmişti.
- 3 farklı Google hesabında API key oluşturuldu ve denendi. Sorunun modele özgü olduğu anlaşıldı.
- Bot ikinci kez başlatıldığında Gemini **düzgün çalıştı**: Karagümrük, Kasımpaşa, Shakhtar gibi haberleri doğru şekilde "İLGİSİZ" olarak filtreledi, "Cristiano Ronaldo - Fenerbahçe" haberini "İLGİLİ" bulup kuyruğa ekledi.
- Ancak Gemini ücretsiz planının limitleri çok düşüktü: **dakikada 5, günde 20 istek.**

### Aşama 8: Gemini API Çağrı Optimizasyonu (Mevcut Durum ✅)
- **Sorun:** Her haber için 3 ayrı Gemini çağrısı yapılıyordu:
  1. `check_relevance()` — İlgililik kontrolü
  2. `check_duplicate()` — Mükerrer kontrolü
  3. `generate_tweet()` — Tweet metni oluşturma
- Günde 20 istek = sadece 7 haber işlenebiliyordu.
- **Çözüm:** 3 fonksiyon tek bir `process_news()` fonksiyonuna birleştirildi.
- Gemini'ye tek bir prompt gönderiliyor: "Bu haberi oku. 4 Büyüklerle ilgiliyse ve mükerrer değilse tweet metni oluştur. Değilse 'ATLA' de."
- **Sonuç:** 1 çağrı = 1 haber. Günde 20 istek = **20 haber** işlenebilir (3x artış).
- Ek optimizasyonlar:
  - Her kaynak başına 5 yerine **3** haber kontrol ediliyor.
  - Gemini istekleri arasına **15 saniyelik bekleme** eklendi (dakika limiti aşılmasın diye).
  - Aynı döngüde işlenmiş haberler yerel listeye ekleniyor (gereksiz API çağrısı engeli).

---

### Aşama 9: Groq Entegrasyonu ve Kota Sorununun Çözümü
- **Sorun:** Gemini 2.5 Flash'ın ücretsiz kotası (günde 20 istek) 12 RSS kaynağı için yetersiz kaldı.
- **Çözüm:** AI motoru olarak **Groq (Llama 3.3 70B)** kullanmaya geçildi.
- **Sonuç:** Groq günde **14.400** ücretsiz istek hakkı tanıyor. Kota sorunu tamamen çözüldü.
- Hız: Gemini'ye göre ~10 kat daha hızlı (tüm kaynak taraması <1 dakika).

### Aşama 10: İleri Seviye Filtreleme ve Akıllı Paylaşım (Mevcut Durum ✅)
Haber kalitesini ve zamanlamasını iyileştirmek için 4 büyük güncelleme yapıldı:
1. **2 Saat Tazelik Filtresi:** RSS'den gelen bir haber 2 saatten eski ise otomatik olarak atlanıyor.
2. **Gece Modu (03:00 - 07:00):** Bot bu saatlerde paylaşım yapmayı durdurur (uyku modu), böylece verimsiz saatlerde tweet atılmaz.
3. **Kuyruk Temizleme (Hızlı Mod):** Paylaşım aralığı 15 dakikadan **5 dakikaya** düşürüldü. Ayrıca kuyrukta birikme olursa, her döngüde 2 haber birden atarak kuyruğu hızla eritme özelliği eklendi.
4. **Anti-Clickbait (Tık Tuzağı Engelleyici):**
   - İsim vermeyen ("Yıldız isim", "O futbolcu" vb.) muğlak haberler eleniyor.
   - Haber başlığında isim yoksa ama detayda varsa, AI haberi o ismi kullanarak "kurtarıyor" ve öyle paylaşıyor.
   - Gerçek bilgi içermeyen "Perde arkası belli oldu" tarzı boş içerikler engelleniyor.

---

## 🔄 Gelecekte Resmi API'ye Geçiş

Sistem modüler olarak tasarlandığı için, ileride resmi Twitter API'ye geçmek çok kolay:
1. Twitter Developer Portal'a kredi yükle.
2. `twitter_manager.py` dosyasını Tweepy tabanlı eski haline geri döndür.
3. `.env` dosyasındaki resmi API anahtarlarını güncelle.
4. **Toplam süre: ~5 dakika.** Diğer hiçbir dosya değişmez.

---

## 🚧 Kalan İşler (Yapılacaklar)

- [x] Modüler mimari oluştur (main, twitter_manager, ai_manager, database)
- [x] RSS haber toplama sistemi kur (12 kaynak)
- [x] AI filtreleme sistemi (4 Büyükler + mükerrer kontrolü)
- [x] GetXAPI entegrasyonu tamamla (tweet okuma & atma)
- [x] Gemini modelini Groq (Llama 3.3) ile değiştir (14.400/gün kota)
- [x] 2 Saat tazelik filtresini ekle
- [x] Gece modu ve Kuyruk temizleme modunu ekle
- [x] Anti-Clickbait (tık tuzağı) filtresini aktifleştir
- [ ] Botu bulut sunucusuna taşı (Render, AWS veya PythonAnywhere)
- [ ] Log dosyasını (`bot.log`) periyodik olarak izle

---

## 💡 Gelecekte Yapılabilecekler

- **Resmi API'ye Geçiş:** Twitter Developer hesabına kredi yüklenirse, `twitter_manager.py` dosyası Tweepy'ye geri döndürülebilir.
- **Yeni Takımlar:** `ai_manager.py` içindeki prompt değiştirilerek 4 Büyükler dışındaki takımlar da eklenebilir.
- **Yeni Haber Kaynakları:** `main.py` içindeki `RSS_FEEDS` listesine yeni RSS URL'leri eklenebilir.
- **Görüntü Desteği:** Haber linkindeki görseli çekip tweet'e ekleme (GetXAPI medya yükleme desteği gerektirir).

---

## ⚠️ Bilinen Kısıtlamalar

| Kısıtlama | Detay | Çözüm Yolu |
|---|---|---|
| **Groq Günlük Kota** | Günde 14.400 istek (Çok yüksek, neredeyse sınırsız). | Kota dolarsa farklı bir Groq API Key al. |
| **auth_token süresi** | Tarayıcı çerezi (auth_token) ~1 yıl sonra geçerliliğini yitirir. | Chrome DevTools'tan yeni çerez al. |
| **GetXAPI servisi** | Üçüncü parti servis, Twitter engelleyebilir. | Resmi Twitter API'ye geçiş (5 dk). |
| **GetXAPI kredisi** | $0.10 ücretsiz deneme kredisi bitebilir. | Ek kredi yükle ($5 aylarca yeter). |

---

## 📊 Maliyet Özeti

| Servis | Kullanım | Maliyet |
|---|---|---|
| **Twitter Listesi (GetXAPI)** | 45+ hesap, 15 dk'da bir | ~$0.10/gün |
| **AI (Groq - Batching)** | 10'arlı paket işleme | ÜCRETSİZ |
| **GetXAPI — Tweet Atma** | $0.002/tweet | ~$0.04/gün (20 tweet) |
| **TOPLAM** | | **~$4.50 / Ay** |

---

*Son güncelleme: 4 Mayıs 2026, 12:20*


### Aşama 11: AI Batching ve Twitter Liste Geçişi (Maliyet Optimizasyonu ✅)
Botun tarama kapasitesini artırırken maliyetini düşürmek için köklü bir değişikliğe gidildi:
- **Sorun:** 45+ hesabı tek tek taramak GetXAPI üzerinde günlük $4+ maliyet oluşturuyordu. Ayrıca her haber için ayrı AI çağrısı yapmak Groq token limitlerini zorluyordu.
- **Çözüm 1 (Twitter List):** Tüm takip edilen hesaplar tek bir Twitter Listesine toplandı. Bot artık 45 farklı kapıyı çalmak yerine, tek bir liste taraması yaparak ($0.001) herkesin tweetini aynı anda görüyor.
- **Çözüm 2 (Batching):** AI motoru (ai_manager.py) "Toplu İşleme" moduna geçirildi. Bot artık bulduğu haberleri tek tek değil, 10'arlı paketler halinde AI'ya gönderiyor.
- **Sonuç:** Günlük maliyet **$0.10** (yaklaşık 3.5 TL) seviyesine sabitlendi. Takip edilen hesap sayısı 200 de olsa maliyet artık artmıyor.
- **RSS İptali:** Haberlerin hızı ve kalitesi Twitter listesinde daha yüksek olduğu için RSS sistemi tamamen devre dışı bırakıldı.

### Aşama 12: Paylaşım Hızı ve Kaynak Esnekliği (İnce Ayarlar ✅)
Kullanıcı deneyimini iyileştirmek için botun davranışlarında şu düzenlemeler yapıldı:
- **Beğeni Eşiği Düzenlemesi:** Yeni haberlerin daha hızlı yakalanması için `MIN_LIKES` eşiği **40**'a düşürüldü.
- **Kaynak Güven Politikası:** AI'ya, listedeki tüm hesapların (parodi olsalar bile) güvenilir olduğu ve sadece içeriğe odaklanması gerektiği talimatı verildi.
- **Paylaşım Aralığı (Anti-Spam):** Takipçilerin sıkılmaması için her tweet arasına **en az 3 dakika** mesafe konuldu.
- **Dengeli Akış:** Kuyrukta çok haber birikse dahi, bot artık her döngüde sadece 1 tweet atarak akışın doğal görünmesini sağlıyor.

### Aşama 13: Google Cloud (GCP) Geçişi ve Tam Otonomizasyon (Tamamlandı ✅)
Botun kişisel bilgisayardan bağımsız, 7/24 çalışabilmesi için profesyonel bulut altyapısına geçiş yapıldı:
- **Altyapı:** Google Cloud Compute Engine üzerinden `e2-micro` (Always Free) sunucu kuruldu. Bölge olarak Iowa (us-central1) seçilerek maliyet sıfıra indirildi.
- **Beyin Nakli (Gemini 1.5 Flash):** Groq (Llama 3.3) modelinin günlük token limitleri (100k) yetersiz kaldığı için botun ana işlemcisi **Gemini 1.5 Flash** modeline taşındı. Bu sayede günlük 1.500 istek (yaklaşık 15.000 haber işleme kapasitesi) elde edildi.
- **Teknik Kurulum:** 
    *   Ubuntu 22.04 Minimal işletim sistemi üzerine Python3 ve gerekli kütüphaneler (google-generativeai, schedule, requests) kuruldu.
    *   **Screen Kullanımı:** Botun SSH penceresi kapansa dahi çalışmaya devam etmesi için "hayalet oturum" (screen) sistemi kuruldu.
- **Karşılaşılan Engeller ve Çözümler:** 
    *   *Terminal Yazım Sorunları:* Uzun kodların kopyalanırken bozulması sorunu, kodların küçük parçalar halinde (heredoc) ve ASCII karakterler kullanılarak aktarılmasıyla aşıldı.
    *   *Model Adı Karmaşası:* `gemini-1.5-flash` isminin bazı kütüphane sürümlerinde çalışmaması üzerine, `gemini-flash-latest` alias'ı kullanılarak bağlantı sağlandı.
    *   *Auth Token Hatası (400 Bad Request):* GetXAPI servisinin `auth_token` değerini sadece başlıkta (header) değil, JSON paketi (payload) içinde de zorunlu kıldığı tespit edildi ve `twitter_manager.py` buna göre güncellendi.
- **Son Durum:** Bot artık tamamen kendi başına, bir sunucu üzerinde yaşayan, haberleri süzüp anında paylaşan bir yapay zeka sistemine dönüştü.

---
*Son güncelleme: 4 Mayıs 2026, 17:48*


