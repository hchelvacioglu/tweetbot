"""
main.py — Faz 2 Güncellemesi
============================
Faz 1'e ek olarak:
- Publisher artık post_tweet'in döndürdüğü Twitter ID'yi DB'ye kaydediyor (engagement için kritik)
- Yeni: engagement_tracker_job() — saatte bir, son 24h'de paylaşılmış tweetlerin metric'lerini çeker
- Yeni schedule: engagement her saat başı çalışır
- Eski 'Paylasildi' status'u korundu (geri uyumluluk için)
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
ENGAGEMENT_INTERVAL_MIN = int(os.getenv("ENGAGEMENT_INTERVAL_MIN", 60))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", 5))  # Faz 2: 10 → 5 (truncation çözümü)

_ai_quota_blocked_until = None

# ============================================================
# Yardımcılar
# ============================================================

def is_night_time() -> bool:
    return 3 <= datetime.datetime.now().hour < 7

def parse_tweet_date(date_str: str):
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        pass
    return None

def is_too_old(date_str: str) -> bool:
    dt = parse_tweet_date(date_str)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - dt
    return age.total_seconds() > MAX_TWEET_AGE_HOURS * 3600

# ============================================================
# Publisher
# ============================================================

def publisher_job():
    if is_night_time():
        logger.info("[Publisher] Gece modu (03-07), atlandı.")
        return

    tweet = database.get_oldest_pending_tweet()
    if not tweet:
        return

    tweet_id = tweet['id']
    tweet_content_str = tweet['tweet_content']

    try:
        content_to_post = json.loads(tweet_content_str)
    except (json.JSONDecodeError, TypeError):
        content_to_post = tweet_content_str

    if isinstance(content_to_post, list):
        result = twitter_manager.post_thread(content_to_post)
    else:
        result = twitter_manager.post_tweet(content_to_post)

    # result: ya str (tweet ID) ya True ya False
    if result:
        if isinstance(result, str) and result:
            # Tweet ID ile kaydet → engagement tracker buradan başlar
            database.save_posted_tweet_id(tweet_id, result)
            logger.info(f"[Publisher] ✓ ID {tweet_id} paylaşıldı (Twitter ID: {result}).")
        else:
            # ID alınamadı, sadece status güncelle (eski davranış, engagement yapılmayacak)
            database.update_tweet_status(tweet_id, 'Paylasildi')
            logger.warning(f"[Publisher] ✓ ID {tweet_id} paylaşıldı ama Twitter ID alınamadı.")
    else:
        database.increment_attempt(tweet_id)
        logger.warning(f"[Publisher] ✗ ID {tweet_id} fail. Attempt sayacı artırıldı.")

# ============================================================
# Collector
# ============================================================

def twitter_collector_job():
    global _ai_quota_blocked_until

    if not TWITTER_LIST_ID:
        logger.warning("[Collector] TWITTER_LIST_ID yok, atlandı.")
        return

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

        likes = tweet.get('likeCount', 0)
        if likes < MIN_LIKES:
            skipped_likes += 1
            continue

        if is_too_old(tweet.get('createdAt')):
            skipped_age += 1
            continue

        if database.hash_exists(text):
            skipped_dup += 1
            continue

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

    saved_count = 0
    # Faz 2: batch size 10 → 5 (configurable via env)
    for i in range(0, len(pending_items), AI_BATCH_SIZE):
        batch = pending_items[i:i + AI_BATCH_SIZE]
        try:
            results = ai_manager.process_news_batch(batch, recent_titles)
        except ai_manager.AIQuotaExceeded:
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

        if i + AI_BATCH_SIZE < len(pending_items):
            time.sleep(5)

    logger.info(f"[Collector] Bitti. {saved_count} yeni tweet kuyruğa eklendi.")

# ============================================================
# Engagement Tracker (Faz 2 yeni)
# ============================================================

def engagement_tracker_job():
    """
    Saatte bir çalışır. Son 24h'de paylaşılmış tweetlerin engagement'ını GetXAPI ile çeker, DB'ye kaydeder.
    Her tweet için 3 snapshot tutulur: 1h, 6h, 24h.
    """
    pending_checks = database.get_pending_engagement_checks()
    if not pending_checks:
        logger.info("[Engagement] Ölçülecek snapshot yok.")
        return
    
    logger.info(f"[Engagement] {len(pending_checks)} snapshot ölçülecek.")
    
    success_count = 0
    fail_count = 0
    
    for check in pending_checks:
        tweet_id = check['tweet_id']
        posted_tweet_id = check['posted_tweet_id']
        snapshot = check['snapshot']
        
        metrics = twitter_manager.get_tweet_metrics(posted_tweet_id)
        if metrics is None:
            fail_count += 1
            logger.warning(f"[Engagement] Tweet {posted_tweet_id} metric alınamadı (snapshot {snapshot}).")
            continue
        
        try:
            database.update_engagement(
                tweet_id=tweet_id,
                snapshot=snapshot,
                likes=metrics['likes'],
                retweets=metrics['retweets'],
                replies=metrics['replies'],
                views=metrics['views']
            )
            success_count += 1
            logger.info(
                f"[Engagement] Tweet {tweet_id} ({snapshot}): "
                f"{metrics['likes']}❤ {metrics['retweets']}🔁 {metrics['replies']}💬 {metrics['views']}👁"
            )
        except Exception as e:
            fail_count += 1
            logger.error(f"[Engagement] DB update hatası tweet {tweet_id}: {e}")
        
        # GetXAPI rate limiting nazikçe
        time.sleep(1)
    
    logger.info(f"[Engagement] Bitti. ✓ {success_count} | ✗ {fail_count}")

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("X Sports News Bot başlatılıyor (Faz 2)...")
    logger.info(f"AI Provider: {ai_manager.AI_PROVIDER}")
    logger.info(f"List ID: {TWITTER_LIST_ID}")
    logger.info(f"Min Likes: {MIN_LIKES} | Max Age: {MAX_TWEET_AGE_HOURS}h | AI Batch: {AI_BATCH_SIZE}")
    logger.info(f"Collector: her {COLLECTOR_INTERVAL_MIN}dk | Publisher: her {PUBLISHER_INTERVAL_MIN}dk | Engagement: her {ENGAGEMENT_INTERVAL_MIN}dk")
    logger.info("=" * 60)

    database.init_db()

    schedule.every(COLLECTOR_INTERVAL_MIN).minutes.do(twitter_collector_job)
    schedule.every(PUBLISHER_INTERVAL_MIN).minutes.do(publisher_job)
    schedule.every(ENGAGEMENT_INTERVAL_MIN).minutes.do(engagement_tracker_job)

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
