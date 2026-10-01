"""Refresh citation-verified rules for every scheme in the local catalog."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.extract import RuleExtraction, extract_rules
from src.ingest import load_pdf_pages, load_schemes, scheme_pdf_path
from src.models import Rule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleChanges:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.modified)


def _canonical_rule(rule: Rule) -> str:
    """Serialize rule semantics independent of citation and JSON key order."""
    data = rule.model_dump(mode="json")
    data["citations"] = sorted(
        data["citations"],
        key=lambda citation: json.dumps(citation, sort_keys=True, ensure_ascii=False),
    )
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compare_rules(previous: RuleExtraction, refreshed: RuleExtraction) -> RuleChanges:
    """Compare rule collections by ID, ignoring ordering but detecting content edits."""
    before: dict[str, list[str]] = defaultdict(list)
    after: dict[str, list[str]] = defaultdict(list)
    for rule in previous.rules:
        before[rule.id].append(_canonical_rule(rule))
    for rule in refreshed.rules:
        after[rule.id].append(_canonical_rule(rule))
    for values in (*before.values(), *after.values()):
        values.sort()

    before_ids = set(before)
    after_ids = set(after)
    return RuleChanges(
        added=tuple(sorted(after_ids - before_ids)),
        removed=tuple(sorted(before_ids - after_ids)),
        modified=tuple(
            sorted(
                rule_id
                for rule_id in before_ids & after_ids
                if before[rule_id] != after[rule_id]
            )
        ),
    )


def _cached_extraction(cache_path: Path) -> RuleExtraction:
    if not cache_path.is_file():
        return RuleExtraction()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return RuleExtraction.model_validate(payload["extraction"])


def _cache_path(schemes_dir: Path, scheme_id: str) -> Path:
    return schemes_dir / f"{scheme_id}.rules.json"


def refresh_all_rules(
    schemes_dir: Path,
    *,
    llm_call: Callable[[str], str] | None = None,
) -> int:
    """Refresh all catalog rules, report changes, and return the failure count."""
    schemes = load_schemes(schemes_dir)
    if not schemes:
        print("No schemes are listed in the catalog.")
        return 0

    failures = 0
    for scheme in schemes:
        cache_path = _cache_path(schemes_dir, scheme.id)
        try:
            previous = _cached_extraction(cache_path)
            pdf_path = scheme_pdf_path(scheme, schemes_dir)
            pages = load_pdf_pages(pdf_path)
            refreshed = extract_rules(
                pages,
                scheme_id=scheme.id,
                document=pdf_path.name,
                cache_dir=schemes_dir,
                llm_call=llm_call,
                force_refresh=True,
            )
        except Exception as exc:
            failures += 1
            logger.exception("Could not refresh rules for %s", scheme.id)
            print(f"{scheme.id}: refresh failed: {type(exc).__name__}: {exc}")
            continue

        changes = compare_rules(previous, refreshed)
        if not changes.changed:
            print(f"{scheme.id}: no rule changes")
            continue
        print(f"{scheme.id}: rules changed")
        for label, rule_ids in (
            ("added", changes.added),
            ("removed", changes.removed),
            ("modified", changes.modified),
        ):
            for rule_id in rule_ids:
                print(f"  {label}: {rule_id}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    logging.basicConfig(level=logging.INFO)
    return int(refresh_all_rules(root / "data" / "schemes") > 0)


if __name__ == "__main__":
    raise SystemExit(main())
