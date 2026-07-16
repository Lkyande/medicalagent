import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings


def knowledge_root() -> Path:
    if settings.murtagh_knowledge_root:
        return Path(settings.murtagh_knowledge_root).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[2]
        / ".opencode"
        / "skills"
        / "disease-guidelines"
        / "skill"
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Murtagh knowledge file is unavailable: {path.name}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=1)
def routing_index() -> dict[str, Any]:
    return _read_json(knowledge_root() / "routing_index.json")


@lru_cache(maxsize=1)
def skills_registry() -> dict[str, Any]:
    return _read_json(knowledge_root() / "skills_registry.json")


def _terms(query: str, values: list[str] | None) -> list[str]:
    result = [value.strip() for value in values or [] if value.strip()]
    normalized_query = query.strip().lower()
    for section in ("disease_routes", "symptom_routes", "keyword_index"):
        for key in routing_index().get(section, {}):
            if key.lower() in normalized_query and key not in result:
                result.append(key)
    return result


def route_skills(
    query: str,
    diseases: list[str] | None = None,
    symptoms: list[str] | None = None,
    keywords: list[str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    index = routing_index()
    requested = {
        "disease_routes": _terms(query, diseases),
        "symptom_routes": _terms(query, symptoms),
        "keyword_index": _terms(query, keywords),
    }
    matches: list[dict[str, Any]] = []
    chapters: list[int] = []
    for section, terms in requested.items():
        routes = index.get(section, {})
        for term in terms:
            for route_term, route_chapters in routes.items():
                if term.lower() != route_term.lower() and route_term.lower() not in term.lower():
                    continue
                for chapter in route_chapters:
                    matches.append({"type": section, "term": route_term, "chapter": chapter})
                    if chapter not in chapters:
                        chapters.append(chapter)

    query_lower = query.lower()
    candidates: list[dict[str, Any]] = []
    for chapter in chapters:
        chapter_data = skills_registry().get("skills_by_chapter", {}).get(str(chapter), {})
        for skill in chapter_data.get("skills", []):
            searchable = " ".join(
                str(skill.get(field, ""))
                for field in ("skill_name", "objective", "scope")
            ).lower()
            score = sum(1 for term in requested["disease_routes"] + requested["symptom_routes"] + requested["keyword_index"] if term.lower() in searchable)
            if skill.get("skill_name", "").lower() in query_lower:
                score += 3
            emergency = any(term in searchable for term in ("紧急", "急诊", "红旗", "危重", "立即", "扭转", "休克"))
            candidates.append(
                {
                    "chapter_number": chapter,
                    "skill_id": skill.get("skill_id"),
                    "skill_name": skill.get("skill_name"),
                    "objective": skill.get("objective"),
                    "scope": skill.get("scope"),
                    "source_title": skill.get("source_title"),
                    "source_page": skill.get("source_page"),
                    "priority": "emergency" if emergency else "routine",
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: (item["priority"] == "emergency", item["score"]), reverse=True)
    bounded_limit = max(1, min(limit, 10))
    return {
        "knowledge_base": {
            "book": skills_registry().get("book"),
            "revision": skills_registry().get("generated_at"),
        },
        "matched_routes": matches,
        "candidates": candidates[:bounded_limit],
    }


@lru_cache(maxsize=1)
def _skill_lookup() -> dict[str, tuple[str, dict[str, Any]]]:
    result = {}
    for chapter, chapter_data in skills_registry().get("skills_by_chapter", {}).items():
        for skill in chapter_data.get("skills", []):
            skill_id = skill.get("skill_id")
            if skill_id:
                result[skill_id] = (chapter, skill)
    return result


def get_skills(skill_ids: list[str]) -> dict[str, Any]:
    if not skill_ids or len(skill_ids) > 5:
        raise ValueError("skill_ids must contain between one and five IDs")
    result = []
    for skill_id in skill_ids:
        entry = _skill_lookup().get(skill_id)
        if not entry:
            raise ValueError(f"Unknown skill_id: {skill_id}")
        chapter, metadata = entry
        path = (
            knowledge_root()
            / f"莫塔全科医学---第{chapter}章_skill"
            / metadata["file_path"]
        )
        if not path.is_file():
            raise RuntimeError(f"Approved skill content is unavailable: {skill_id}")
        result.append(
            {
                **metadata,
                "chapter_number": int(chapter),
                "content": path.read_text(encoding="utf-8-sig"),
                "review_status": "approved",
            }
        )
    return {
        "knowledge_base": {
            "book": skills_registry().get("book"),
            "revision": skills_registry().get("generated_at"),
        },
        "skills": result,
    }
