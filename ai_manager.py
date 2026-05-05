"""
ai_manager.py — Faz 2 Güncellemesi
==================================
Faz 1'e ek olarak:
- max_output_tokens 4000 → 8192 (Gemini truncation'ı çözmek için)
- Yeni: response.usage_metadata loglanıyor (gerçek token sayısı görülsün)
- Yeni: response.candidates[0].finish_reason kontrolü (MAX_TOKENS uyarısı görsel)
- A filtre: dedikodu, başkan adayı, hakem haberleri geçer; sadece futbol-dışı + clickbait + yerel haber elenir
- Söz aktarımı koruması: "İsim: 'söz'" yapısı bozulmaz
- Prompt biraz daha kısa ve net (token tasarrufu için)
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
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

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

class AIQuotaExceeded(Exception):
    pass

# ============================================================
# LLM Çağrıları
# ============================================================

def _call_groq(prompt: str) -> str:
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY missing")
    backoff = [3, 8, 20]
    last_err = None
    for attempt, delay in enumerate(backoff):
        try:
            response = _groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=GROQ_MODEL,
                temperature=0.7,
                max_tokens=4000,
            )
            text = (response.choices[0].message.content or "").strip()
            logger.info(f"Groq raw: len={len(text)}")
            return text
        except Exception as e:
            err_str = str(e)
            last_err = e
            if "429" in err_str or "rate_limit" in err_str.lower() or "quota" in err_str.lower():
                logger.error(f"Groq quota exceeded (no retry): {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            logger.warning(f"Groq error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)
    raise last_err

def _call_gemini(prompt: str) -> str:
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
                    max_output_tokens=8192,  # Faz 2: 4000 → 8192
                )
            )
            text = (response.text or "").strip()
            
            # Diagnostik: gerçek token sayısı + finish_reason
            usage_info = ""
            try:
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    um = response.usage_metadata
                    usage_info = f" tokens(in={um.prompt_token_count}, out={um.candidates_token_count})"
            except Exception:
                pass
            
            finish_reason = ""
            try:
                if response.candidates and len(response.candidates) > 0:
                    fr = response.candidates[0].finish_reason
                    finish_reason = f" finish={fr}"
                    # MAX_TOKENS uyarısı
                    if str(fr) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS", "2"):
                        logger.warning(f"Gemini MAX_TOKENS sınırına ulaştı! Yanıt kesilmiş olabilir.")
            except Exception:
                pass
            
            logger.info(f"Gemini raw: len={len(text)}{usage_info}{finish_reason}")
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
# Prompt — Faz 2 (A filtre + söz aktarımı koruması)
# ============================================================

PROMPT_TEMPLATE = """Sen @FlasFutbool adlı X hesabının editörüsün. Türkiye'nin 4 BÜYÜK kulübüyle ilgili haberleri paylaşırsın: Galatasaray (GS), Fenerbahçe (FB), Beşiktaş (BJK), Trabzonspor (TS).

==================== ATLA KURALLARI ====================
ATLA olarak işaretle:

1) FUTBOL DIŞI SPORLAR: Voleybol, basketbol, tenis, atletizm, golf, motor sporları, dövüş sporları, e-spor.
   Örnek atlanması gerekenler: "VakıfBank", "Zehra Güneş", "Anadolu Efes", "Fenerbahçe Beko" (basket), "Galatasaray Kadın Voleybol".

2) FUTBOL DIŞI HİÇBİR ŞEY: Trafik kazası, ölüm haberi (futbolcu hariç), siyaset, ekonomi, tarım, sağlık reklamı, iş ilanı, kumar/bahis tahmini, şehir haberleri, hava durumu.

3) 4 BÜYÜKLER İLE BAĞLANTISIZ KULÜPLER: Samsunspor, Konyaspor vb. tek başına haber konusu olmaz. AMA 4 büyüklerden biriyle transfer/maç/karşılaşma bağlantısı varsa PAYLAŞ.

4) İÇERİK GÜVENLİĞİ: Küfür, hakaret, ırkçı/cinsiyetçi/ayrımcı söylem, kişiyi hedef alan saldırgan dil ATLA.

5) İÇERİĞİ BOŞ CLICKBAIT: "Yıldız isim", "O futbolcu", "Bomba isim" gibi somut bilgi (isim/olay) içermeyen başlıklar ATLA.
   İSTİSNA: Detay metninde net isim varsa, tweet'e o ismi koyarak PAYLAŞ.

==================== PAYLAŞ KURALLARI (GENİŞ FİLTRE) ====================
ŞUNLARIN HEPSİ PAYLAŞILABILIR (4 büyüklerle ilgili olmak şartıyla):
- Transfer ve transfer dedikoduları
- Maç sonuçları, kadro, taktik, sakatlık, ceza
- Yönetim, başkan adaylığı, kongre, mali tablolar
- Hakem atamaları, VAR kararları
- Antrenör (teknik direktör) haberleri, ayrılık/yeni geliş
- Futbolcu özel hayatı / sosyal medya çıkışları (eğer haber değeri varsa)
- "🚨 ÖZEL", "FLAŞ" gibi haberler — somut bilgi varsa paylaş, içeriği boşsa atla
- Milli takım haberleri 4 büyüklerden bir oyuncuyu içeriyorsa PAYLAŞ

==================== TWEET YAZIM KURALLARI ====================
ÇOK ÖNEMLİ — SÖZ AKTARIMLARINI DEĞİŞTİRME:
- Eğer haberde birinin sözü tırnak içinde geçiyorsa ("Sercan Hamzaoğlu: 'falan filan'"), bu sözü AYNEN koru.
- Söylenmemiş söz ekleme, söylenen sözü değiştirme.
- Kendi yorum cümleni eklemek YASAK.

TWEET FORMATI:
- 280 karakteri AŞMA. Çok uzun haberlerde özün korunması için kısaltma yapabilirsin AMA tırnak içi sözleri değiştirme.
- Sonunda parantez içinde KAYNAK ekle:
  * Eğer haber metninde zaten parantezli kaynak varsa (örn. "(Hürriyet)" veya "(Sabah)") onu koru, ekleme yapma
  * Eğer yoksa, "Kaynak" alanındaki @kullanıcıadı'nı kullan, örn: (@yagosabuncuoglu)
  * İki kaynak ekleme — bir tane yeterli
- Emoji EKLEME (zaten varsa koruyabilirsin).
- Tweet TÜRKÇE olsun.

==================== MÜKERRER KONTROLÜ ====================
Aşağıdakilerle aynı konuyu işleyen yeni haberi ATLA:
{recent_titles}

==================== HABER LİSTESİ ====================
{news_formatted}

==================== ÇIKTI FORMATI ====================
SADECE bir JSON array döndür. Açıklama, markdown, ön söz YOK:
[
  {{"id": 0, "decision": "PAYLAS", "tweet": "..."}},
  {{"id": 1, "decision": "ATLA"}}
]"""


def process_news_batch(news_items: List[Dict], recent_titles: List[str]) -> List[Dict]:
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
        raise
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        return []

    # Markdown code block'larını temizle
    clean_text = re.sub(r'^```[a-zA-Z]*\n?', '', response_text.strip())
    clean_text = re.sub(r'\n?```\s*$', '', clean_text)
    clean_text = clean_text.lstrip('﻿​‌‍⁠').strip()

    results = None

    # 1. Direkt parse
    try:
        results = json.loads(clean_text)
    except json.JSONDecodeError:
        pass

    # 2. Greedy regex
    if results is None:
        m = re.search(r'\[.+\]', clean_text, re.DOTALL)
        if m:
            try:
                results = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    # 3. Partial parse
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
