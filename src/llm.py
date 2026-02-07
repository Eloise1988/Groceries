import asyncio
import json
import logging
from openai import OpenAI

from .config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

_client = None
logger = logging.getLogger(__name__)


def _get_client():
    global _client
    if not OPENAI_API_KEY:
        return None
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def llm_enabled() -> bool:
    return bool(OPENAI_API_KEY)


def _call_llm(system_text: str, user_text: str, max_output_tokens: int = 400):
    client = _get_client()
    if client is None:
        return None

    try:
        if hasattr(client, "responses"):
            response = client.responses.create(
                model=OPENAI_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_text}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_text}],
                    },
                ],
                temperature=OPENAI_TEMPERATURE,
                max_output_tokens=max_output_tokens,
            )
            return response.output_text

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            temperature=OPENAI_TEMPERATURE,
            max_tokens=max_output_tokens,
        )
        if response.choices:
            return response.choices[0].message.content
        return None
    except Exception as exc:
        logger.exception("LLM call failed: %s", exc)
        return None


def _safe_json_array(text: str):
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return data


async def llm_parse_ingredients(title: str, raw_ingredients: list[str]):
    system = (
        "You are a helpful cooking assistant. "
        "Your task is to normalize recipe ingredients for a grocery list. "
        "Return only a JSON array of ingredient strings. "
        "Rules: remove quantities and units, keep only the ingredient name, "
        "keep common descriptors when essential (e.g., 'soy sauce', 'rice noodles'), "
        "deduplicate similar items, and keep names short."
    )
    payload = {
        "title": title,
        "ingredients": raw_ingredients,
    }
    user = (
        "Normalize the following recipe ingredients for a grocery list. "
        "Return JSON array only.\n" + json.dumps(payload, ensure_ascii=False)
    )

    def _run():
        return _call_llm(system, user, max_output_tokens=600)

    text = await asyncio.to_thread(_run)
    data = _safe_json_array(text)
    if data is None:
        return None

    cleaned = []
    for item in data:
        if not isinstance(item, str):
            continue
        name = " ".join(item.split()).strip()
        if name:
            cleaned.append(name)
    return cleaned


async def llm_select_suggestions(candidates: list[dict], limit: int):
    system = (
        "You are a conservative grocery assistant. "
        "Choose only items that are highly likely to be needed weekly. "
        "If unsure, return fewer items. "
        "Return only a JSON array of item names from the provided candidates."
    )

    payload = {
        "limit": limit,
        "candidates": candidates,
    }
    user = (
        "Select the most likely weekly items. "
        "Return JSON array only, using candidate 'name' values.\n" + json.dumps(payload, ensure_ascii=False)
    )

    def _run():
        return _call_llm(system, user, max_output_tokens=400)

    text = await asyncio.to_thread(_run)
    data = _safe_json_array(text)
    if data is None:
        return None

    selected = []
    for item in data:
        if isinstance(item, str):
            selected.append(item)
    return selected[:limit]


async def llm_extract_recipe_from_html(url: str, html_text: str):
    system = (
        "You are a precise recipe extractor. "
        "Given raw HTML of a recipe page, extract ingredients and steps. "
        "Return only JSON with keys: title (string), ingredients (array of strings), steps (array of strings). "
        "Do not invent content. If a field is unknown, return an empty array/string."
    )
    payload = {
        "url": url,
        "html": html_text[:120000],
    }
    user = "Extract the recipe data from this HTML. Return JSON only.\n" + json.dumps(payload, ensure_ascii=False)

    def _run():
        return _call_llm(system, user, max_output_tokens=800)

    text = await asyncio.to_thread(_run)
    try:
        obj = json.loads(text) if text else None
    except Exception:
        return None

    if not isinstance(obj, dict):
        return None

    title = obj.get("title") if isinstance(obj.get("title"), str) else ""
    ingredients = obj.get("ingredients") if isinstance(obj.get("ingredients"), list) else []
    steps = obj.get("steps") if isinstance(obj.get("steps"), list) else []

    ingredients = [i.strip() for i in ingredients if isinstance(i, str) and i.strip()]
    steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]

    return {
        "title": title.strip(),
        "ingredients": ingredients,
        "steps": steps,
    }
