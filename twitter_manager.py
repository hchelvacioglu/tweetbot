"""
twitter_manager.py — Faz 2 Güncellemesi
=======================================
Faz 1'e ek olarak:
- get_tweet_metrics(tweet_id): tek bir tweet'in beğeni/RT/reply/view sayısını çeker
- get_tweets_metrics_batch(tweet_ids): birden fazla tweet için aynı sorguyu yapar
- get_list_tweets'te 'q' ve 'count' parametreleri korundu (GetXAPI doğru olanı bunlar)
- post_tweet artık başarıda Twitter ID'sini döner (engagement tracking için kritik)
"""

import os
import time
import logging
import requests
from typing import List, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

GETXAPI_BASE_URL = "https://api.getxapi.com"
TWEET_HARD_LIMIT = 280  # GetXAPI auth token standart, 280 üstü 500 dönüyor

def make_video_embed_url(tweet_url: str) -> str:
    """
    Tweet URL'sini /video/1 formatına çevirir.
    Query parametrelerini temizler (?s=20 vb).

    Örnek:
      https://x.com/user/status/123?s=20  →  https://x.com/user/status/123/video/1
      https://x.com/user/status/123       →  https://x.com/user/status/123/video/1
    """
    if not tweet_url:
        return ""
    clean = tweet_url.split('?')[0].split('#')[0]
    clean = clean.rstrip('/')
    return f"{clean}/video/1"

def make_photo_embed_url(tweet_url: str) -> str:
    """
    Tweet URL'sini /photo/1 formatına çevirir.
    Query parametrelerini temizler (?s=20 vb).
    Mantık make_video_embed_url ile aynı, sadece ek "/photo/1".

    Örnek:
      https://x.com/user/status/123?s=20  →  https://x.com/user/status/123/photo/1
    """
    if not tweet_url:
        return ""
    clean = tweet_url.split('?')[0].split('#')[0]
    clean = clean.rstrip('/')
    return f"{clean}/photo/1"

def _get_headers():
    return {
        "Authorization": f"Bearer {os.getenv('GETXAPI_KEY')}",
        "Content-Type": "application/json"
    }

def _log_response_error(prefix: str, response: requests.Response):
    try:
        body = response.text[:500]
    except Exception:
        body = "(could not read body)"
    logger.error(f"{prefix} | status={response.status_code} | body={body}")

# ============================================================
# READ — Liste / Kullanıcı Tweetleri
# ============================================================

def get_recent_tweets(username: str, count: int = 20) -> List[Dict]:
    try:
        url = f"{GETXAPI_BASE_URL}/twitter/user/tweets"
        params = {"userName": username}
        response = requests.get(url, headers=_get_headers(), params=params, timeout=20)
        if response.status_code >= 400:
            _log_response_error(f"get_recent_tweets({username}) failed", response)
            return []
        data = response.json()
        tweets = data.get("tweets", [])
        return tweets[:count]
    except requests.exceptions.RequestException as e:
        logger.error(f"get_recent_tweets({username}) network error: {e}")
        return []

def get_list_tweets(list_id: str, count: int = 60) -> List[Dict]:
    """
    Twitter listesinden son tweet'leri pagination ile getirir.
    Hedef tweet sayısına ulaşana kadar veya max sayfaya kadar döner.
    Dedup uygulanır (aynı tweet_id 2x gelirse atılır).
    """
    MAX_PAGES = 3
    all_tweets = []
    seen_ids = set()
    cursor = None

    try:
        for page in range(MAX_PAGES):
            url = f"{GETXAPI_BASE_URL}/twitter/tweet/advanced_search"
            params = {
                "q": f"list:{list_id}",
                "count": 20
            }
            if cursor:
                params["cursor"] = cursor

            response = requests.get(url, headers=_get_headers(), params=params, timeout=20)
            if response.status_code >= 400:
                _log_response_error(f"get_list_tweets({list_id}) page {page+1} failed", response)
                break

            data = response.json()
            page_tweets = data.get("tweets", [])
            if not page_tweets:
                break

            new_count = 0
            for tweet in page_tweets:
                tweet_id = tweet.get('id') or tweet.get('id_str') or tweet.get('tweet_id')
                if tweet_id and tweet_id not in seen_ids:
                    seen_ids.add(tweet_id)
                    all_tweets.append(tweet)
                    new_count += 1

            logger.info(f"List {list_id} sayfa {page+1}: {len(page_tweets)} geldi, {new_count} yeni (toplam {len(all_tweets)})")

            if len(all_tweets) >= count:
                break

            cursor = data.get("next_cursor")
            has_more = data.get("has_more", False)
            if not cursor or not has_more:
                break

        logger.info(f"List {list_id}: toplam {len(all_tweets)} tweet alındı ({page+1} sayfa).")
        return all_tweets[:count]
    except requests.exceptions.RequestException as e:
        logger.error(f"get_list_tweets({list_id}) network error: {e}")
        return all_tweets

# ============================================================
# READ — Engagement Metrics (Faz 2 yeni)
# ============================================================

def get_tweet_metrics(tweet_id: str) -> Optional[Dict]:
    """
    Tek bir tweet'in son metric'lerini çeker.
    Returns: {'likes': X, 'retweets': Y, 'replies': Z, 'views': W} or None
    
    GetXAPI'nin tweet detail endpoint'i kullanılır.
    """
    try:
        url = f"{GETXAPI_BASE_URL}/twitter/tweet/detail"
        params = {"id": tweet_id}
        response = requests.get(url, headers=_get_headers(), params=params, timeout=20)
        if response.status_code >= 400:
            _log_response_error(f"get_tweet_metrics({tweet_id}) failed", response)
            return None
        data = response.json()
        # GetXAPI farklı response yapıları döndürebiliyor — esnek parse
        tweet = data.get("data") or data.get("tweet") or {}
        if not tweet or not isinstance(tweet, dict):
            return None
        return {
            "likes": int(tweet.get("likeCount", 0) or 0),
            "retweets": int(tweet.get("retweetCount", 0) or 0),
            "replies": int(tweet.get("replyCount", 0) or 0),
            "views": int(tweet.get("viewCount", 0) or 0),
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"get_tweet_metrics({tweet_id}) network error: {e}")
        return None
    except (ValueError, TypeError) as e:
        logger.error(f"get_tweet_metrics({tweet_id}) parse error: {e}")
        return None

# ============================================================
# WRITE — Tweet Atma
# ============================================================

def post_tweet(text: str, reply_to_id: str = None) -> Union[bool, str]:
    """
    Tek tweet atar.
    Faz 2: başarıda Twitter tweet ID'sini döner (string).
    Başarısızlıkta False döner. (Geri uyumlu — bool kontrolünde False çalışır.)
    """
    # Hot Fix 14 (280+ skip) Hot Fix 24 ile kaldırıldı.
    # Sebep: Yeni X auth token Premium claim taşıyor, 280+ note tweet artık çalışıyor.
    # Manuel test (8 Mayıs, 331 karakter): GetXAPI Status 200 döndü.

    try:
        url = f"{GETXAPI_BASE_URL}/twitter/tweet/create"
        payload = {
            "auth_token": os.getenv("X_AUTH_TOKEN"),
            "text": text
        }
        if reply_to_id:
            payload["reply_to_tweet_id"] = reply_to_id

        logger.info(f"Posting tweet: {text[:60]}...")
        response = requests.post(url, headers=_get_headers(), json=payload, timeout=45)

        if response.status_code >= 400:
            body_text = response.text or ""
            # Faz 7 hot fix 6 — 187 duplicate = tweet zaten atılmış, başarılı say
            if "187" in body_text or "duplicate" in body_text.lower():
                logger.warning(f"⚠️ Duplicate (kod 187): tweet zaten atılmış sayılıyor | status={response.status_code}")
                return "duplicate_already_posted"
            _log_response_error("post_tweet failed", response)
            return False

        data = response.json()
        if data.get("status") == "success":
            tweet_id = data.get("data", {}).get("id", "")
            logger.info(f"✓ Tweet posted! ID: {tweet_id}")
            return tweet_id if tweet_id else True  # ID yoksa True döner (geri uyumluluk)
        else:
            logger.error(f"Tweet post unexpected response: {data}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"post_tweet network error: {e}")
        return False

def post_thread(tweets: List[str]) -> Union[bool, str]:
    """
    Thread atar. İlk tweet'in ID'sini döner (engagement için anchor).
    """
    if not tweets:
        return False
    logger.info(f"Posting thread with {len(tweets)} tweets...")
    previous_tweet_id = None
    first_tweet_id = None

    for i, tweet_text in enumerate(tweets):
        try:
            url = f"{GETXAPI_BASE_URL}/twitter/tweet/create"
            payload = {
                "auth_token": os.getenv("X_AUTH_TOKEN"),
                "text": tweet_text
            }
            if previous_tweet_id:
                payload["reply_to_tweet_id"] = previous_tweet_id

            response = requests.post(url, headers=_get_headers(), json=payload, timeout=45)
            if response.status_code >= 400:
                _log_response_error(f"thread tweet {i+1} failed", response)
                return False

            data = response.json()
            if data.get("status") == "success":
                previous_tweet_id = data.get("data", {}).get("id")
                if i == 0:
                    first_tweet_id = previous_tweet_id
                logger.info(f"Thread tweet {i+1}/{len(tweets)} posted. ID: {previous_tweet_id}")
            else:
                logger.error(f"Thread tweet {i+1} unexpected: {data}")
                return False

            if i < len(tweets) - 1:
                time.sleep(2)
        except requests.exceptions.RequestException as e:
            logger.error(f"Thread tweet {i+1} network error: {e}")
            return False

    logger.info("✓ Thread posted!")
    return first_tweet_id if first_tweet_id else True

def post_quote_tweet(text: str, quote_url: str):
    """
    Quote tweet atar. quote_url tam tweet URL'si olmalı.
    Başarıda yeni tweet ID döner (string), aksi False döner.
    """
    try:
        url = f"{GETXAPI_BASE_URL}/twitter/tweet/create"
        payload = {
            "auth_token": os.getenv("X_AUTH_TOKEN"),
            "text": text,
            "quote_tweet_url": quote_url
        }
        logger.info(f"Posting quote tweet: '{text[:40]}' quoting {quote_url[-40:]}")
        response = requests.post(url, headers=_get_headers(), json=payload, timeout=45)
        if response.status_code >= 400:
            _log_response_error("post_quote_tweet failed", response)
            return False
        data = response.json()
        if data.get("status") == "success":
            tweet_id = data.get("data", {}).get("id", "")
            logger.info(f"✓ Quote tweet posted! ID: {tweet_id}")
            return tweet_id if tweet_id else True
        else:
            logger.error(f"Quote tweet unexpected response: {data}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"post_quote_tweet network error: {e}")
        return False
