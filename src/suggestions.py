from math import log1p
from .llm import llm_enabled, llm_select_suggestions
from .utils import now_utc


def score_item(stats_doc):
    accepts = stats_doc.get("accepts", 0)
    rejects = stats_doc.get("rejects", 0)
    total = accepts + rejects

    if total == 0:
        return 0.0

    accept_rate = accepts / total
    volume = log1p(accepts) / 4.0  # grows slowly
    return accept_rate * 0.7 + volume * 0.3


async def build_suggestions(db, chat_id, current_items, limit):
    stats = db.stats

    current_set = {item["name"] for item in current_items}

    cursor = stats.find({"chat_id": chat_id})
    candidates = []
    async for doc in cursor:
        name = doc.get("name")
        if not name or name in current_set:
            continue
        candidates.append({
            "name": name,
            "display_name": doc.get("display_name", name),
            "accepts": doc.get("accepts", 0),
            "rejects": doc.get("rejects", 0),
        })

    candidates.sort(key=score_item, reverse=True)

    if llm_enabled() and candidates:
        top_candidates = candidates[: min(len(candidates), 20)]
        selected = await llm_select_suggestions(top_candidates, limit)
        if selected is not None:
            selected_set = set(selected)
            suggestions = []
            for doc in top_candidates:
                if doc["name"] in selected_set:
                    suggestions.append({
                        "name": doc["name"],
                        "display_name": doc["display_name"],
                    })
            return suggestions[:limit]

    suggestions = []
    for doc in candidates[:limit]:
        suggestions.append({
            "name": doc.get("name"),
            "display_name": doc.get("display_name", doc.get("name")),
        })

    return suggestions


async def record_feedback(db, chat_id, item_name, display_name, accepted: bool):
    stats = db.stats
    history = db.history

    update = {
        "$inc": {"accepts": 1} if accepted else {"rejects": 1},
        "$set": {
            "display_name": display_name,
            "updated_at": now_utc(),
        },
        "$setOnInsert": {
            "chat_id": chat_id,
            "name": item_name,
            "created_at": now_utc(),
            "accepts": 0,
            "rejects": 0,
        },
    }

    await stats.update_one(
        {"chat_id": chat_id, "name": item_name},
        update,
        upsert=True,
    )

    await history.insert_one({
        "chat_id": chat_id,
        "name": item_name,
        "display_name": display_name,
        "accepted": accepted,
        "at": now_utc(),
    })
