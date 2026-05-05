#!/bin/bash
#
# setup_gcs.sh — GCS bucket'ı oluşturur ve lifecycle kurallarını ayarlar
# ============================================================
# Tek seferlik kurulum scripti. Sunucuda bir kez çalıştırılır.

set -e

BUCKET_NAME="flasbot-hch7-backups"
REGION="us-central1"
PROJECT_ID="twitter-bot-495313"

echo "=== GCS Bucket Setup ==="
echo ""

# 1. Project doğrula
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ "$CURRENT_PROJECT" != "$PROJECT_ID" ]; then
    echo "Project ayarlanıyor: $PROJECT_ID"
    gcloud config set project "$PROJECT_ID"
fi

# 2. Bucket var mı kontrol et
if gsutil ls -b "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    echo "✓ Bucket zaten var: gs://${BUCKET_NAME}"
else
    echo "Bucket oluşturuluyor: gs://${BUCKET_NAME} ($REGION)"
    gsutil mb -l "$REGION" -c STANDARD "gs://${BUCKET_NAME}" || {
        echo "HATA: Bucket oluşturulamadı. İsim global unique olmayabilir."
        echo "Çözüm: backup_db.sh ve setup_gcs.sh içindeki BUCKET_NAME'i değiştir, tekrar dene."
        exit 1
    }
    echo "✓ Bucket oluşturuldu."
fi

# 3. Lifecycle rule (30 gün sonra otomatik sil)
LIFECYCLE_FILE=$(mktemp)
cat > "$LIFECYCLE_FILE" << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 30}
      }
    ]
  }
}
EOF

echo "Lifecycle rule kuruluyor (30 gün sonra otomatik silme)..."
gsutil lifecycle set "$LIFECYCLE_FILE" "gs://${BUCKET_NAME}"
rm -f "$LIFECYCLE_FILE"
echo "✓ Lifecycle rule aktif."

# 4. İzinler (default uniform bucket-level access yeterli, ama doğrula)
echo ""
echo "=== Bucket Hazır ==="
echo "Bucket: gs://${BUCKET_NAME}"
echo "Region: $REGION"
echo "Lifecycle: 30 gün sonra otomatik silme"
echo ""
echo "Test backup için: bash /home/hch7/tweetbot/backup_db.sh"
echo ""
