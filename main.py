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
import re
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

import sqlite3
import database
import ai_manager
import twitter_manager
import summary_card
from summary_card import generate_summary_card

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
# AI'ın ATLA dediği tweet hash'leri — aynı tweet'i tekrar AI'a yollamayalım
_atlanan_hashes = set()
_ATLANAN_CACHE_MAX_SIZE = 5000  # cache max boyut, dolduğunda sıfırla
_atla_cache_last_cleared = datetime.datetime.now()  # Hot Fix 23


def _maybe_clear_atla_cache(hours: int = 2):
    """ATLA cache son N saatte temizlenmediyse temizle. (Hot Fix 23)"""
    global _atla_cache_last_cleared
    elapsed = (datetime.datetime.now() - _atla_cache_last_cleared).total_seconds() / 3600
    if elapsed >= hours:
        old_size = len(_atlanan_hashes)
        _atlanan_hashes.clear()
        _atla_cache_last_cleared = datetime.datetime.now()
        logger.info(f"[Collector] 🧠 ATLA cache temizlendi ({old_size} hash silindi, {hours} saat doldu)")


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

def has_media(tweet: dict) -> bool:
    media = tweet.get('media') or []
    return isinstance(media, list) and len(media) > 0

def get_media_type(tweet: dict) -> str:
    """
    Tweet'teki medya türünü döner.
    Returns: 'video', 'gif', 'photo', veya 'none'
    """
    media = tweet.get('media') or []
    if not isinstance(media, list) or len(media) == 0:
        return 'none'
    first = media[0] if isinstance(media[0], dict) else {}
    mtype = (first.get('type') or '').lower()
    if mtype == 'video':
        return 'video'
    if mtype == 'animated_gif':
        return 'gif'
    if mtype == 'photo':
        return 'photo'
    return 'none'

def get_media_expanded_url(tweet: dict) -> str:
    """
    Tweet'in media[0].expanded_url'sini döner. Bu URL Twitter tarafından zaten
    /video/1 veya /photo/1 ile biten doğru kaynak URL'si.

    Önemli: bbosports gibi hesaplar başka tweet'ten video embed edebilir.
    Bu durumda expanded_url ALT kaynağa (orijinal video sahibine) işaret eder.
    Bot kendi URL'si yerine bunu kullanmalı, yoksa video kırık görünür.

    Returns: expanded URL veya boş string
    """
    media = tweet.get('media') or []
    if not isinstance(media, list) or len(media) == 0:
        return ''
    first = media[0] if isinstance(media[0], dict) else {}
    expanded = first.get('expanded_url') or ''
    return expanded.strip()

def has_tco_in_original(tweet: dict) -> bool:
    """
    Orijinal tweet metninde t.co linki var mı?
    Eğer varsa, AI o linki aktarır ve Twitter otomatik kart açar.
    Yoksa bot manuel olarak /photo/1 eklemelidir.
    """
    text = tweet.get('text', '') or tweet.get('full_text', '') or ''
    return 't.co/' in text

def is_quote_of_quote(tweet: dict) -> bool:
    qt = tweet.get('quoted_tweet')
    return qt is not None and isinstance(qt, dict) and bool(qt)

def get_hour_thresholds() -> dict:
    """
    Türkiye saatine göre filtre eşiklerini döner.
    Saat dilimleri:
    - 22-06: Gece (sessiz saatler, gevşek eşikler)
    - 06-10: Sabah (transfer patlaması, gevşek eşikler)
    - 10-16: Gündüz (normal akış)
    - 16-22: Akşam (yoğun trafik, sıkı eşikler)
    """
    tr_hour = datetime.datetime.now().hour

    if 6 <= tr_hour < 10:
        return {'profil': 'sabah',  'tr_hour': tr_hour, 'min_likes': 10, 'rt_ratio': 0.005, 'reply_ratio': 0.05,  'interest': 0.10}
    if 10 <= tr_hour < 16:
        return {'profil': 'gündüz', 'tr_hour': tr_hour, 'min_likes': 15, 'rt_ratio': 0.01,  'reply_ratio': 0.10,  'interest': 0.15}
    if 16 <= tr_hour < 22:
        return {'profil': 'akşam',  'tr_hour': tr_hour, 'min_likes': 25, 'rt_ratio': 0.015, 'reply_ratio': 0.12,  'interest': 0.20}
    return     {'profil': 'gece',   'tr_hour': tr_hour, 'min_likes': 8,  'rt_ratio': 0.003, 'reply_ratio': 0.05,  'interest': 0.08}


def get_rate_threshold(followers: int) -> float:
    if followers < 10_000:
        return 0.003
    if followers < 100_000:
        return 0.002
    if followers < 500_000:
        return 0.001
    return 0.0005

def should_collect(tweet: dict) -> tuple:
    likes = tweet.get('likeCount', 0) or 0
    retweets = tweet.get('retweetCount', 0) or 0
    replies = tweet.get('replyCount', 0) or 0
    author = tweet.get('author') or {}
    followers = author.get('followers', 0) or 0

    th = get_hour_thresholds()

    if likes < th['min_likes']:
        return (False, "low_likes")

    rt_ratio = retweets / likes if likes > 0 else 0
    reply_ratio = replies / likes if likes > 0 else 0
    if rt_ratio < th['rt_ratio'] and reply_ratio < th['reply_ratio']:
        return (False, "no_news_signal")

    if followers > 0:
        rate = likes / followers
        threshold = get_rate_threshold(followers)
        if rate >= threshold:
            return (True, "engagement_rate")

    interest = (retweets * 3 + replies) / likes if likes > 0 else 0
    if interest >= th['interest']:
        return (True, "interest_score")

    return (False, "low_quality")

# ============================================================
# Publisher
# ============================================================

def publisher_job():
    # Hot Fix 16: Bayat tweet'leri temizle (30 dk üstü)
    # Hot Fix 17: Yedek güvenlik — collector geç çalışırsa veya crash olursa devreye girer
    stale_count = database.cleanup_stale_pending_tweets(max_age_minutes=60)
    if stale_count > 0:
        logger.info(f"[Publisher] 🗑️ {stale_count} bayat tweet temizlendi (30+ dk eski, Basarisiz işaretlendi)")

    if is_night_time():
        logger.info("[Publisher] Gece modu (03-07), atlandı.")
        return

    tweet = database.get_oldest_pending_tweet()
    if not tweet:
        return

    tweet_id = tweet['id']
    tweet_content_str = tweet['tweet_content']
    share_type = tweet.get('share_type') or 'text'
    quote_url = tweet.get('quote_url')

    try:
        content_to_post = json.loads(tweet_content_str)
    except (json.JSONDecodeError, TypeError):
        content_to_post = tweet_content_str

    # Faz 5: Quote tweet kaldırıldı. Tüm paylaşımlar post_tweet ile.
    # video_embed durumunda tweet_content zaten "metin + URL" şeklinde.
    if isinstance(content_to_post, list):
        result = twitter_manager.post_thread(content_to_post)
    else:
        result = twitter_manager.post_tweet(content_to_post if isinstance(content_to_post, str) else "")

    # result: ya str (tweet ID) ya True ya False ya "skip_too_long"
    if result == "skip_too_long":
        database.update_tweet_status(tweet_id, 'Failed')
        logger.info(f"[Publisher] ⏭️ ID {tweet_id} 280+ karakter, GetXAPI çağrısı yapılmadı (para tasarrufu).")
    elif result:
        if isinstance(result, str) and result:
            database.save_posted_tweet_id(tweet_id, result)
            logger.info(f"[Publisher] ✓ ID {tweet_id} ({share_type}) paylaşıldı (Twitter ID: {result}).")
        else:
            # ID alınamadı, sadece status güncelle (eski davranış, engagement yapılmayacak)
            database.update_tweet_status(tweet_id, 'Paylasildi')
            logger.warning(f"[Publisher] ✓ ID {tweet_id} paylaşıldı ama Twitter ID alınamadı.")
    else:
        # Faz 8: Tek deneme — fail ise direkt bırak, retry yok
        database.update_tweet_status(tweet_id, 'Failed')
        logger.warning(f"[Publisher] ✗ ID {tweet_id} fail. Tek deneme, abandon edildi.")

# ============================================================
# Collector
# ============================================================

def twitter_collector_job():
    global _ai_quota_blocked_until, _atlanan_hashes

    if not TWITTER_LIST_ID:
        logger.warning("[Collector] TWITTER_LIST_ID yok, atlandı.")
        return

    if _ai_quota_blocked_until and datetime.datetime.now() < _ai_quota_blocked_until:
        remaining = (_ai_quota_blocked_until - datetime.datetime.now()).total_seconds() / 60
        logger.info(f"[Collector] AI kotası kilitli, {remaining:.0f} dk sonra tekrar denenecek.")
        return

    # Hot Fix 23: ATLA cache 2 saatte bir temizle
    _maybe_clear_atla_cache(hours=2)

    # Hot Fix 17: Yeni cycle, kuyruğu sıfırla — önceki cycle'dan kalanlar bayatlamış olabilir
    cleared = database.clear_all_pending_tweets()
    if cleared > 0:
        logger.info(f"[Collector] 🧹 {cleared} eski bekleyen tweet temizlendi (Iptal işaretlendi)")

    th = get_hour_thresholds()
    logger.info(
        f"[Collector] Başlıyor... (ATLA cache: {len(_atlanan_hashes)} hash, "
        f"profil: {th['profil']} TR{th['tr_hour']:02d}:xx, "
        f"min_likes: {th['min_likes']})"
    )
    recent_titles = database.get_recent_news_titles(hours=12)

    tweets = twitter_manager.get_list_tweets(TWITTER_LIST_ID, count=60)
    if not tweets:
        logger.info("[Collector] Listeden tweet alınamadı, atlandı.")
        return

    pending_items = []
    skipped_low_likes = 0
    skipped_no_signal = 0
    skipped_low_quality = 0
    skipped_age = 0
    skipped_dup = 0
    media_count = 0
    quote_skip_count = 0

    for tweet in tweets:
        text = tweet.get('text', '')
        if not text:
            continue

        if is_too_old(tweet.get('createdAt')):
            skipped_age += 1
            continue

        passes, reason = should_collect(tweet)
        if not passes:
            if reason == "low_likes":
                skipped_low_likes += 1
            elif reason == "no_news_signal":
                skipped_no_signal += 1
            elif reason == "low_quality":
                skipped_low_quality += 1
            continue

        if database.hash_exists(text):
            skipped_dup += 1
            continue

        # AI cache: bu tweet'i daha önce AI ATLA demişse tekrar yollama
        text_hash = database.make_hash(text)
        if text_hash in _atlanan_hashes:
            skipped_dup += 1
            continue

        media_type = get_media_type(tweet)
        is_qoq = is_quote_of_quote(tweet)
        has_link_in_text = has_tco_in_original(tweet)

        # Karar (Faz 7 hot fix 5 — quote bug fix):
        # Üst tweetin KENDİ medyası varsa, quote olması engelleyici DEĞİL.
        # Bot media_type'a göre embed yapar (quote arka planda kalır).
        # - Video/GIF varsa → video_embed (Faz 5: /video/1)
        # - Foto varsa → photo_embed (Faz 8 hot fix 15: has_tco_in_original kontrolü kaldırıldı)
        # - Medyası yok → text
        if media_type in ('video', 'gif'):
            share_decision = 'video_embed'
        elif media_type == 'photo':
            # Faz 8 hot fix 15: has_tco_in_original kontrolü kaldırıldı.
            # Faz 8'de AI metin yazmıyor, hot fix 10 t.co'ları siliyor, hot fix 11 expanded_url
            # kullanıyor — t.co kontrolü artık anlamsız ve foto'lu tweet'leri text'e atıyor (bug).
            share_decision = 'photo_embed'
        else:
            share_decision = 'text'

        if media_type != 'none':
            media_count += 1
        if is_qoq:
            quote_skip_count += 1

        followers = (tweet.get('author') or {}).get('followers', 0) or 0
        likes = tweet.get('likeCount', 0) or 0
        retweets = tweet.get('retweetCount', 0) or 0
        replies = tweet.get('replyCount', 0) or 0

        username = tweet.get('userName', '') or ''
        tweet_url = tweet.get('url', '') or ''
        if not username and tweet_url:
            parts = tweet_url.rstrip('/').split('/')
            if len(parts) >= 4 and parts[-2] == 'status':
                username = parts[-3]
        source = f"@{username}" if username else ""

        logger.info(
            f"[Collector]   ✓ Aday: @{username} (followers: {followers}, "
            f"likes: {likes}, rt: {retweets}, reply: {replies}, reason: {reason})"
        )

        pending_items.append({
            "title": text,
            "description": "",
            "link": tweet_url,
            "source": source,
            "published_date": tweet.get('createdAt') or time.strftime('%Y-%m-%d %H:%M:%S'),
            "_share_decision": share_decision,
            "_tweet_url": tweet_url,
            "_source_username": username,
            "_tweet": tweet,
        })

    logger.info(
        f"[Collector] Filtreleme: {len(tweets)} tweet → {len(pending_items)} aday "
        f"(düşük beğeni: {skipped_low_likes}, eğlence: {skipped_no_signal}, "
        f"düşük kalite: {skipped_low_quality}, eski: {skipped_age}, mükerrer: {skipped_dup}, "
        f"medya: {media_count}, q-of-q skip: {quote_skip_count})"
    )

    if not pending_items:
        return

    saved_count = 0
    video_embed_count = 0
    photo_embed_count = 0
    text_count = 0
    duplicate_count = 0

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

        # Faz 8: ATLA cache — PAYLAS olmayan tüm batch itemlarını ekle
        paylas_idxs = {res['idx'] for res in results}
        for i, item in enumerate(batch):
            if i not in paylas_idxs:
                _atlanan_hashes.add(database.make_hash(item['title']))
        if len(_atlanan_hashes) > _ATLANAN_CACHE_MAX_SIZE:
            logger.info(f"[Collector] ATLA cache {_ATLANAN_CACHE_MAX_SIZE} aşıldı, sıfırlanıyor")
            _atlanan_hashes.clear()

        for res in results:
            idx = res.get('idx')
            if idx is None or idx >= len(batch):
                continue

            original_item = batch[idx]
            share_decision = original_item.get('_share_decision', 'text')
            tweet_url = original_item.get('_tweet_url', '')
            source_username = original_item.get('_source_username', '')
            orig_tweet = original_item.get('_tweet') or {}

            # Faz 8: Orijinal tweet metnini AYNEN al (AI metin üretmiyor)
            base_text = (original_item.get('title') or '').strip()

            # Hot fix 10 mantığı korunuyor: t.co linklerini sil (çift link olmasın)
            base_text = re.sub(r'\s*https?://t\.co/\w+\s*', ' ', base_text).strip()
            base_text = re.sub(r'\s+', ' ', base_text).strip()

            # Kaynak ekleme kontrolleri

            # 1a. Tweet'in SONUNDA zaten parantezli ifade var mı? (genel kontrol)
            has_source_at_end = bool(re.search(r'\([^)]+\)\s*$', base_text))

            # 1b. Tweet'in HERHANGI YERİNDE bilinen Türk medya kaynağı veya gazeteci adı parantezli mi?
            KNOWN_SOURCES = (
                r'Takvim|Sözcü|Hürriyet|Sabah|Fanatik|Milliyet|Star|Posta|Türkiye|Akşam|'
                r'CNN Türk|NTV|Habertürk|A Spor|TRT|TRT Spor|Show TV|Kanal D|ATV|FOX|'
                r'Fotomaç|Fotospor|AMK|Ajansspor|beIN Sports|S Sport|Eurosport|Tivibu|'
                r'Anadolu Ajansı|İHA|DHA|AA|Sky Sport|Tuttosport|Gazzetta|L\'Equipe|'
                r'Goal|ESPN|BBC|Telegraph|Mirror|Sun|Marca|AS|Bild|Kicker'
            )
            media_pattern = re.compile(
                rf'\(\s*(?:@\w+|{KNOWN_SOURCES})\s*\)',
                re.IGNORECASE
            )
            ad_soyad_pattern = re.compile(
                r'\(\s*[A-ZÇĞİÖŞÜ][a-zçğıöşüA-ZÇĞİÖŞÜ\.]+\s+[A-ZÇĞİÖŞÜ][a-zçğıöşüA-ZÇĞİÖŞÜ\.]+\s*\)'
            )
            has_known_source_anywhere = bool(media_pattern.search(base_text)) or bool(ad_soyad_pattern.search(base_text))
            has_existing_source = has_source_at_end or has_known_source_anywhere

            # 2. Tweet "İsim:" veya "İsim Soyisim:" formatında başlıyor mu?
            starts_with_named_quote = bool(re.match(
                r'^[A-ZÇĞİÖŞÜ][a-zA-ZçğıöşüÇĞİÖŞÜ\.]+(\s+[A-ZÇĞİÖŞÜ][a-zA-ZçğıöşüÇĞİÖŞÜ\.]+){0,2}\s*:',
                base_text
            ))

            # @username sadece eklenir eğer: kaynak yok + "İsim:" başlangıcı yok
            if (source_username
                and f"@{source_username}" not in base_text
                and not has_existing_source
                and not starts_with_named_quote):
                base_text = f"{base_text} (@{source_username})"

            # Hot Fix 21: Jaccard similarity dedup (mükerrer haber engelleme)
            if database.is_duplicate_recent_tweet(base_text, hours=2, threshold=0.6):
                logger.info(f"[Collector] 🔁 Mükerrer haber atlandı (son 2 saatte benzer atıldı): {base_text[:60]}...")
                duplicate_count += 1
                continue

            if share_decision == 'video_embed' and tweet_url:
                # Hot fix 11 mantığı korunuyor: media[0].expanded_url öncelikli
                expanded = get_media_expanded_url(orig_tweet)
                if expanded and '/video/' in expanded:
                    video_url = expanded.split('?')[0]
                else:
                    video_url = twitter_manager.make_video_embed_url(tweet_url)
                full_text = f"{video_url} {base_text}"
                ok = database.add_pending_tweet(
                    title=res['title'], link=res['link'], published_date=res['published_date'],
                    tweet_content=full_text,
                    share_type='video_embed'
                )
                if ok:
                    video_embed_count += 1
            elif share_decision == 'photo_embed' and tweet_url:
                # Hot fix 11 mantığı korunuyor: media[0].expanded_url öncelikli
                expanded = get_media_expanded_url(orig_tweet)
                if expanded and '/photo/' in expanded:
                    photo_url = expanded.split('?')[0]
                else:
                    photo_url = twitter_manager.make_photo_embed_url(tweet_url)
                full_text = f"{photo_url} {base_text}"
                ok = database.add_pending_tweet(
                    title=res['title'], link=res['link'], published_date=res['published_date'],
                    tweet_content=full_text,
                    share_type='photo_embed'
                )
                if ok:
                    photo_embed_count += 1
            else:
                ok = database.add_pending_tweet(
                    title=res['title'], link=res['link'], published_date=res['published_date'],
                    tweet_content=base_text,
                    share_type='text'
                )
                if ok:
                    text_count += 1

            if ok:
                saved_count += 1
                recent_titles.append(res['title'])
                markers = {'video_embed': '🎬', 'photo_embed': '📷', 'text': '📝'}
                marker = markers.get(share_decision, '📝')
                logger.info(f"[Collector] {marker} Kuyruğa: {res['title'][:60]}")

        if i + AI_BATCH_SIZE < len(pending_items):
            time.sleep(5)

    logger.info(
        f"[Collector] Bitti. {saved_count} yeni tweet "
        f"(video: {video_embed_count}, foto: {photo_embed_count}, text: {text_count}, dup_atlandı: {duplicate_count})."
    )

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
# Saatlik Özet Kart — Faz 10 / Hot Fix 25
# ============================================================

SUMMARY_TEST_MODE = os.getenv("SUMMARY_TEST_MODE", "true").lower() == "true"
SUMMARY_HOURS_ACTIVE = set(range(8, 24)) | {0, 1, 2}  # 08:00-23:00 + 00:00, 01:00, 02:00


def hourly_summary_job():
    """Her tam saatte tetiklenir. Son 1 saatin tweet özetini kart olarak hazırlar."""
    now = datetime.datetime.now()

    if now.hour not in SUMMARY_HOURS_ACTIVE:
        logger.info(f"[Summary] Saat {now.hour}:00 — aktif saat değil, atlandı")
        return

    one_hour_ago = now - datetime.timedelta(hours=1)

    conn = sqlite3.connect(database.DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tweet_content, posted_at
            FROM Bekleyen_Tweetler
            WHERE status = 'Paylasildi'
              AND posted_at >= ?
            ORDER BY posted_at DESC
            LIMIT 7
            """,
            (one_hour_ago.strftime('%Y-%m-%d %H:%M:%S'),),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if len(rows) < 3:
        logger.info(f"[Summary] Son 1 saatte {len(rows)} tweet — yetersiz (min 3), kart atılmadı")
        return

    logger.info(f"[Summary] {len(rows)} tweet için kart hazırlanıyor...")

    news_items = []
    for row in rows:
        try:
            headline = ai_manager.summarize_for_card(row["tweet_content"], max_chars=60)
            if headline:
                news_items.append(headline)
        except Exception as e:
            logger.warning(f"[Summary] Başlık çıkarma hata (id={row['id']}): {e}")

    if len(news_items) < 3:
        logger.warning(f"[Summary] Başlık çıkarma sonrası {len(news_items)} kaldı, kart atılmadı")
        return

    time_range = f"{one_hour_ago.strftime('%H:00')} - {now.strftime('%H:00')}"
    success, png_path, b64_data = generate_summary_card(news_items, time_range)

    if not success:
        logger.error("[Summary] Kart üretimi başarısız")
        return

    logger.info(f"[Summary] ✓ Kart hazır: {png_path}")

    if SUMMARY_TEST_MODE:
        logger.info(f"[Summary] 🧪 TEST MODE — Twitter'a atılmadı. PNG: {png_path}")
        return

    if not b64_data:
        logger.error("[Summary] Base64 data yok, atılamadı")
        return

    tweet_text = f"🏆 SAATLİK ÖZET — {now.strftime('%H:00')}"
    try:
        result = twitter_manager.post_tweet_with_media(tweet_text, b64_data, media_type="image/png")
        if result:
            logger.info(f"[Summary] ✓ Özet kart atıldı: {result.get('data', {}).get('id')}")
        else:
            logger.error("[Summary] Atış başarısız")
    except Exception as e:
        logger.error(f"[Summary] Atış hata: {e}")

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("X Sports News Bot başlatılıyor (Faz 8 — Orijinal Metin + Yavaşlatma + Tek Deneme)...")
    logger.info(f"AI Provider: {ai_manager.AI_PROVIDER}")
    logger.info(f"List ID: {TWITTER_LIST_ID}")
    logger.info(f"Filtre: Akıllı (engagement rate + RT ratio) | Max Age: {MAX_TWEET_AGE_HOURS}h | AI Batch: {AI_BATCH_SIZE}")
    logger.info(f"Collector: her {COLLECTOR_INTERVAL_MIN}dk | Publisher: her {PUBLISHER_INTERVAL_MIN}dk | Engagement: her {ENGAGEMENT_INTERVAL_MIN}dk")
    logger.info("=" * 60)

    database.init_db()

    schedule.every(COLLECTOR_INTERVAL_MIN).minutes.do(twitter_collector_job)
    schedule.every(PUBLISHER_INTERVAL_MIN).minutes.do(publisher_job)
    schedule.every(ENGAGEMENT_INTERVAL_MIN).minutes.do(engagement_tracker_job)
    # Faz 10 / Hot Fix 25: Saatlik özet kart (her tam saatin :00'ında)
    schedule.every().hour.at(":00").do(hourly_summary_job)

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
