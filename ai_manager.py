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
# Etiket Validation
# ============================================================

_VALID_CATEGORIES = ('Transfer', 'Maç', 'Hakem', 'Yönetim', 'Açıklama', 'Sakatlık', 'Gündem')
_VALID_TEAMS = ('GS', 'FB', 'BJK', 'TS', 'TR')

def _validate_etiket(etiket: str) -> str:
    """Etiket formatını doğrular. Geçersizse '📌 Gündem | TR' döner."""
    if not etiket:
        return "📌 Gündem | TR"
    if '|' not in etiket:
        return "📌 Gündem | TR"
    parts = etiket.split('|', 1)
    if len(parts) != 2:
        return "📌 Gündem | TR"
    left = parts[0].strip()
    right = parts[1].strip()
    # Sol taraf emoji ile başlamalı (ASCII değil)
    if not left or left[0].isascii():
        return "📌 Gündem | TR"
    if not any(cat in left for cat in _VALID_CATEGORIES):
        return "📌 Gündem | TR"
    teams_in_right = [t.strip() for t in right.split('-')]
    if not teams_in_right or not all(t in _VALID_TEAMS for t in teams_in_right):
        return "📌 Gündem | TR"
    if len(etiket) > 60:
        return "📌 Gündem | TR"
    return etiket

# ============================================================
# Provider seçimi
# ============================================================
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

# Gemini client (mevcut anahtar varsa)
try:
    from google import genai as genai_new
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    _gemini_client = genai_new.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
except ImportError:
    _gemini_client = None
    GEMINI_MODEL = None

# Groq client (mevcut anahtar varsa)
try:
    from groq import Groq
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    _groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
except ImportError:
    _groq_client = None
    GROQ_MODEL = None

available = []
if _gemini_client:
    available.append(f"Gemini ({GEMINI_MODEL})")
if _groq_client:
    available.append(f"Groq ({GROQ_MODEL})")
logger.info(f"AI providers yüklü: {', '.join(available) or 'HİÇBİRİ'}")
logger.info(f"Öncelikli provider: {AI_PROVIDER}")


class AIQuotaExceeded(Exception):
    """429: Gerçek kota dolması — 1 saat dondur."""
    pass

class AIPromptTooLarge(Exception):
    """413: Prompt veya output sınırı aştı — batch'i yarıla."""
    pass

class AIModelDeprecated(Exception):
    """400: Model artık desteklenmiyor — fallback dene."""
    pass

class AITransientError(Exception):
    """502/503/504/timeout: Geçici sorun — kısa retry sonrası fallback."""
    pass


def _classify_error(err_str: str):
    """Hata mesajından doğru exception sınıfını döner."""
    err_lower = err_str.lower()
    if "429" in err_str or "rate_limit" in err_lower or "resource_exhausted" in err_lower or "tokens per day" in err_lower or "quota" in err_lower:
        return AIQuotaExceeded
    if "413" in err_str or "too large" in err_lower or "payload too large" in err_lower:
        return AIPromptTooLarge
    if "decommissioned" in err_lower or "deprecated" in err_lower or "no longer supported" in err_lower or "model_decommissioned" in err_lower:
        return AIModelDeprecated
    if "503" in err_str or "502" in err_str or "504" in err_str or "timeout" in err_lower or "unavailable" in err_lower:
        return AITransientError
    return None

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
            error_class = _classify_error(err_str)
            if error_class is AIQuotaExceeded:
                logger.error(f"Groq quota exceeded: {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            if error_class is AIPromptTooLarge:
                logger.error(f"Groq 413 prompt too large: {err_str[:200]}")
                raise AIPromptTooLarge(err_str) from e
            if error_class is AIModelDeprecated:
                logger.error(f"Groq model deprecated: {err_str[:200]}")
                raise AIModelDeprecated(err_str) from e
            # Diğer (transient veya unknown): retry yap
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
            error_class = _classify_error(err_str)
            if error_class is AIQuotaExceeded:
                logger.error(f"Gemini quota exceeded: {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            if error_class is AIPromptTooLarge:
                logger.error(f"Gemini 413 prompt too large: {err_str[:200]}")
                raise AIPromptTooLarge(err_str) from e
            if error_class is AIModelDeprecated:
                logger.error(f"Gemini model deprecated: {err_str[:200]}")
                raise AIModelDeprecated(err_str) from e
            # Diğer (transient veya unknown): retry yap
            logger.warning(f"Gemini error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)
    raise last_err

def _get_provider_chain():
    """Env'deki provider'a göre fallback zinciri kur."""
    if AI_PROVIDER == "gemini":
        return ["gemini", "groq"]
    return ["groq", "gemini"]


def _call_provider(provider: str, prompt: str) -> str:
    """Belirli bir provider'a çağrı yap."""
    if provider == "gemini":
        if not _gemini_client:
            raise RuntimeError("Gemini client yok (GEMINI_API_KEY eksik?)")
        return _call_gemini(prompt)
    elif provider == "groq":
        if not _groq_client:
            raise RuntimeError("Groq client yok (GROQ_API_KEY eksik?)")
        return _call_groq(prompt)
    else:
        raise ValueError(f"Bilinmeyen provider: {provider}")


def _call_llm(prompt: str) -> str:
    """Provider chain ile çağrı yap. Biri kotada ise sonrakine geç."""
    chain = _get_provider_chain()
    last_quota_error = None
    last_other_error = None

    for provider in chain:
        try:
            logger.info(f"AI çağrısı: {provider}")
            return _call_provider(provider, prompt)
        except AIQuotaExceeded as e:
            logger.warning(f"{provider} kotası dolu, fallback denenecek")
            last_quota_error = e
            continue
        except AIPromptTooLarge as e:
            logger.warning(f"{provider} 413 prompt too large, fallback denenecek")
            last_other_error = e
            continue
        except AIModelDeprecated as e:
            logger.warning(f"{provider} modeli deprecated, fallback denenecek")
            last_other_error = e
            continue
        except RuntimeError as e:
            logger.warning(f"{provider} kullanılamıyor: {e}")
            last_other_error = e
            continue

    # Tüm provider'lar başarısız
    if last_quota_error:
        logger.error("Tüm provider'lar kotada — bot 1 saat dondurulacak")
        raise last_quota_error
    if last_other_error:
        raise last_other_error
    raise RuntimeError("Hiçbir provider çalıştırılamadı")

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

6) SAF EĞLENCE / SELAMLAŞMA: "İyi sabahlar Cimbomlular", "Hadi BJK", "Günaydın Trabzonlular" gibi tek satırlık taraftarlık paylaşımları, mood paylaşımları, takım renkleriyle yapılan emojili coşku tweet'leri ATLA. Somut bilgi, isim, olay yoksa atla.
   İSTİSNA: Futbolcunun KENDİ sosyal medya çıkışı veya açıklaması varsa PAYLAŞ.

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
ÖNEMLİ — KAYNAK METNİ ASLA DEĞİŞTİRME:
- AI olarak tweet metnini ÜRETMİYORSUN. Sadece kaynak haberin metnini AKTAR.
- Söz aktarımları varsa ("İsim: 'söz'") AYNEN koru.
- Yorum cümleni eklemek YASAK.

UZUNLUK:
- Eğer kaynak metin 230 karakterden KISA ise: aynen aktar (tweet alanına olduğu gibi yaz).
- Eğer kaynak metin 230 karakterden UZUN ise: anlamı bozmayacak şekilde 230 karaktere KISALT. Söz aktarımlarını bozma, isimleri çıkarma.

KAYNAK BİLGİSİ:
- Kaynak hesap kullanıcı adı kod tarafından otomatik eklenecek.
- Eğer kaynak metinde zaten "(@kullaniciadi)" veya "(Hürriyet)" gibi parantezli kaynak varsa, ekleme; yoksa kod ekleyecek.
- Tweet'ine kendin kaynak ekleme.

DİĞER:
- Emoji EKLEME (zaten varsa koruyabilirsin).
- Tweet TÜRKÇE olsun.
- Tweet alanı 230 karakteri AŞMASIN.

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
    except AIPromptTooLarge:
        if len(news_items) <= 1:
            logger.error("Tek haberle bile 413 — prompt yapısal olarak büyük")
            return []
        half = len(news_items) // 2
        logger.warning(f"Batch 413 — bölünüyor: {len(news_items)} → {half}+{len(news_items)-half}")
        first_half = process_news_batch(news_items[:half], recent_titles)
        second_half = process_news_batch(news_items[half:], recent_titles)
        return first_half + second_half
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
            tweet_text = (res.get("tweet") or "").strip()
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
