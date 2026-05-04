"""
database.py — Faz 1 Güncellemesi
================================
Yeni özellikler:
- attempt_count: bir tweet kaç kez post edilmeye çalışıldığını sayar
- content_hash: başlık bazlı deterministik hash, mükerrer engel
- 'Basarisiz' status: 3 kez fail eden tweetler artık sonsuza kadar denenmez
- Hash bazlı duplicate kontrolü: AI'ya değil, DB'ye soruyor (hız + maliyet)
"""

import sqlite3
import datetime
import hashlib
import re
from typing import List, Dict, Optional

DB_NAME = "sports_bot.db"
MAX_ATTEMPTS = 3  # Bu kadar fail ederse 'Basarisiz' olur

# ============================================================
# Hash Helpers
# ============================================================

def normalize_for_hash(text: str) -> str:
    """Türkçe karakterleri sadeleştir, küçük harf yap, fazla boşluk temizle."""
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
    """Başlıktan deterministik bir hash üret (16 karakter)."""
    norm = normalize_for_hash(title)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

# ============================================================
# Schema
# ============================================================

def init_db():
    """DB'yi başlatır. Tablo yoksa oluşturur, varsa migration yapar."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

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
            content_hash TEXT
        )
    ''')

    # Mevcut tablolar için ALTER (idempotent)
    for col_def in [
        ("attempt_count", "INTEGER DEFAULT 0"),
        ("content_hash", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE Bekleyen_Tweetler ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass  # zaten var

    # Hash için UNIQUE INDEX
    try:
        cursor.execute("CREATE UNIQUE INDEX idx_content_hash ON Bekleyen_Tweetler(content_hash)")
    except sqlite3.OperationalError:
        pass  # zaten var

    conn.commit()
    conn.close()

# ============================================================
# Yazma
# ============================================================

def add_pending_tweet(title: str, link: str, published_date: str, tweet_content: str) -> bool:
    """
    Yeni tweet ekler. Hash zaten varsa False döner (duplicate engel).
    Başarılıysa True döner.
    """
    h = make_hash(title)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO Bekleyen_Tweetler 
                (title, link, published_date, tweet_content, status, content_hash)
            VALUES (?, ?, ?, ?, 'Bekliyor', ?)
        ''', (title, link, published_date, tweet_content, h))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # UNIQUE INDEX ihlali = mükerrer
        return False
    finally:
        conn.close()

def hash_exists(title: str) -> bool:
    """Bu başlığa ait hash DB'de var mı? (eklemeden önce kontrol için)"""
    h = make_hash(title)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM Bekleyen_Tweetler WHERE content_hash = ? LIMIT 1", (h,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

# ============================================================
# Okuma
# ============================================================

def get_oldest_pending_tweet() -> Optional[Dict]:
    """En eski bekleyen tweet'i getirir (Basarisiz olanları atlar)."""
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
    """Son X saatte işlenmiş tüm başlıkları getirir (status fark etmeksizin)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    time_threshold = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    cursor.execute('''
        SELECT title FROM Bekleyen_Tweetler
        WHERE created_at >= ?
    ''', (time_threshold.strftime('%Y-%m-%d %H:%M:%S'),))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_pending_count() -> int:
    """Şu an aktif bekleyen tweet sayısı."""
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
# Status Güncelleme
# ============================================================

def update_tweet_status(tweet_id: int, new_status: str = 'Paylasildi'):
    """Tweet'in statusunu günceller."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE Bekleyen_Tweetler SET status = ? WHERE id = ?', (new_status, tweet_id))
    conn.commit()
    conn.close()

def increment_attempt(tweet_id: int):
    """
    Bir post denemesi başarısız olunca attempt_count'u 1 artır.
    MAX_ATTEMPTS'e ulaşırsa status'u otomatik 'Basarisiz' yapar.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE Bekleyen_Tweetler SET attempt_count = attempt_count + 1 WHERE id = ?', (tweet_id,))
    cursor.execute('SELECT attempt_count FROM Bekleyen_Tweetler WHERE id = ?', (tweet_id,))
    row = cursor.fetchone()
    if row and row[0] >= MAX_ATTEMPTS:
        cursor.execute("UPDATE Bekleyen_Tweetler SET status = 'Basarisiz' WHERE id = ?", (tweet_id,))
    conn.commit()
    conn.close()
