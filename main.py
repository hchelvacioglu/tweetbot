"""
main.py — Faz 1 Güncellemesi
============================
Düzeltmeler:
- Tarih parser düzeltildi: GetXAPI Twitter formatını (`Fri May 01 12:37:29 +0000 2026`) artık doğru parse ediyor
- Publisher: tweet fail olunca attempt_count artırılıyor, MAX_ATTEMPTS sonra Basarisiz oluyor
- Publisher: aynı tweet'i sonsuza kadar denememe garantisi
- Collector: AIQuotaExceeded yakalanıyor — kota dolarsa o döngü atlanır, sıradaki saatte tekrar denenir
- DB hash ile dedup: AI'dan ÖNCE hash kontrolü, gereksiz AI çağrısı engelleniyor
- Eski RSS / feedparser / email.utils kalıntıları temizlendi
- Çift initial run kaldırıldı
"""

import os
import time
import json
import logging
import schedule
import datetime
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

import database
import ai_manager
import twitter_manager

# ============================================================
# Config
# ============================================================
TWITTER_LIST_ID = os.getenv("TWITTER_LIST_ID")
MIN_LIKES = int(os.getenv("MIN_LIKES", 40))
MAX_TWEET_AGE_HOURS = float(os.getenv("MAX_TWEET_AGE_HOURS", 3))
COLLECTOR_INTERVAL_MIN = int(os.getenv("COLLECTOR_INTERVAL_MIN", 15))
PUBLISHER_INTERVAL_MIN = int(os.getenv("PUBLISHER_INTERVAL_MIN", 3))

# AI quota dolduğunda bu zamana kadar collector'ı atla
_ai_quota_blocked_until = None

# ============================================================
# Yardımcılar
# ============================================================

def is_night_time() -> bool:
    """Yerel saat 03:00 - 07:00 arası mı?"""
    return 3 <= datetime.datetime.now().hour < 7

def parse_tweet_date(date_str: str):
    """
    GetXAPI Twitter formatını parse eder.
    Örn: 'Fri May 01 12:37:29 +0000 2026' veya ISO format.
    Başarısızsa None döner.
    """
    if not date_str:
        return None
    # Önce Twitter formatını dene (RFC 2822 benzeri)
    try:
        return parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        pass
    # ISO formatını dene
    try:
        return datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        pass
    return None

def is_too_old(date_str: str) -> bool:
    """Tweet MAX_TWEET_AGE_HOURS'tan eski mi?"""
    dt = parse_tweet_date(date_str)
    if not dt:
        return False  # Parse edemediysek atlamayalım, AI bakar
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - dt
    return age.total_seconds() > MAX_TWEET_AGE_HOURS * 3600

# ============================================================
# Publisher
# ============================================================

def publisher_job():
    """En eski bekleyen tweet'i atar. Fail olursa attempt_count'u artırır."""
    if is_night_time():
        logger.info("[Publisher] Gece modu (03-07), atlandı.")
        return

    tweet = database.get_oldest_pending_tweet()
    if not tweet:
        return

    tweet_id = tweet['id']
    tweet_content_str = tweet['tweet_content']

    # Thread mi tek tweet mi?
    try:
        content_to_post = json.loads(tweet_content_str)
    except (json.JSONDecodeError, TypeError):
        content_to_post = tweet_content_str

    if isinstance(content_to_post, list):
        success = twitter_manager.post_thread(content_to_post)
    else:
        success = twitter_manager.post_tweet(content_to_post)

    if success:
        database.update_tweet_status(tweet_id, 'Paylasildi')
        logger.info(f"[Publisher] ✓ ID {tweet_id} paylaşıldı.")
    else:
        database.increment_attempt(tweet_id)
        logger.warning(f"[Publisher] ✗ ID {tweet_id} fail. Attempt sayacı artırıldı.")

# ============================================================
# Collector
# ============================================================

def twitter_collector_job():
    """Twitter listesini tarar, AI ile filtreler, kuyruğa ekler."""
    global _ai_quota_blocked_until

    if not TWITTER_LIST_ID:
        logger.warning("[Collector] TWITTER_LIST_ID yok, atlandı.")
        return

    # AI kotası dolduysa belirli süre boyunca atla
    if _ai_quota_blocked_until and datetime.datetime.now() < _ai_quota_blocked_until:
        remaining = (_ai_quota_blocked_until - datetime.datetime.now()).total_seconds() / 60
        logger.info(f"[Collector] AI kotası kilitli, {remaining:.0f} dk sonra tekrar denenecek.")
        return

    logger.info("[Collector] Başlıyor...")
    recent_titles = database.get_recent_news_titles(hours=12)

    tweets = twitter_manager.get_list_tweets(TWITTER_LIST_ID, count=60)
    if not tweets:
        logger.info("[Collector] Listeden tweet alınamadı, atlandı.")
        return

    pending_items = []
    skipped_likes = 0
    skipped_age = 0
    skipped_dup = 0

    for tweet in tweets:
        text = tweet.get('text', '')
        if not text:
            continue

        # Min beğeni
        likes = tweet.get('likeCount', 0)
        if likes < MIN_LIKES:
            skipped_likes += 1
            continue

        # Tazelik
        if is_too_old(tweet.get('createdAt')):
            skipped_age += 1
            continue

        # DB hash dedup (AI'dan önce, ücretsiz)
        if database.hash_exists(text):
            skipped_dup += 1
            continue

        # userName yoksa URL'den çıkarmayı dene, o da olmazsa boş bırak
        username = tweet.get('userName', '') or ''
        if not username:
            url = tweet.get('url', '')
            parts = url.rstrip('/').split('/')
            if len(parts) >= 4 and parts[-2] == 'status':
                username = parts[-3]
        source = f"@{username}" if username else ""

        pending_items.append({
            "title": text,
            "description": "",
            "link": tweet.get('url', ''),
            "source": source,
            "published_date": tweet.get('createdAt') or time.strftime('%Y-%m-%d %H:%M:%S')
        })

    logger.info(
        f"[Collector] Filtreleme: {len(tweets)} tweet → "
        f"{len(pending_items)} aday "
        f"(düşük beğeni: {skipped_likes}, eski: {skipped_age}, mükerrer: {skipped_dup})"
    )

    if not pending_items:
        return

    # AI ile batch işle
    saved_count = 0
    for i in range(0, len(pending_items), 10):
        batch = pending_items[i:i+10]
        try:
            results = ai_manager.process_news_batch(batch, recent_titles)
        except ai_manager.AIQuotaExceeded:
            # Kota doldu, 1 saat boyunca collector'ı dondur
            _ai_quota_blocked_until = datetime.datetime.now() + datetime.timedelta(hours=1)
            logger.error("[Collector] AI kotası doldu! Collector 1 saat dondurulacak.")
            break
        except Exception as e:
            logger.error(f"[Collector] Batch hatası: {e}")
            continue

        for res in results:
            ok = database.add_pending_tweet(
                res['title'], res['link'], res['published_date'], res['tweet']
            )
            if ok:
                saved_count += 1
                recent_titles.append(res['title'])
                logger.info(f"[Collector] + Kuyruğa: {res['title'][:60]}")

        # Batch'ler arası kısa bekleme (rate limit nazikçe)
        if i + 10 < len(pending_items):
            time.sleep(5)

    logger.info(f"[Collector] Bitti. {saved_count} yeni tweet kuyruğa eklendi.")

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("X Sports News Bot başlatılıyor...")
    logger.info(f"AI Provider: {ai_manager.AI_PROVIDER}")
    logger.info(f"List ID: {TWITTER_LIST_ID}")
    logger.info(f"Min Likes: {MIN_LIKES} | Max Age: {MAX_TWEET_AGE_HOURS}h")
    logger.info(f"Collector: her {COLLECTOR_INTERVAL_MIN}dk | Publisher: her {PUBLISHER_INTERVAL_MIN}dk")
    logger.info("=" * 60)

    database.init_db()

    schedule.every(COLLECTOR_INTERVAL_MIN).minutes.do(twitter_collector_job)
    schedule.every(PUBLISHER_INTERVAL_MIN).minutes.do(publisher_job)

    # İlk açılışta collector'ı çalıştır, publisher schedule'a bırakılsın (3 dk gap için)
    logger.info("İlk collector çağrısı...")
    twitter_collector_job()

    logger.info("Ana döngüye giriliyor. Ctrl+C ile durdurulabilir.")
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Bot kapatılıyor (kullanıcı isteği).")
            break
        except Exception as e:
            logger.exception(f"Ana döngüde beklenmedik hata: {e}")
            time.sleep(60)
