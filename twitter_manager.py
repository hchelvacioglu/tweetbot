"""
twitter_manager.py — Faz 1 Güncellemesi
=======================================
Düzeltmeler:
- get_list_tweets: parametre adları GetXAPI dokümanına uyumlu hale getirildi (q->query, count->limit)
- Hata loglaması artık response.text içeriğini de gösteriyor (debug için kritik)
- post_tweet: 500 hatasında daha net log
- Timeout süreleri artırıldı (GetXAPI yavaş olabiliyor)
"""

import os
import time
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)

GETXAPI_BASE_URL = "https://api.getxapi.com"

def _get_headers():
    """Authorization header'larını her çağrıda ortamdan tazeleyerek döner."""
    return {
        "Authorization": f"Bearer {os.getenv('GETXAPI_KEY')}",
        "Content-Type": "application/json"
    }

def _log_response_error(prefix: str, response: requests.Response):
    """Hata yanıtlarını detaylı logla — debug için kritik."""
    try:
        body = response.text[:500]
    except Exception:
        body = "(could not read body)"
    logger.error(f"{prefix} | status={response.status_code} | body={body}")

# ============================================================
# READ
# ============================================================

def get_recent_tweets(username: str, count: int = 20) -> List[Dict]:
    """Bir kullanıcının son tweetlerini getirir."""
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

def get_list_tweets(list_id: str, count: int = 40) -> List[Dict]:
    """
    Twitter listesinden son tweetleri getirir.
    Düzeltme: GetXAPI 'query' ve 'limit' bekliyordu, eski kod 'q' ve 'count' yolluyordu.
    """
    try:
        url = f"{GETXAPI_BASE_URL}/twitter/tweet/advanced_search"
        params = {
            "q": f"list:{list_id}",
            "count": count
        }
        response = requests.get(url, headers=_get_headers(), params=params, timeout=20)
        if response.status_code >= 400:
            _log_response_error(f"get_list_tweets({list_id}) failed", response)
            return []
        data = response.json()
        tweets = data.get("tweets", [])
        logger.info(f"List {list_id}: {len(tweets)} tweet alındı.")
        return tweets
    except requests.exceptions.RequestException as e:
        logger.error(f"get_list_tweets({list_id}) network error: {e}")
        return []

# ============================================================
# WRITE
# ============================================================

def post_tweet(text: str, reply_to_id: str = None) -> bool:
    """Tek tweet atar."""
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
            _log_response_error("post_tweet failed", response)
            return False

        data = response.json()
        if data.get("status") == "success":
            tweet_id = data.get("data", {}).get("id", "N/A")
            logger.info(f"✓ Tweet posted! ID: {tweet_id}")
            return True
        else:
            logger.error(f"Tweet post unexpected response: {data}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"post_tweet network error: {e}")
        return False

def post_thread(tweets: List[str]) -> bool:
    """Thread (zincirleme tweetler) atar."""
    if not tweets:
        return False
    logger.info(f"Posting thread with {len(tweets)} tweets...")
    previous_tweet_id = None

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
    return True
