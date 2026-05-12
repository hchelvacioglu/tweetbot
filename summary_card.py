"""
Saatlik özet kartı oluşturma modülü.
HTML template + Playwright ile PNG screenshot.
"""

import os
import base64
import logging
import asyncio
import datetime as dt
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_CARDS_DIR = Path("/home/hch7/test_cards")
TEST_CARDS_DIR.mkdir(parents=True, exist_ok=True)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    width: 1200px;
    height: 1600px;
    background: #0a0a0a;
    color: #ffffff;
    font-family: 'Inter', sans-serif;
    padding: 80px;
    position: relative;
    overflow: hidden;
  }

  .header {
    border-bottom: 4px solid #00ff88;
    padding-bottom: 40px;
    margin-bottom: 60px;
  }

  .title {
    font-size: 72px;
    font-weight: 900;
    letter-spacing: -2px;
    line-height: 1;
    margin-bottom: 16px;
  }

  .time {
    font-size: 36px;
    color: #888;
    font-weight: 600;
  }

  .news-list {
    display: flex;
    flex-direction: column;
    gap: 40px;
  }

  .news-item {
    display: flex;
    align-items: flex-start;
    gap: 30px;
  }

  .number {
    font-size: 56px;
    font-weight: 900;
    color: #00ff88;
    min-width: 80px;
    line-height: 1;
  }

  .text {
    font-size: 34px;
    font-weight: 600;
    line-height: 1.3;
    color: #fff;
    flex: 1;
  }

  .footer {
    position: absolute;
    bottom: 80px;
    left: 80px;
    right: 80px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #555;
    font-size: 28px;
    font-weight: 600;
    border-top: 2px solid #222;
    padding-top: 30px;
  }

  .handle {
    color: #00ff88;
  }
</style>
</head>
<body>
  <div class="header">
    <div class="title">SAATLİK ÖZET</div>
    <div class="time">__TIME_RANGE__</div>
  </div>

  <div class="news-list">
    __NEWS_ITEMS__
  </div>

  <div class="footer">
    <span class="handle">@FlasFutbool</span>
    <span>Türk Futbol Gündemi</span>
  </div>
</body>
</html>
"""


def _build_html(time_range: str, news_items: list) -> str:
    items_html = ""
    for idx, text in enumerate(news_items, 1):
        text_safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        items_html += f"""
        <div class="news-item">
            <div class="number">{idx}.</div>
            <div class="text">{text_safe}</div>
        </div>
        """
    html = HTML_TEMPLATE.replace("__TIME_RANGE__", time_range)
    html = html.replace("__NEWS_ITEMS__", items_html)
    return html


async def _render_to_png(html: str, output_path: Path) -> bool:
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 1600})
            await page.set_content(html, wait_until="networkidle")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(output_path), full_page=False, omit_background=False)
            await browser.close()

        return True
    except Exception as e:
        logger.error(f"PNG render hatası: {e}")
        return False


def generate_summary_card(news_items: list, time_range: str, save_path: Path = None) -> tuple:
    """
    Saatlik özet kartı üret.

    Returns:
        (success: bool, png_path: Path or None, base64_data: str or None)
    """
    if not news_items or len(news_items) < 3:
        logger.warning(f"Yetersiz haber ({len(news_items)}), kart üretilmedi")
        return (False, None, None)

    if len(news_items) > 7:
        news_items = news_items[:7]

    html = _build_html(time_range, news_items)

    if save_path is None:
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = TEST_CARDS_DIR / f"summary_{timestamp}.png"

    success = asyncio.run(_render_to_png(html, save_path))

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
