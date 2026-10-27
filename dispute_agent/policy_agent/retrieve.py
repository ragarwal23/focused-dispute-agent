"""Policy Agent, step 1: search (code, no LLM). Find the policy sections that apply to this complaint.

Load the policy markdown files in data/policies/, keep the sections in force on the policy date for this category,
rank them by BM25 keyword match against the complaint, keep the top POLICY_TOP_K, then add any section those cite
("see LF.v3.s5") one hop out.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from dispute_agent.config import POLICY_TOP_K
from dispute_agent.data import Category

SECTION_REF = re.compile(r"\b[A-Z]{2}\.v\d+\.s\d+\b")  # a section ID like LF.v3.s5
BM25_K1, BM25_B = 1.5, 0.75  # the standard BM25 defaults


class PolicySection(BaseModel):
    section_id: str
    heading: str
    doc_id: str
    version: int
    doc_title: str
    categories: list[Category]
    effective_from: date
    effective_to: date | None = None
    status: Literal["current", "superseded"]
    text: str


class RetrievedSection(BaseModel):
    section: PolicySection
    rank: int
    score: float
    via: str | None = None  # set when the section was added because another retrieved section cites it


def _front_matter(text: str, path: Path) -> tuple[dict[str, object], str]:
    """Split a policy file into its `---` front matter (as a dict) and the body."""
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError(f"{path}: unclosed front matter")
    meta: dict[str, object] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"{path}: invalid front matter")
        value = value.strip()
        if value.startswith("["):
            meta[key] = [x.strip() for x in value[1:-1].split(",") if x.strip()]
        else:
            meta[key] = value or None
    return meta, text[end + 4:]


def load_policies(policy_dir: str | Path = "data/policies") -> dict[str, PolicySection]:
    """Every `## ` section of every policy file, keyed by section ID."""
    result: dict[str, PolicySection] = {}
    for path in sorted(Path(policy_dir).glob("*_v*.md")):
        meta, body = _front_matter(path.read_text(), path)
        try:
            doc_id, version = str(meta["doc_id"]), int(meta["version"])
            effective_to = meta.get("effective_to")
            for chunk in re.split(r"^## ", body, flags=re.M)[1:]:
                line, _, text = chunk.partition("\n")
                section_id, sep, heading = line.partition(" — ")
                if not sep or not section_id.startswith(f"{doc_id}.v{version}."):
                    raise ValueError(f"{path}: invalid section heading {line!r}")
                if section_id in result:
                    raise ValueError(f"{path}: duplicate section {section_id}")
                result[section_id] = PolicySection(
                    section_id=section_id,
                    heading=heading,
                    doc_id=doc_id,
                    version=version,
                    doc_title=str(meta["title"]),
                    categories=meta["categories"],
                    effective_from=date.fromisoformat(str(meta["effective_from"])),
                    effective_to=date.fromisoformat(str(effective_to)) if effective_to else None,
                    status=meta["status"],
                    text=text.strip(),
                )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(str(path)):
                raise  # already names the file
            raise ValueError(f"{path}: {exc}") from exc
    return result


def in_force(section: PolicySection, on: date) -> bool:
    return section.effective_from <= on and (section.effective_to is None or on <= section.effective_to)


def candidates(sections: dict[str, PolicySection], category: Category, on: date) -> list[PolicySection]:
    return [s for s in sections.values() if category in s.categories and in_force(s, on)]


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def search_policy(
    sections: dict[str, PolicySection], category: Category, policy_date: datetime, query: str, k: int = POLICY_TOP_K
) -> list[RetrievedSection]:
    pool = candidates(sections, category, policy_date.date())
    if not pool:
        return []
    docs = [_tokens(f"{s.section_id} — {s.heading} {s.text}") for s in pool]
    terms, n = set(_tokens(query)), len(pool)
    avg_len = sum(map(len, docs)) / n
    doc_freq = Counter(t for doc in docs for t in set(doc))

    # BM25: rank each section by how well its words match the complaint
    scored = []
    for index, (section, doc) in enumerate(zip(pool, docs)):
        tf, score = Counter(doc), 0.0
        for term in terms & doc_freq.keys():
            idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            length_norm = (1 - BM25_B) + BM25_B * len(doc) / avg_len
            score += idf * tf[term] * (BM25_K1 + 1) / (tf[term] + BM25_K1 * length_norm)
        scored.append((score, index, section))
    kept = sorted(scored, key=lambda x: (-x[0], x[1]))[:k]  # ties keep file order, so results are deterministic
    out = [RetrievedSection(section=s, rank=i + 1, score=score) for i, (score, _, s) in enumerate(kept)]

    # one hop of cross-references: add sections the kept ones cite, if in force on the policy date
    seen = {r.section.section_id for r in out}
    for r in list(out):
        for ref in SECTION_REF.findall(r.section.text):
            target = sections.get(ref)
            if target and ref not in seen and in_force(target, policy_date.date()):
                seen.add(ref)
                out.append(RetrievedSection(section=target, rank=len(out) + 1, score=0.0, via=r.section.section_id))
    return out
