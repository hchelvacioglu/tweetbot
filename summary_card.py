"""
Saatlik özet kartı oluşturma modülü.
HTML template + Playwright ile JPEG screenshot.
1280x720 + Pillow ile adaptive quality (GetXAPI ~100KB base64 limitine sığar).
"""

import base64
import io
import logging
import asyncio
import datetime as dt
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_CARDS_DIR = Path("/home/hch7/test_cards")
TEST_CARDS_DIR.mkdir(parents=True, exist_ok=True)

# GetXAPI ~100KB base64 limit. 95KB binary ≈ 127KB base64, üstünde.
# Güvenli hedef: 70KB binary ≈ 94KB base64.
TARGET_BINARY_BYTES = 70_000

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
    background: linear-gradient(160deg, #0d1117 0%, #0f1f16 100%);
    color: #ffffff;
    font-family: 'Inter', sans-serif;
    padding: 32px 56px 32px 56px;
    position: relative;
    overflow: hidden;
  }}

  /* sağ üst yeşil parıltı */
  body::before {{
    content: '';
    position: absolute;
    top: -100px;
    right: -100px;
    width: 500px;
    height: 500px;
    background: radial-gradient(circle, rgba(0,200,100,0.12) 0%, transparent 68%);
    pointer-events: none;
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
    color: #5a6a5a;
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
    color: #6a7a6a;
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
    background: #2a3a2a;
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
    border-bottom: 1px solid #1a2a1a;
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
    background: #1a2a1a;
    border: 1px solid #2a3a2a;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
    color: #5a6a5a;
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
    color: #5a6a5a;
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
    color: #3a4a3a;
    border-top: 1px solid #1e2e1e;
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
    """Önce PNG olarak render et — Pillow ile adaptive JPEG'e çevireceğiz."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            await page.set_content(html, wait_until="networkidle")
            await page.wait_for_timeout(600)
            # Önce kayıpsız PNG buffer'a al
            png_bytes = await page.screenshot(full_page=False, type="png")
            await browser.close()

        # Pillow ile adaptive JPEG sıkıştırma: en yüksek quality ile hedef boyuta in
        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")

        # Binary search: hedef ≤ TARGET_BINARY_BYTES, q ∈ [30, 92]
        lo, hi = 30, 92
        best_q, best_bytes = 30, None
        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=mid, optimize=True, progressive=True)
            size = buf.tell()
            if size <= TARGET_BINARY_BYTES:
                best_q, best_bytes = mid, buf.getvalue()
                lo = mid + 1  # daha yüksek q'yu dene
            else:
                hi = mid - 1

        if best_bytes is None:
            # Hedefe sığmadı, en düşük q ile zorla yaz
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=30, optimize=True, progressive=True)
            best_bytes = buf.getvalue()
            best_q = 30
            logger.warning(f"Adaptive JPEG hedefe sığmadı, q=30 forced (size={len(best_bytes)})")

        output_path.write_bytes(best_bytes)
        logger.info(f"Adaptive JPEG: q={best_q}, binary={len(best_bytes)} bytes")
        return True
    except Exception as e:
        logger.error(f"JPEG render hatası: {e}")
        return False


def generate_summary_card(news_items: list, time_range: str, save_path: Path = None) -> tuple:
    """
    Saatlik özet kartı üret. Landscape 1600x900.

    Args:
        news_items: 3-6 arası {"headline": str, "desc": str} dict listesi
        time_range: "20:00 - 21:00" gibi string
        save_path: PNG kayıt yolu (test mode için)

    Returns:
        (success: bool, png_path: Path or None, base64_data: str or None)
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

    try:
        with open(save_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Base64 encode hatası: {e}")
        return (True, save_path, None)

    logger.info(f"✓ Kart üretildi: {save_path} ({len(b64_data)} char base64)")
    return (True, save_path, b64_data)
