"""Policy loading and deterministic, date-aware BM25 retrieval."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..data import Category

TOP_K = 10
REF = re.compile(r"\b[A-Z]{2}\.v\d+\.s\d+\b")


class PolicySection(BaseModel):
    section_id: str; heading: str; doc_id: str; version: int; doc_title: str
    categories: list[Category]; effective_from: date; effective_to: date | None = None
    status: Literal["current", "superseded"]; text: str


class RetrievedSection(BaseModel):
    section: PolicySection; rank: int; score: float; via: str | None = None


def _front(text: str, path: Path) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    end = text.find("\n---", 4)
    if end < 0: raise ValueError(f"{path}: unclosed front matter")
    out: dict[str, object] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep: raise ValueError(f"{path}: invalid front matter")
        value = value.strip()
        out[key] = [x.strip() for x in value[1:-1].split(",") if x.strip()] if value.startswith("[") else (value or None)
    return out, text[end + 4:]


def load_policies(policy_dir: str | Path = "data/policies") -> dict[str, PolicySection]:
    result: dict[str, PolicySection] = {}
    for path in sorted(Path(policy_dir).glob("*_v*.md")):
        meta, rest = _front(path.read_text(), path)
        try:
            doc_id, version = str(meta["doc_id"]), int(meta["version"])
            chunks = re.split(r"^## ", rest, flags=re.M)[1:]
            for chunk in chunks:
                line, _, body = chunk.partition("\n")
                section_id, sep, heading = line.partition(" — ")
                if not sep or not section_id.startswith(f"{doc_id}.v{version}."):
                    raise ValueError(f"{path}: invalid section heading {line!r}")
                if section_id in result: raise ValueError(f"{path}: duplicate section {section_id}")
                result[section_id] = PolicySection(section_id=section_id, heading=heading, doc_id=doc_id, version=version,
                    doc_title=str(meta["title"]), categories=meta["categories"], effective_from=date.fromisoformat(str(meta["effective_from"])),
                    effective_to=date.fromisoformat(str(meta["effective_to"])) if meta.get("effective_to") else None,
                    status=meta["status"], text=body.strip())
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(str(path)): raise
            raise ValueError(f"{path}: {exc}") from exc
    return result


def in_force(section: PolicySection, on: date) -> bool:
    return section.effective_from <= on and (section.effective_to is None or on <= section.effective_to)


def candidates(sections: dict[str, PolicySection], category: Category, on: date) -> list[PolicySection]:
    return [s for s in sections.values() if category in s.categories and in_force(s, on)]


def _tokens(text: str) -> list[str]: return re.findall(r"\w+", text.lower())


def search_policy(sections: dict[str, PolicySection], category: Category, policy_date: datetime, query: str, k: int = TOP_K) -> list[RetrievedSection]:
    pool = candidates(sections, category, policy_date.date())
    docs = [_tokens(f"{s.section_id} — {s.heading} {s.text}") for s in pool]
    terms, n = set(_tokens(query)), len(pool)
    if not n: return []
    avg = sum(map(len, docs)) / n
    df = Counter(t for doc in docs for t in set(doc))
    scored = []
    for index, (section, doc) in enumerate(zip(pool, docs)):
        tf, score = Counter(doc), 0.0
        for term in terms & df.keys():
            idf = math.log(1 + (n - df[term] + .5) / (df[term] + .5))
            score += idf * tf[term] * 2.5 / (tf[term] + 1.5 * (.25 + .75 * len(doc) / avg))
        scored.append((score, index, section))
    kept = sorted(scored, key=lambda x: (-x[0], x[1]))[:k]
    out = [RetrievedSection(section=s, rank=i + 1, score=score) for i, (score, _, s) in enumerate(kept)]
    seen = {r.section.section_id for r in out}
    for r in list(out):
        for ref in REF.findall(r.section.text):
            target = sections.get(ref)
            if target and ref not in seen and in_force(target, policy_date.date()):
                seen.add(ref); out.append(RetrievedSection(section=target, rank=len(out) + 1, score=0.0, via=r.section.section_id))
    return out
