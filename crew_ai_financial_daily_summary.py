import os
import json
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image


SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")  
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")  

CREWAI_FLOW_NAME = "financial_daily_summary"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def litellm_generate(prompt: str, max_tokens: int = 512, temperature: float = 0.0) -> str:
    """
    Minimal wrapper to call Litellm (or placeholder). Replace with real SDK calls
    depending on the "litellm" library you have. This function returns the model text.
    """
    if not LITELLM_API_KEY:
        logger.warning("LITELLM_API_KEY not set — returning mock response for testing.")
        return "[MOCK LLM RESPONSE] " + (prompt[:400] + "...")

    url = ""# REPLACE with real endpoint
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("text") or data.get("generated_text") or json.dumps(data)
    except Exception as e:
        logger.exception("LLM request failed")
        return "[LLM_ERROR] " + str(e)



def search_serper(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    """
    Simple Serper search (replace endpoint/key as needed). Returns list of {title, link, snippet, image}
    """
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set — returning mock search results.")
        return [
            {"title": "Mock: US markets rally on strong jobs data",
             "link": "your desired link",
             "snippet": "S&P 500 closed higher after...",
             "image": "your desired image"},
        ]

    url = "https://api.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num_results}
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    results = []
    for item in data.get("organic", [])[:num_results]:
        results.append({
            "title": item.get("title"),
            "link": item.get("link"),
            "snippet": item.get("snippet"),
            "image": item.get("image") or None,
        })
    return results


def search_tavily(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    
    if not TAVILY_API_KEY:
        return search_serper(query, num_results)

    # If you have Tavily API details, implement here
    return search_serper(query, num_results)


def search_agent_us_financial_news() -> List[Dict[str, Any]]:
    """
    Search agent: fetch US financial news from the last hour. This function tries both Tavily
    and Serper and merges results.
    """
    # Build a time-aware query: markets close ~16:00 ET -> we want last hour of news about US markets.
    now = datetime.now(timezone.utc)
    query = "US markets news last hour OR " \
            "Wall Street today OR S&P 500 NASDAQ Dow Jones earnings"

    logger.info("Running search_agent with query: %s", query)
    results = []
    try:
        results += search_tavily(query, num_results=5)
    except Exception:
        logger.exception("Tavily search failed, falling back to Serper")
    try:
        results += search_serper(query, num_results=5)
    except Exception:
        logger.exception("Serper search failed")

    # Simple dedupe by link
    seen = set()
    deduped = []
    for r in results:
        link = r.get("link") or r.get("title")
        if link in seen:
            continue
        seen.add(link)
        deduped.append(r)

    logger.info("search_agent found %d unique results", len(deduped))
    return deduped



def summary_agent_generate(results: List[Dict[str, Any]]) -> str:
    """
    Generate a concise summary (< 500 words) using the LLM. We pass the top results as context
    and instruct the model to produce bullet + paragraph style summary focused on trading activity.
    """
    if not results:
        return "No results found in the last hour."

    context_lines = []
    for i, r in enumerate(results[:6], start=1):
        context_lines.append(f"{i}. {r.get('title')} - {r.get('snippet') or ''} ({r.get('link')})")
    prompt = (
        "You are a succinct financial news summarizer. Given the following search results from US financial news, "
        "write a short, clear summary (under 500 words) focused on the most important market moves, drivers, "
        "and trading activity. Use 3 short bullets and a 2-4 sentence paragraph. Do not invent facts; if uncertain, say 'reported'.\n\n"
        "CONTEXT:\n" + "\n".join(context_lines) + "\n\nOUTPUT:\n"
    )
    out = litellm_generate(prompt, max_tokens=600, temperature=0.0)
    return out


def select_images_from_results(results: List[Dict[str, Any]], max_images: int = 2) -> List[str]:
    """
    Choose up to max_images URLs from search results. If results include no images, pick placeholders.
    """
    images = []
    for r in results:
        img = r.get("image")
        if img:
            images.append(img)
        if len(images) >= max_images:
            break
    while len(images) < max_images:
        images.append(f"https://via.placeholder.com/800x400.png?text=Financial+Chart+{len(images)+1}")
    logger.info("Selected %d images", len(images))
    return images



LANGUAGE_CODES = {"arabic": "ar", "hindi": "hi", "hebrew": "he"}


def translating_agent_translate(text: str, target_lang: str) -> str:
    """
    Translate the summary into target_lang using the LLM while preserving format (bullets, headings).
    """
    lang_code = LANGUAGE_CODES.get(target_lang.lower(), target_lang)
    prompt = (
        f"Translate the following summary into {target_lang} (language code: {lang_code}). \n"
        "Preserve the original formatting (bullets, short paragraphs). Do not add or remove content.\n\n"
        f"ORIGINAL:\n{text}\n\nTRANSLATED:\n"
    )
    translated = litellm_generate(prompt, max_tokens=800, temperature=0.0)
    return translated


def create_pdf(summary_texts_by_lang: Dict[str, str], images: List[str], out_path: str):
    """
    Create a PDF that contains: English summary first, then each translated language.
    Each section places images logically (we place the first image after the English header, second after first translated section).
    """
    c = canvas.Canvas(out_path, pagesize=letter)
    w, h = letter
    margin = 40
    y = h - margin

    def draw_text_block(title: str, text: str):
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, y, title)
        y -= 18
        c.setFont("Helvetica", 11)
        for line in text.split("\n"):
            if y < margin + 50:
                c.showPage()
                y = h - margin
            c.drawString(margin, y, line[:120])
            y -= 14
        y -= 10

    # English section
    eng = summary_texts_by_lang.get("english") or summary_texts_by_lang.get("en")
    draw_text_block("English Summary", eng)

    # place first image
    try:
        img_url = images[0]
        r = requests.get(img_url, stream=True, timeout=15)
        r.raise_for_status()
        img = Image.open(r.raw)
        img.thumbnail((500, 300))
        c.drawInlineImage(ImageReader(img), margin, y - 310, width=500, height=300)
        y -= 320
    except Exception:
        logger.exception("Failed drawing first image; skipping")

    # other languages
    for i, (lang, text) in enumerate(summary_texts_by_lang.items()):
        if lang.lower() in ("english", "en"):
            continue
        draw_text_block(f"{lang.capitalize()} Summary", text)
        # place second image after the first translated block
        if i == 1 and len(images) > 1:
            try:
                img_url = images[1]
                r = requests.get(img_url, stream=True, timeout=15)
                r.raise_for_status()
                img = Image.open(r.raw)
                img.thumbnail((500, 300))
                if y < margin + 320:
                    c.showPage()
                    y = h - margin
                c.drawInlineImage(ImageReader(img), margin, y - 310, width=500, height=300)
                y -= 320
            except Exception:
                logger.exception("Failed drawing second image; skipping")

    c.save()
    logger.info("PDF saved to %s", out_path)


def send_to_telegram(pdf_path: str, caption: str = "Daily Market Summary") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.warning("Telegram credentials not set. Skipping send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with open(pdf_path, "rb") as f:
        files = {"document": f}
        data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption}
        r = requests.post(url, data=data, files=files, timeout=30)
        if r.status_code == 200:
            logger.info("PDF sent to Telegram channel %s", TELEGRAM_CHANNEL_ID)
            return True
        else:
            logger.error("Telegram send failed: %s %s", r.status_code, r.text)
            return False


def crewai_flow_run():
    # guardrail: ensure we run only once per day (or per run) and capture errors
    try:
        results = search_agent_us_financial_news()
        if not results:
            logger.warning("No results; ending run")
            return False


        summary_en = summary_agent_generate(results)

        images = select_images_from_results(results, max_images=2)

        translations = {}
        translations["english"] = summary_en
        for lang in ["arabic", "hindi", "hebrew"]:
            translations[lang] = translating_agent_translate(summary_en, lang)
            time.sleep(0.5)

        # create PDF
        today = datetime.now().strftime("%Y%m%d")
        out_pdf = f"daily_summary_{today}.pdf"
        create_pdf(translations, images, out_pdf)

        # send to telegram
        sent = send_to_telegram(out_pdf, caption=f"Daily Market Summary - {today}")

        return True
    except Exception as e:
        logger.exception("CrewAI flow run failed")
        return False

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CrewAI Daily Financial Summary runner")
    parser.add_argument("--run", action="store_true", help="Run the CrewAI flow now")
    args = parser.parse_args()

    if args.run:
        ok = crewai_flow_run()
        if ok:
            print("Run completed — check working directory for PDF and logs.")
        else:
            print("Run failed. See logs for details.")
    else:
        print("This file contains a CrewAI flow template. Run with --run after configuring API keys.")
