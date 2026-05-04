"""
Faz 1 — DB Migration ve Acil Kuyruk Temizliği
=============================================
Bunu SUNUCUDA bir kez çalıştır:
    python3 migrate_and_clean.py

Yaptıkları:
1. Bekleyen_Tweetler tablosuna 'attempt_count' ve 'content_hash' kolonlarını ekler.
2. content_hash için UNIQUE INDEX oluşturur (mükerrer engel).
3. Mevcut "Bekliyor" kuyruğundaki tüm kayıtları 'Iptal' yapar (insan onayı olmadan saçma içerikler atılmasın).
4. Mevcut kayıtların hash'lerini geriye dönük doldurur.
"""

import sqlite3
import hashlib
import re
import sys

DB_NAME = "sports_bot.db"

def normalize_for_hash(text: str) -> str:
    """Türkçe karakterleri sadeleştir, küçük harf yap, fazla boşluk temizle."""
    if not text:
        return ""
    t = text.lower()
    # Türkçe karakter normalize
    replacements = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
        "â": "a", "î": "i", "û": "u",
    }
    for k, v in replacements.items():
        t = t.replace(k, v)
    # Noktalama, sayı dışı işaretleri at
    t = re.sub(r"[^\w\s]", "", t)
    # Birden fazla boşluğu tek yap
    t = re.sub(r"\s+", " ", t).strip()
    return t

def make_hash(title: str) -> str:
    """Başlıktan deterministik bir hash üret."""
    norm = normalize_for_hash(title)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

def main():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. Yeni kolonları ekle (varsa hata vermez)
    print("[1/4] Yeni kolonları ekliyorum...")
    try:
        cursor.execute("ALTER TABLE Bekleyen_Tweetler ADD COLUMN attempt_count INTEGER DEFAULT 0")
        print("    ✓ attempt_count eklendi")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("    ⚠ attempt_count zaten var, atlanıyor")
        else:
            raise

    try:
        cursor.execute("ALTER TABLE Bekleyen_Tweetler ADD COLUMN content_hash TEXT")
        print("    ✓ content_hash eklendi")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("    ⚠ content_hash zaten var, atlanıyor")
        else:
            raise

    # 2. Mevcut kayıtların hash'lerini geriye doldur
    print("[2/4] Mevcut kayıtların hash'lerini hesaplıyorum...")
    cursor.execute("SELECT id, title FROM Bekleyen_Tweetler WHERE content_hash IS NULL")
    rows = cursor.fetchall()
    for r_id, r_title in rows:
        h = make_hash(r_title or "")
        cursor.execute("UPDATE Bekleyen_Tweetler SET content_hash = ? WHERE id = ?", (h, r_id))
    print(f"    ✓ {len(rows)} kaydın hash'i dolduruldu")

    # 2.5. Duplicate content_hash'leri temizle (en eski kaydı tut)
    print("[2.5/4] Duplicate hash'leri temizliyorum...")
    cursor.execute("""
        DELETE FROM Bekleyen_Tweetler
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM Bekleyen_Tweetler GROUP BY content_hash
        )
    """)
    removed = cursor.rowcount
    if removed > 0:
        print(f"    ✓ {removed} duplicate kayıt silindi (en eski tutuldu)")
    else:
        print("    ✓ Duplicate yok")

    # 3. UNIQUE INDEX oluştur (varsa atla)
    print("[3/4] content_hash için UNIQUE INDEX oluşturuyorum...")
    try:
        cursor.execute("CREATE UNIQUE INDEX idx_content_hash ON Bekleyen_Tweetler(content_hash)")
        print("    ✓ Index oluşturuldu")
    except sqlite3.OperationalError as e:
        if "already exists" in str(e).lower():
            print("    ⚠ Index zaten var, atlanıyor")
        else:
            raise

    # 4. Şüpheli kuyruğu Iptal'e çek
    print("[4/4] Mevcut 'Bekliyor' kuyruğunu güvenlik için 'Iptal'e çekiyorum...")
    cursor.execute("SELECT COUNT(*) FROM Bekleyen_Tweetler WHERE status = 'Bekliyor'")
    pending_count = cursor.fetchone()[0]
    if pending_count > 0:
        print(f"    {pending_count} bekleyen tweet bulundu.")
        if "--no-confirm" not in sys.argv:
            answer = input("    Hepsini iptal etmek için ENTER'a bas, korumak için 'n' yaz: ")
            if answer.strip().lower() == "n":
                print("    Kuyruk korundu (iptal yapılmadı).")
            else:
                cursor.execute("UPDATE Bekleyen_Tweetler SET status = 'Iptal' WHERE status = 'Bekliyor'")
                print(f"    ✓ {pending_count} tweet 'Iptal' yapıldı")
        else:
            cursor.execute("UPDATE Bekleyen_Tweetler SET status = 'Iptal' WHERE status = 'Bekliyor'")
            print(f"    ✓ {pending_count} tweet 'Iptal' yapıldı (no-confirm)")
    else:
        print("    Bekleyen tweet yok, atlanıyor.")

    conn.commit()
    conn.close()
    print("\n✅ Migration tamam. Bot artık yeni şemayla çalışabilir.")

if __name__ == "__main__":
    main()
