"""
Saatlik özet kartı oluşturma modülü.
HTML template + Playwright retina screenshot (1280x720 @ scale=2 → 2560x1440).
GCS public bucket'a yüklenir, public URL döndürülür (GetXAPI media_urls için).
"""

import io
import logging
import asyncio
import datetime as dt
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_CARDS_DIR = Path("/home/hch7/test_cards")
TEST_CARDS_DIR.mkdir(parents=True, exist_ok=True)

GCS_BUCKET = "flasbot-hch7-cards"
GCS_PUBLIC_URL_BASE = f"https://storage.googleapis.com/{GCS_BUCKET}"

# GetXAPI media_urls ile 5MB JPEG/PNG limit (Twitter native). Kalite öncelikli.
JPEG_QUALITY = 92
# subsampling=0 (4:4:4) renk bilgisini düşürmez — keskin metin kenarları için kritik.
# Varsayılan 4:2:0 metin etrafında "renkli halo" yaratır.
JPEG_SUBSAMPLING = 0

ACCENT_COLORS = ["#4a9eff", "#ff6b35", "#aaaaaa", "#ff4d94", "#ffd700", "#4a9eff"]

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&display=swap');

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    width: 1280px;
    height: 720px;
    background: #000000;
    color: #ffffff;
    font-family: 'Inter', sans-serif;
    padding: 32px 56px 32px 56px;
    position: relative;
    overflow: hidden;
  }}

  /* ======= HEADER ======= */
  .header {{
    position: relative;
    margin-bottom: 0;
  }}

  .header-label {{
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 4px;
    color: #808080;
    text-transform: uppercase;
    margin-bottom: 5px;
  }}

  .header-title {{
    font-size: 54px;
    font-weight: 900;
    letter-spacing: -2px;
    line-height: 1;
    color: #ffffff;
  }}

  .header-meta {{
    position: absolute;
    top: 0;
    right: 0;
    text-align: right;
  }}

  .header-date {{
    font-size: 22px;
    font-weight: 600;
    color: #909090;
    display: block;
    margin-bottom: 4px;
  }}

  .header-handle {{
    font-size: 20px;
    font-weight: 600;
    color: #3dcc7a;
  }}

  .divider {{
    width: 100%;
    height: 1px;
    background: #2a2a2a;
    margin: 16px 0 14px 0;
  }}

  /* ======= NEWS LIST ======= */
  .news-list {{
    display: flex;
    flex-direction: column;
    gap: 0;
  }}

  .news-item {{
    display: flex;
    align-items: flex-start;
    padding-bottom: 7px;
    margin-bottom: 7px;
    border-bottom: 1px solid #1f1f1f;
  }}

  .news-item:last-child {{
    border-bottom: none;
    margin-bottom: 0;
    padding-bottom: 0;
  }}

  .num-badge {{
    min-width: 30px;
    height: 30px;
    border-radius: 50%;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
    color: #909090;
    margin-top: 2px;
    flex-shrink: 0;
  }}

  .accent {{
    width: 28px;
    height: 3px;
    border-radius: 2px;
    flex-shrink: 0;
    margin: 12px 12px 0 10px;
  }}

  .text-block {{
    flex: 1;
    min-width: 0;
  }}

  .headline {{
    font-size: 23px;
    font-weight: 700;
    line-height: 1.25;
    color: #f0f0f0;
    margin-bottom: 2px;
    letter-spacing: -0.2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .desc {{
    font-size: 14px;
    font-weight: 400;
    color: #909090;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  /* ======= FOOTER ======= */
  .footer {{
    position: absolute;
    bottom: 16px;
    left: 56px;
    right: 56px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    font-weight: 500;
    color: #707070;
    border-top: 1px solid #1f1f1f;
    padding-top: 8px;
  }}

  .footer-handle {{
    color: #3dcc7a;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="header-meta">
      <span class="header-date">{date_label}</span>
      <span class="header-handle">@FlasFutbool</span>
    </div>
    <div class="header-label">SON BİR SAATTE FUTBOLDA NELER OLDU?</div>
    <div class="header-title">{time_label} FUTBOL GÜNDEMİ</div>
  </div>

  <div class="divider"></div>

  <div class="news-list">
    {news_items_html}
  </div>

  <div class="footer">
    <span class="footer-handle">@FlasFutbool</span>
    <span>Son 1 saatin futbol gündemi</span>
  </div>
</body>
</html>
"""


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_html(time_label: str, date_label: str, news_items: list) -> str:
    """news_items: list of {"headline": str, "desc": str}"""
    items_html = ""
    for idx, item in enumerate(news_items):
        color = ACCENT_COLORS[idx % len(ACCENT_COLORS)]
        headline = _html_escape(item.get("headline", ""))
        desc = _html_escape(item.get("desc", ""))
        desc_html = f'<div class="desc">{desc}</div>' if desc else ""
        items_html += f"""\
    <div class="news-item">
      <div class="num-badge">{idx + 1}</div>
      <div class="accent" style="background:{color}"></div>
      <div class="text-block">
        <div class="headline">{headline}</div>
        {desc_html}
      </div>
    </div>
"""
    return HTML_TEMPLATE.format(
        time_label=time_label,
        date_label=date_label,
        news_items_html=items_html,
    )


async def _render_to_image(html: str, output_path: Path) -> bool:
    """Retina render (2x scale) + yüksek kalite JPEG. Boyut limit yok (GCS host)."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=2,
            )
            await page.set_content(html, wait_until="networkidle")
            await page.wait_for_timeout(600)
            png_bytes = await page.screenshot(full_page=False, type="png")
            await browser.close()

        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        buf = io.BytesIO()
        img.save(
            buf,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
            subsampling=JPEG_SUBSAMPLING,
        )
        data = buf.getvalue()
        output_path.write_bytes(data)
        logger.info(f"JPEG render: {img.size[0]}x{img.size[1]} q={JPEG_QUALITY} sub=4:4:4 size={len(data)} bytes")
        return True
    except Exception as e:
        logger.error(f"JPEG render hatası: {e}")
        return False


def _upload_to_gcs(local_path: Path) -> str:
    """JPEG'i public GCS bucket'a yükle, public URL döndür. Hata olursa None."""
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        object_name = local_path.name  # örn. summary_20260513_204500.jpg
        blob = bucket.blob(object_name)
        blob.upload_from_filename(str(local_path), content_type="image/jpeg")
        url = f"{GCS_PUBLIC_URL_BASE}/{object_name}"
        logger.info(f"✓ GCS upload: {url}")
        return url
    except Exception as e:
        logger.error(f"GCS upload hatası: {e}")
        return None


def generate_summary_card(news_items: list, time_range: str, save_path: Path = None) -> tuple:
    """
    Saatlik özet kartı üret + GCS'e yükle.

    Args:
        news_items: 3-8 arası {"headline": str, "desc": str} dict listesi
        time_range: "20:00 - 21:00" gibi string
        save_path: JPG kayıt yolu (test mode için)

    Returns:
        (success: bool, jpg_path: Path or None, public_url: str or None)
    """
    if not news_items or len(news_items) < 3:
        logger.warning(f"Yetersiz haber ({len(news_items)}), kart üretilmedi")
        return (False, None, None)

    if len(news_items) > 8:
        news_items = news_items[:8]

    now = dt.datetime.now()
    date_label = now.strftime("%-d %b")

    # SaatlikGundem mantığı: "12:00 - 13:00" aralığı için header "13:00 GÜNDEM"
    time_label = time_range.split(" - ")[-1] if " - " in time_range else time_range

    html = _build_html(time_label, date_label, news_items)

    if save_path is None:
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        save_path = TEST_CARDS_DIR / f"summary_{timestamp}.jpg"

    success = asyncio.run(_render_to_image(html, save_path))

    if not success:
        return (False, None, None)

    public_url = _upload_to_gcs(save_path)
    if not public_url:
        return (True, save_path, None)

    logger.info(f"✓ Kart hazır: {save_path} → {public_url}")
    return (True, save_path, public_url)
