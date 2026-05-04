"""
ai_manager.py — Faz 1 Güncellemesi
==================================
Düzeltmeler:
- 429 (token limit) hatasında HEMEN çıkar, sonsuza kadar retry yapmaz
- AI_PROVIDER flag'i: 'groq' veya 'gemini' (.env'den okur, default 'groq')
- Content safety: küfür/hakaret/ırkçılık ATLA
- Sıkılaştırılmış prompt: 4 büyükler dışı kesinlikle paylaşılmaz
- Yerel haber tuzaklarına özel uyarı (Haber61 vb. trabzon yerel haberleri)
- Kumar/promosyon/reklam içerikleri ATLA
- Voleybol, tenis, basket, milli takım dışındaki spor dallarına ATLA
"""

import os
import re
import time
import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# ============================================================
# Provider seçimi
# ============================================================
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").lower()

if AI_PROVIDER == "gemini":
    from google import genai as genai_new
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    _gemini_client = genai_new.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    logger.info(f"AI provider: Gemini ({GEMINI_MODEL})")
else:
    from groq import Groq
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    _groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    logger.info(f"AI provider: Groq ({GROQ_MODEL})")

# Custom exception — token limit aşıldığında main.py yakalayıp graceful skip yapsın
class AIQuotaExceeded(Exception):
    pass

# ============================================================
# LLM Çağrısı
# ============================================================

def _call_groq(prompt: str) -> str:
    """Groq çağrısı. 429 görürse direkt AIQuotaExceeded fırlatır."""
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY missing")
    backoff = [3, 8, 20]  # 3 deneme, sonra çıkar
    last_err = None
    for attempt, delay in enumerate(backoff):
        try:
            response = _groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=GROQ_MODEL,
                temperature=0.7,
                max_tokens=1500,  # batch için 500 yetmeyebilir
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            err_str = str(e)
            last_err = e
            # Token limit / quota hatasıysa retry yapma — limit zaten dolmuş
            if "429" in err_str or "rate_limit" in err_str.lower() or "quota" in err_str.lower():
                logger.error(f"Groq quota exceeded (no retry): {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            logger.warning(f"Groq error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)
    raise last_err

def _call_gemini(prompt: str) -> str:
    """Gemini çağrısı (yeni google-genai SDK). 429 görürse AIQuotaExceeded fırlatır."""
    if not _gemini_client:
        raise RuntimeError("GEMINI_API_KEY missing")
    backoff = [3, 8, 20]
    last_err = None
    for attempt, delay in enumerate(backoff):
        try:
            from google.genai import types as genai_types
            response = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=4000,
                )
            )
            text = (response.text or "").strip()
            logger.info(f"Gemini raw: len={len(text)}, has_close={']' in text}")
            return text
        except Exception as e:
            err_str = str(e)
            last_err = e
            if "429" in err_str or "quota" in err_str.lower() or "ResourceExhausted" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                logger.error(f"Gemini quota exceeded (no retry): {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            logger.warning(f"Gemini error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)
    raise last_err

def _call_llm(prompt: str) -> str:
    if AI_PROVIDER == "gemini":
        return _call_gemini(prompt)
    return _call_groq(prompt)

# ============================================================
# Batch İşleme
# ============================================================

PROMPT_TEMPLATE = """Sen @FlasFutbool adlı X hesabının profesyonel editörüsün. SADECE Türkiye'nin 4 BÜYÜK kulübüyle ilgili FUTBOL haberlerini paylaşırsın: Galatasaray (GS), Fenerbahçe (FB), Beşiktaş (BJK), Trabzonspor (TS).

==================== KESİN ATLA KURALLARI ====================
Şunlardan HİÇBİRİNİ paylaşma, hepsini "ATLA" olarak işaretle:

1) FUTBOL DIŞI SPORLAR: Voleybol, basketbol, tenis, atletizm, golf, motor sporları, dövüş sporları, e-spor, vb.
   Örnek atlanması gerekenler: "VakıfBank", "Zehra Güneş", "Anadolu Efes", "Fenerbahçe Beko" (basket), "Galatasaray Kadın Voleybol".

2) 4 BÜYÜKLER DIŞI KULÜPLER: Samsunspor, Kasımpaşa, Konyaspor, Eyüpspor, Karagümrük, Gaziantep, vb. tek başına haber konusu olamaz.
   İSTİSNA: Bu kulüplerden biri 4 büyüklerle TRANSFER veya TRANSFER DEDİKODUSU bağlamında geçerse PAYLAŞ.

3) MİLLİ TAKIM HABERLERİ: A Milli Takım, Genç Milli Takım haberleri tek başına ATLA.

4) FUTBOL DIŞI HİÇBİR ŞEY: Trafik kazası, ölüm haberi, siyaset, ekonomi, tarım (fındık), sağlık (hastane, diş), reklam, iş ilanı, kumar/bahis tahmini ("xx oranla yy TL kazandı"), şehir haberleri, hava durumu.

5) İÇERİK GÜVENLİĞİ: Küfür, hakaret, ırkçı/cinsiyetçi/ayrımcı söylem, kişiyi hedef alan saldırgan dil içeren tweetler ATLA.

6) İÇERİĞİ BOŞ HABERLER: "Yıldız isim", "O futbolcu", "Bomba transfer", "Perde arkası belli oldu" gibi somut bilgi (isim/olay) içermeyen clickbait'ler ATLA. İSTİSNA: Detay metninde ismi varsa, tweet'e o ismi YAZARAK paylaş.

==================== PAYLAŞ KURALLARI ====================
Bir haberi PAYLAŞ olarak işaretlersen:
- Tweet 280 karakteri AŞMASIN
- Sonuna parantez içinde kaynak ekle. Öncelik sırası: (1) tweet metninde zaten parantez içinde bir kaynak varsa onu kullan, (2) yoksa Kaynak alanındaki hesabı kullan, (3) ikisi de yoksa parantez ekleme. Asla iki ayrı parantez yazma.
- Tweet metni TÜRKÇE olmalı, asıl haberin özünü versin
- "bomba", "saldırı", "şok" gibi spor terimleri serbesttir, sansürleme
- Emoji kullanma (zaten kaynak hesaplar yeterince kullanmış oluyor)

==================== MÜKERRER KONTROLÜ ====================
Aşağıdaki haberler son 12 saatte zaten işlendi. Listedeki bir haberle AYNI KONUYU işleyen yeni haberi ATLA:
{recent_titles}

==================== HABER LİSTESİ ====================
{news_formatted}

==================== ÇIKTI FORMATI ====================
SADECE aşağıdaki JSON array'ı döndür, başka hiçbir şey yazma. Açıklama, markdown, ön söz, son söz hiçbir şey yok:
[
  {{"id": 0, "decision": "PAYLAS", "tweet": "..."}},
  {{"id": 1, "decision": "ATLA"}},
  ...
]"""


def process_news_batch(news_items: List[Dict], recent_titles: List[str]) -> List[Dict]:
    """
    Haberleri toplu işler. AIQuotaExceeded fırlatabilir — main.py yakalamalı.
    """
    if not news_items:
        return []

    titles_context = "\n".join(f"- {t}" for t in recent_titles[:15]) if recent_titles else "(henüz hiçbiri)"
    
    news_formatted = ""
    for i, item in enumerate(news_items):
        news_formatted += (
            f"\n--- HABER ID: {i} ---\n"
            f"Başlık: {item['title']}\n"
            f"Detay: {item.get('description', '')}\n"
            f"Kaynak: {item['source']}\n"
        )

    prompt = PROMPT_TEMPLATE.format(
        recent_titles=titles_context,
        news_formatted=news_formatted
    )

    try:
        response_text = _call_llm(prompt)
    except AIQuotaExceeded:
        # Yukarı fırlat — main.py tüm collector'ı durdurur
        raise
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        return []

    # Markdown code block'larını temizle (```json, ```JSON, ``` vb.)
    clean_text = re.sub(r'^```[a-zA-Z]*\n?', '', response_text.strip())
    clean_text = re.sub(r'\n?```\s*$', '', clean_text)
    # BOM ve invisible Unicode karakterleri temizle
    clean_text = clean_text.lstrip('﻿​‌‍⁠').strip()

    results = None
    # 1. Direkt parse
    try:
        results = json.loads(clean_text)
    except json.JSONDecodeError:
        pass

    # 2. Greedy regex ile tam array bul
    if results is None:
        m = re.search(r'\[.+\]', clean_text, re.DOTALL)
        if m:
            try:
                results = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    # 3. Partial parse: truncated response'dan tamamlanmış item'ları topla
    if results is None:
        objects = re.findall(r'\{[^{}]*"decision"\s*:\s*"[^"]*"[^{}]*\}', clean_text)
        if objects:
            try:
                results = json.loads('[' + ','.join(objects) + ']')
                logger.warning(f"Partial parse: {len(results)} item (yanıt kesilmiş)")
            except json.JSONDecodeError:
                pass

    if results is None:
        logger.error(f"JSON parse tamamen başarısız. repr: {repr(clean_text[:600])}")
        return []

    processed = []
    for res in results:
        idx = res.get("id")
        decision = (res.get("decision") or "").upper()
        if idx is None or idx >= len(news_items):
            continue
        if decision in ("PAYLAS", "PAYLAŞ"):
            tweet_text = res.get("tweet", "").strip()
            if not tweet_text:
                continue
            item = news_items[idx]
            processed.append({
                "title": item['title'],
                "tweet": tweet_text,
                "link": item['link'],
                "published_date": item.get('published_date', time.strftime('%Y-%m-%d %H:%M:%S'))
            })
    
    logger.info(f"AI batch: {len(news_items)} işlendi, {len(processed)} paylaşılacak.")
    return processed
