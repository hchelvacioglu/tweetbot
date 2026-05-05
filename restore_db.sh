#!/bin/bash
#
# restore_db.sh — Yedekten DB geri yükleme
# ============================================================
# KULLANIM:
#   bash restore_db.sh                  # En son yedeği listeler
#   bash restore_db.sh 2026-05-10       # Belirtilen tarihteki yedeği geri yükler

set -e

BUCKET_NAME="flasbot-hch7-backups"
DB_PATH="/home/hch7/tweetbot/sports_bot.db"

if [ -z "$1" ]; then
    echo "Mevcut yedekler:"
    gsutil ls "gs://${BUCKET_NAME}/" | sort
    echo ""
    echo "Kullanım: bash restore_db.sh YYYY-MM-DD"
    echo "Örnek:    bash restore_db.sh 2026-05-10"
    exit 0
fi

DATE="$1"
BACKUP_NAME="sports_bot_${DATE}.db"
GCS_PATH="gs://${BUCKET_NAME}/${BACKUP_NAME}"

# Yedek var mı?
if ! gsutil ls "$GCS_PATH" >/dev/null 2>&1; then
    echo "HATA: Yedek bulunamadı: $GCS_PATH"
    exit 1
fi

echo "=== DB Geri Yükleme ==="
echo "Yedek: $GCS_PATH"
echo "Hedef: $DB_PATH"
echo ""
echo "DİKKAT: Mevcut DB üzerine yazılacak."
read -p "Devam etmek için 'evet' yazın: " confirm

if [ "$confirm" != "evet" ]; then
    echo "İptal edildi."
    exit 0
fi

# Bot'u durdur
echo "Bot durduruluyor..."
sudo systemctl stop flasbot

# Mevcut DB'yi yedekle (paranoya)
if [ -f "$DB_PATH" ]; then
    SAFETY_BACKUP="${DB_PATH}.before_restore_$(date +%s)"
    cp "$DB_PATH" "$SAFETY_BACKUP"
    echo "Mevcut DB güvenlik yedeği: $SAFETY_BACKUP"
fi

# Yedeği indir
echo "Yedek indiriliyor..."
gsutil cp "$GCS_PATH" "$DB_PATH"

# İzinleri düzelt
chown hch7:hch7 "$DB_PATH"

# Bot'u başlat
echo "Bot başlatılıyor..."
sudo systemctl start flasbot

echo ""
echo "✓ Geri yükleme tamam."
echo "Servis durumu için: sudo systemctl status flasbot"
