"""
ai_manager.py — Faz 8 Güncellemesi
==================================
Faz 8: AI artık metin üretmiyor — sadece PAYLAS/ATLA karar veriyor.
Tweet metni orijinal kaynaktan alınıyor (halüsinasyon riski sıfır).
AI_BATCH_SIZE 8 → 4 (daha küçük batch, daha az karışma).
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

# OpenRouter client (mevcut anahtar varsa) — OpenAI-compatible API
try:
    from openai import OpenAI as OpenRouterClient
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
    _openrouter_client = OpenRouterClient(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1"
    ) if OPENROUTER_API_KEY else None
except ImportError:
    _openrouter_client = None
    OPENROUTER_MODEL = None

available = []
if _gemini_client:
    available.append(f"Gemini ({GEMINI_MODEL})")
if _openrouter_client:
    available.append(f"OpenRouter ({OPENROUTER_MODEL})")
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
                logger.warning(f"Gemini quota exceeded: {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            if error_class is AIPromptTooLarge:
                logger.warning(f"Gemini 413 prompt too large: {err_str[:200]}")
                raise AIPromptTooLarge(err_str) from e
            if error_class is AIModelDeprecated:
                logger.warning(f"Gemini model deprecated: {err_str[:200]}")
                raise AIModelDeprecated(err_str) from e
            # Diğer (transient veya unknown): retry yap
            logger.warning(f"Gemini error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)
    raise last_err

def _call_openrouter(prompt: str) -> str:
    """OpenRouter üzerinden gpt-oss-120b free model çağrısı."""
    backoff = [1, 2, 4]
    last_err = None

    for attempt, delay in enumerate(backoff):
        try:
            response = _openrouter_client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": "JSON yanıt veren Türkçe haber asistanı."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=8192,
                extra_headers={
                    "HTTP-Referer": "https://x.com/FlasFutbool",
                    "X-Title": "FlasFutbol Bot"
                }
            )

            content = response.choices[0].message.content
            if content is None:
                logger.warning("OpenRouter raw: content=None (model belki reasoning model)")
                last_err = ValueError("OpenRouter empty response")
                if attempt < len(backoff) - 1:
                    time.sleep(delay)
                continue

            in_tokens = response.usage.prompt_tokens if response.usage else 0
            out_tokens = response.usage.completion_tokens if response.usage else 0
            logger.info(f"OpenRouter raw: len={len(content)} tokens(in={in_tokens}, out={out_tokens})")

            return content

        except Exception as e:
            err_str = str(e)
            last_err = e
            error_class = _classify_error(err_str)
            if error_class is AIQuotaExceeded:
                logger.error(f"OpenRouter quota exceeded: {err_str[:200]}")
                raise AIQuotaExceeded(err_str) from e
            if error_class is AIPromptTooLarge:
                logger.error(f"OpenRouter 413 prompt too large: {err_str[:200]}")
                raise AIPromptTooLarge(err_str) from e
            if error_class is AIModelDeprecated:
                logger.error(f"OpenRouter model deprecated: {err_str[:200]}")
                raise AIModelDeprecated(err_str) from e
            logger.warning(f"OpenRouter error attempt {attempt+1}: {err_str[:200]}")
            if attempt < len(backoff) - 1:
                time.sleep(delay)

    raise RuntimeError(f"OpenRouter failed after retries: {last_err}")

def _get_provider_chain():
    """Env'deki provider'a göre fallback zinciri kur. OpenRouter middle olarak girer."""
    if AI_PROVIDER == "gemini":
        return ["gemini", "openrouter", "groq"]
    if AI_PROVIDER == "openrouter":
        return ["openrouter", "gemini", "groq"]
    return ["groq", "openrouter", "gemini"]


def _call_provider(provider: str, prompt: str) -> str:
    """Belirli bir provider'a çağrı yap."""
    if provider == "gemini":
        if not _gemini_client:
            raise RuntimeError("Gemini client yok (GEMINI_API_KEY eksik?)")
        return _call_gemini(prompt)
    elif provider == "openrouter":
        if not _openrouter_client:
            raise RuntimeError("OpenRouter client yok (OPENROUTER_API_KEY eksik?)")
        return _call_openrouter(prompt)
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
# Prompt — Faz 8 (Sadece PAYLAS/ATLA — metin üretme yok)
# ============================================================

SYSTEM_PROMPT = """Sen Türk **FUTBOL** odaklı bir filtreleme asistanısın.
Sana 4 büyük takım (Galatasaray, Fenerbahçe, Beşiktaş, Trabzonspor) ile ilgili tweet'ler verilecek.

Her tweet için sadece KARAR vereceksin:

"PAYLAS": Somut futbol haberi — transfer, açıklama, maç sonucu, sakatlık, başkanlık, kadro, antrenman raporu, hakem kararı, federasyon kararı.

"ATLA":
1) Sadece mizah/eğlence amaçlı VE haber değeri OLMAYAN içerik
2) Troll, sıradan yorum
3) Futbol DIŞI haberler:
   - Basketbol: "Fenerbahçe Beko", "Anadolu Efes", "Galatasaray Nef", "Beşiktaş Basketbol", "Real Madrid Basket", "Final Four", "Euroleague", "BSL", "NBA"
   - Voleybol, hentbol, yüzme, atletizm, futsal
4) Nostalji içerikleri:
   - "Tarihte bugün", "geçmiş şampiyonluk", "yıldönümü", "anma"
   - Eski maçların hatıraları, eski oyuncu retrospektifi
5) Kulüp resmi hesabının (örn. @GalatasaraySK) kutlama/anma paylaşımları (haber içerikleri PAYLAS kalır)

YENİ KURAL:
- Eğlenceli/mizahi içerik FUTBOL gündemiyle ilgili bir olay ya da bilgi içeriyorsa PAYLAS'tır.
- "Fenerbahçe Beko" kelimesini görürsen kesinlikle ATLA — bu basketbol.

ÖNEMLİ:
- Tweet metnini değiştirme, özetleme, yeniden yazma. Sadece karar ver.
- Sadece JSON dizisi yanıt ver, başka açıklama YAZMA.

Yanıt formatı:
[{"id": 0, "decision": "PAYLAS"}, {"id": 1, "decision": "ATLA"}]"""


def build_user_prompt(candidates: List[Dict]) -> str:
    lines = ["Aşağıdaki tweet'leri değerlendir:"]
    lines.append("")
    for idx, c in enumerate(candidates):
        text = c.get('title', '') or c.get('text', '') or ''
        lines.append(f"[{idx}] {text}")
        lines.append("")
    lines.append("JSON yanıt ver (sadece id ve decision):")
    return "\n".join(lines)


def process_news_batch(news_items: List[Dict], recent_titles: List[str]) -> List[Dict]:
    if not news_items:
        return []

    full_prompt = SYSTEM_PROMPT + "\n\n" + build_user_prompt(news_items)

    try:
        response_text = _call_llm(full_prompt)
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
    atla_count = 0
    for res in results:
        idx = res.get("id")
        decision = (res.get("decision") or "").upper()
        if idx is None or idx >= len(news_items):
            continue
        item = news_items[idx]
        snippet = (item.get('title') or '')[:80]
        if decision in ("PAYLAS", "PAYLAŞ"):
            logger.info(f"AI ✓ PAYLAS: {snippet}")
            processed.append({
                "idx": idx,
                "title": item['title'],
                "link": item['link'],
                "published_date": item.get('published_date', time.strftime('%Y-%m-%d %H:%M:%S'))
            })
        else:
            logger.info(f"AI ✗ ATLA: {snippet}")
            atla_count += 1

    logger.info(f"AI batch: {len(news_items)} işlendi, {len(processed)} paylaşılacak, {atla_count} atlandı.")
    return processed


def summarize_for_card(tweet_text: str, max_chars: int = 80) -> str:
    """
    Tweet metnini saatlik özet kartı için başlığa dönüştürür.
    Haber niteliğinde değilse (görüş, tebrik, reaksiyon) None döner.
    Her zaman nokta ile biter.
    max_chars = görsel hard limit. AI'ye soft_limit (max_chars - 15) hedefi verilir,
    böylece AI rahat yazar; trim sadece görsel limit aşılırsa devreye girer.
    """
    if not tweet_text:
        return None

    cleaned = tweet_text.strip()
    cleaned = re.sub(r'https?://\S+', '', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if not cleaned:
        return None

    soft_limit = max(40, max_chars - 15)

    prompt = (
        f"Aşağıdaki tweet bir futbol haberi mi yoksa kişisel görüş/tebrik/taziye/reaksiyon mu?\n\n"
        f"Eğer haber DEĞİLSE (örn. 'geçmiş olsun', 'tebrikler', 'bence', kişisel yorum): "
        f"SADECE 'HABER_DEGIL' yaz.\n\n"
        f"Eğer habersa: Türkçe, HEDEF {soft_limit} KARAKTER (kesinlikle {max_chars}'i aşma), "
        f"FİİL ile biten ve nokta ile kapanan TAM CÜMLE bir başlık yaz. "
        f"ÖNEMLİ: cümle yarım kalmasın, fiil ile bitsin, '...' KULLANMA. "
        f"Çok uzunsa kısalt — detayları çıkar, ana eylemi koru. "
        f"KESİNLİKLE JSON, kod, süslü parantez, tırnak, etiket veya format kullanma. "
        f"Sadece düz Türkçe cümle yaz. Açıklama yok, sadece başlık.\n\n"
        f"Tweet:\n{cleaned}\n\nBaşlık:"
    )

    try:
        response = _call_llm(prompt)
        if not response:
            return _trim_to_word_boundary(cleaned, max_chars)
        result = response.strip().strip('"').strip("'").split('\n')[0].strip()
        if result.upper() == "HABER_DEGIL":
            logger.info(f"summarize_for_card: haber değil, atlandı — {cleaned[:60]}")
            return None
        # JSON / kod tortusunu temizle: "baslik": "..." pattern'ini parse et veya at
        result = _clean_json_artifacts(result)
        if not result or len(result) < 10:
            logger.warning(f"summarize_for_card: AI bozuk çıktı verdi, atlandı — {cleaned[:60]}")
            return None
        # Hard limit aşıldıysa uyarı logla + kelime sınırında kes
        if len(result) > max_chars:
            logger.warning(f"AI başlığı {len(result)} char (>>{max_chars}), trim ediliyor: {result}")
            result = _trim_to_word_boundary(result, max_chars)
        # Nokta garantisi
        if result and result[-1] not in '.!?':
            result += '.'
        return result
    except Exception as e:
        logger.warning(f"summarize_for_card hata: {e}, fallback")
        return _trim_to_word_boundary(cleaned, max_chars)


def _clean_json_artifacts(text: str) -> str:
    """AI yanıtı JSON/kod formatında geldiyse içeriği çıkar veya atla."""
    if not text:
        return text
    s = text.strip()
    # {"baslik": "X"} veya benzer JSON wrapper
    if s.startswith('{') or s.startswith('['):
        m = re.search(r'"([^"]{10,})"', s)
        if m:
            return m.group(1).strip()
        return ""  # parse edilemez → bozuk
    # Süslü parantez, köşeli parantez ya da JSON key kalıntısı içerikte ise temizle
    s = re.sub(r'^[\{\[\}\]"\s,:]+', '', s)
    s = re.sub(r'["\{\}\[\]]+$', '', s)
    if any(tok in s.lower() for tok in ['"baslik"', '"title"', '"headline"', '```']):
        return ""
    return s.strip()


def _trim_to_word_boundary(text: str, max_chars: int) -> str:
    """Metni max_chars'tan kısa kelime sınırında keser ve nokta ile bitirir. '...' kullanmaz."""
    if not text:
        return text
    if len(text) <= max_chars:
        result = text.rstrip().rstrip('.,;:!?')
        return result + '.'
    cut = text[:max_chars].rstrip()
    last_space = cut.rfind(' ')
    if last_space > max_chars // 2:
        result = cut[:last_space].rstrip()
    else:
        result = cut.rstrip()
    result = result.rstrip('.,;:!?')
    return result + '.'


def _fallback_headline(text: str, max_chars: int) -> str:
    """Legacy alias — _trim_to_word_boundary'e yönlendir."""
    return _trim_to_word_boundary(text, max_chars)
