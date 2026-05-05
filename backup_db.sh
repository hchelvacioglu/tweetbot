#!/bin/bash
#
# backup_db.sh — SQLite veritabanını GCS bucket'a yedekler
# ============================================================
# Cron tarafından her gün 04:00 UTC'de tetiklenir.
# Bot zaten gece modunda (03-07 arası) olduğu için çakışma yok.
#
# Yedek formatı: sports_bot_YYYY-MM-DD.db
# Bucket: flasbot-hch7-backups
# Lifecycle: 30 gün sonra otomatik silinir (bucket-level rule)

set -e

# Konfigürasyon
DB_PATH="/home/hch7/tweetbot/sports_bot.db"
BUCKET_NAME="flasbot-hch7-backups"
DATE=$(date -u +"%Y-%m-%d")
BACKUP_NAME="sports_bot_${DATE}.db"
LOCAL_TMP="/tmp/${BACKUP_NAME}"

# Log dosyası
LOG_FILE="/home/hch7/tweetbot/backup.log"

log() {
    echo "[$(date -u +'%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOG_FILE"
}

log "=== Backup başlıyor ==="

# DB var mı kontrol et
if [ ! -f "$DB_PATH" ]; then
    log "HATA: DB dosyası bulunamadı: $DB_PATH"
    exit 1
fi

# SQLite tutarlı backup (.backup komutu — DB lock olsa da çalışır)
sqlite3 "$DB_PATH" ".backup '$LOCAL_TMP'" || {
    log "HATA: sqlite3 backup başarısız."
    exit 2
}

DB_SIZE=$(stat -c%s "$LOCAL_TMP")
log "Lokal yedek alındı: $LOCAL_TMP ($DB_SIZE bytes)"

# GCS'ye yükle
gsutil -q cp "$LOCAL_TMP" "gs://${BUCKET_NAME}/${BACKUP_NAME}" || {
    log "HATA: GCS upload başarısız."
    rm -f "$LOCAL_TMP"
    exit 3
}

log "✓ GCS'e yüklendi: gs://${BUCKET_NAME}/${BACKUP_NAME}"

# Lokal tmp dosyayı sil
rm -f "$LOCAL_TMP"

log "=== Backup tamam ==="
exit 0
