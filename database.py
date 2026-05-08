"""
database.py — Faz 2 Güncellemesi
================================
Faz 1'e ek olarak:
- Engagement_Metrics tablosu eklendi (tweet_id ile FK ilişkisi)
- get_recent_posted_tweets(): engagement tracker için, son 24h paylaşılmış tweetleri getirir
- save_posted_tweet_id(): publisher tweet attıktan sonra Twitter ID'yi kaydeder
- update_engagement(): engagement metric'lerini günceller
- get_pending_engagement_checks(): hangi tweet'lerin hangi metric'i ölçüleceğini döner
"""

import sqlite3
import datetime
import hashlib
import re
from typing import List, Dict, Optional, Tuple

DB_NAME = "sports_bot.db"
MAX_ATTEMPTS = 3

# ============================================================
# Hash Helpers (değişmedi)
# ============================================================

def normalize_for_hash(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    replacements = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
        "â": "a", "î": "i", "û": "u",
    }
    for k, v in replacements.items():
        t = t.replace(k, v)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def make_hash(title: str) -> str:
    norm = normalize_for_hash(title)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

# ============================================================
# Schema
# ============================================================

def init_db():
    """DB'yi başlatır. Tüm tabloları ve migration'ları idempotent yapar."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Bekleyen tweetler (Faz 1)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Bekleyen_Tweetler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT,
            published_date DATETIME,
            tweet_content TEXT,
            status TEXT DEFAULT 'Bekliyor',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            attempt_count INTEGER DEFAULT 0,
            content_hash TEXT,
            posted_tweet_id TEXT,
            posted_at DATETIME,
            share_type TEXT DEFAULT 'text',
            quote_url TEXT
        )
    ''')

    # Eksik kolon migration'ları (idempotent)
    for col_def in [
        ("attempt_count", "INTEGER DEFAULT 0"),
        ("content_hash", "TEXT"),
        ("posted_tweet_id", "TEXT"),
        ("posted_at", "DATETIME"),
        ("share_type", "TEXT DEFAULT 'text'"),
        ("quote_url", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE Bekleyen_Tweetler ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass

    # Hash UNIQUE INDEX
    try:
        cursor.execute("CREATE UNIQUE INDEX idx_content_hash ON Bekleyen_Tweetler(content_hash)")
    except sqlite3.OperationalError:
        pass

    # Engagement Metrics (Faz 2)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Engagement_Metrics (
            tweet_id INTEGER PRIMARY KEY,
            posted_tweet_id TEXT NOT NULL,
            posted_at DATETIME NOT NULL,
            likes_1h INTEGER, retweets_1h INTEGER, replies_1h INTEGER, views_1h INTEGER,
            likes_6h INTEGER, retweets_6h INTEGER, replies_6h INTEGER, views_6h INTEGER,
            likes_24h INTEGER, retweets_24h INTEGER, replies_24h INTEGER, views_24h INTEGER,
            last_checked DATETIME,
            FOREIGN KEY (tweet_id) REFERENCES Bekleyen_Tweetler(id)
        )
    ''')

    # Engagement aramaları için index
    try:
        cursor.execute("CREATE INDEX idx_engagement_posted_at ON Engagement_Metrics(posted_at)")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

# ============================================================
# Yazma — Bekleyen Tweetler
# ============================================================

def add_pending_tweet(title: str, link: str, published_date: str, tweet_content: str, share_type: str = 'text', quote_url: str = None) -> bool:
    h = make_hash(title)
    # Hot Fix 19: created_at'i Python'dan TR saati olarak gönder.
    # SQLite'ın DEFAULT CURRENT_TIMESTAMP UTC döner, biz local time istiyoruz.
    now_local = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO Bekleyen_Tweetler
                (title, link, published_date, tweet_content, status, content_hash, share_type, quote_url, created_at)
            VALUES (?, ?, ?, ?, 'Bekliyor', ?, ?, ?, ?)
        ''', (title, link, published_date, tweet_content, h, share_type, quote_url, now_local))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def hash_exists(title: str) -> bool:
    h = make_hash(title)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM Bekleyen_Tweetler WHERE content_hash = ? LIMIT 1", (h,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

# ============================================================
# Okuma — Bekleyen Tweetler
# ============================================================

def get_oldest_pending_tweet() -> Optional[Dict]:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM Bekleyen_Tweetler
        WHERE status = 'Bekliyor' AND attempt_count < ?
        ORDER BY created_at ASC
        LIMIT 1
    ''', (MAX_ATTEMPTS,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_recent_news_titles(hours: int = 12) -> List[str]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    time_threshold = datetime.datetime.now() - datetime.timedelta(hours=hours)
    cursor.execute('''
        SELECT title FROM Bekleyen_Tweetler
        WHERE created_at >= ?
    ''', (time_threshold.strftime('%Y-%m-%d %H:%M:%S'),))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_pending_count() -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM Bekleyen_Tweetler
        WHERE status = 'Bekliyor' AND attempt_count < ?
    ''', (MAX_ATTEMPTS,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ============================================================
# Status & Posted Tweet Tracking
# ============================================================

def update_tweet_status(tweet_id: int, new_status: str = 'Paylasildi'):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE Bekleyen_Tweetler SET status = ? WHERE id = ?', (new_status, tweet_id))
    conn.commit()
    conn.close()

def save_posted_tweet_id(tweet_id: int, posted_tweet_id: str):
    """
    Tweet başarıyla atıldıktan sonra Twitter'ın verdiği ID'yi ve atılma zamanını kaydeder.
    Aynı zamanda Engagement_Metrics tablosuna ilk kaydı yaratır (metric'ler null).
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_iso = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE Bekleyen_Tweetler
        SET status = 'Paylasildi', posted_tweet_id = ?, posted_at = ?
        WHERE id = ?
    ''', (posted_tweet_id, now_iso, tweet_id))
    cursor.execute('''
        INSERT OR IGNORE INTO Engagement_Metrics (tweet_id, posted_tweet_id, posted_at)
        VALUES (?, ?, ?)
    ''', (tweet_id, posted_tweet_id, now_iso))
    conn.commit()
    conn.close()

def increment_attempt(tweet_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE Bekleyen_Tweetler SET attempt_count = attempt_count + 1 WHERE id = ?', (tweet_id,))
    cursor.execute('SELECT attempt_count FROM Bekleyen_Tweetler WHERE id = ?', (tweet_id,))
    row = cursor.fetchone()
    if row and row[0] >= MAX_ATTEMPTS:
        cursor.execute("UPDATE Bekleyen_Tweetler SET status = 'Basarisiz' WHERE id = ?", (tweet_id,))
    conn.commit()
    conn.close()

# ============================================================
# Engagement Metrics
# ============================================================

def get_pending_engagement_checks() -> List[Dict]:
    """
    Hangi tweet'lerin hangi snapshot'larını ölçmemiz gerektiğini döner.
    
    Mantık: Her tweet için 4 snapshot zamanı var: 1h, 6h, 24h.
    Tweet atıldıktan sonra:
    - posted_at + 1h yaklaşmışsa ve likes_1h null ise → 1h snapshot ölç
    - posted_at + 6h yaklaşmışsa ve likes_6h null ise → 6h snapshot ölç
    - posted_at + 24h yaklaşmışsa ve likes_24h null ise → 24h snapshot ölç
    
    Snapshot zamanı geçmiş ama hâlâ null kalmışsa (bot offline'dı vb.) yine ölç.
    
    Tüm 3 snapshot dolduktan sonra bu tweet artık liste dışı.
    
    Returns: [{'tweet_id': X, 'posted_tweet_id': Y, 'snapshot': '1h'|'6h'|'24h'}, ...]
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    now = datetime.datetime.now()
    
    # Son 48 saat içinde paylaşılmış tweetleri getir (24h snapshot için marj)
    cutoff = (now - datetime.timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT tweet_id, posted_tweet_id, posted_at,
               likes_1h, likes_6h, likes_24h
        FROM Engagement_Metrics
        WHERE posted_at >= ?
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    
    pending = []
    for row in rows:
        try:
            posted_dt = datetime.datetime.strptime(row['posted_at'], '%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            continue
        age_minutes = (now - posted_dt).total_seconds() / 60
        
        # 1h snapshot: tweet en az 55 dakika eski + likes_1h hala null
        if age_minutes >= 55 and row['likes_1h'] is None:
            pending.append({
                'tweet_id': row['tweet_id'],
                'posted_tweet_id': row['posted_tweet_id'],
                'snapshot': '1h'
            })
        # 6h snapshot
        elif age_minutes >= 6 * 60 - 5 and row['likes_6h'] is None:
            pending.append({
                'tweet_id': row['tweet_id'],
                'posted_tweet_id': row['posted_tweet_id'],
                'snapshot': '6h'
            })
        # 24h snapshot
        elif age_minutes >= 24 * 60 - 5 and row['likes_24h'] is None:
            pending.append({
                'tweet_id': row['tweet_id'],
                'posted_tweet_id': row['posted_tweet_id'],
                'snapshot': '24h'
            })
    
    return pending

def update_engagement(tweet_id: int, snapshot: str, likes: int, retweets: int, replies: int, views: int):
    """Belirli bir snapshot için metric değerlerini günceller."""
    if snapshot not in ('1h', '6h', '24h'):
        raise ValueError(f"Geçersiz snapshot: {snapshot}")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_iso = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Dynamic column name'ler — snapshot suffix ile
    cursor.execute(f'''
        UPDATE Engagement_Metrics
        SET likes_{snapshot} = ?,
            retweets_{snapshot} = ?,
            replies_{snapshot} = ?,
            views_{snapshot} = ?,
            last_checked = ?
        WHERE tweet_id = ?
    ''', (likes, retweets, replies, views, now_iso, tweet_id))
    conn.commit()
    conn.close()

# ============================================================
# Kuyruk Bakımı (Hot Fix 16)
# ============================================================

def cleanup_stale_pending_tweets(max_age_minutes: int = 30) -> int:
    """
    Belirli yaştan eski 'Bekliyor' statusundaki tweet'leri 'Basarisiz' olarak işaretler.
    Yedek güvenlik: collector geç çalışırsa veya crash olursa devreye girer.

    Returns: işaretlenen tweet sayısı
    """
    cutoff = datetime.datetime.now() - datetime.timedelta(minutes=max_age_minutes)
    cutoff_iso = cutoff.strftime('%Y-%m-%d %H:%M:%S')

    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Bekleyen_Tweetler
            SET status = 'Basarisiz'
            WHERE status = 'Bekliyor'
            AND created_at < ?
        """, (cutoff_iso,))
        affected = cursor.rowcount
        conn.commit()
        return affected
    finally:
        conn.close()

def clear_all_pending_tweets() -> int:
    """
    Kuyruktaki TÜM 'Bekliyor' statusundaki tweet'leri 'Iptal' olarak işaretler.
    Collector başlangıcında çağrılır → her cycle taze başlar.

    Returns: temizlenen tweet sayısı
    """
    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Bekleyen_Tweetler
            SET status = 'Iptal'
            WHERE status = 'Bekliyor'
        """)
        affected = cursor.rowcount
        conn.commit()
        return affected
    finally:
        conn.close()


def _normalize_text_for_dedup(text: str) -> set:
    if not text:
        return set()
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r't\.co/\S+', '', text)
    text = text.lower()
    text = re.sub(r'[^\w\sçğıöşü]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return {w for w in text.split() if len(w) >= 3}


def is_duplicate_recent_tweet(new_text: str, hours: int = 2, threshold: float = 0.6) -> bool:
    """
    Son N saatte atılmış/kuyruktaki tweet'lerle Jaccard similarity hesaplar.
    threshold üstü benzerlik → mükerrer (True).
    """
    new_words = _normalize_text_for_dedup(new_text)
    if len(new_words) < 3:
        return False

    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tweet_content
            FROM Bekleyen_Tweetler
            WHERE status IN ('Paylasildi', 'Bekliyor')
            AND created_at >= datetime('now', ?)
        """, (f'-{hours} hours',))

        for (old_text,) in cursor.fetchall():
            old_words = _normalize_text_for_dedup(old_text)
            if len(old_words) < 3:
                continue
            union = len(new_words | old_words)
            if union == 0:
                continue
            if len(new_words & old_words) / union >= threshold:
                return True

        return False
    finally:
        conn.close()
