"""Let a later stage ask for an earlier IR to be re-opened, instead of failing on a gap it cannot fill.

Stages run Rule -> Asset -> Scene -> Input, and a stage may only change its
own IR. When a later stage needs something an earlier IR does not provide
(e.g. the Scene must show a winner the Rule does not expose), it says so, either
explicitly in ``upstream_requests`` or as a required ``unresolved`` item whose
owner names the earlier IR. The engine then re-opens that IR once with the
requirement, re-checks the stages in between from their accepted replies, and
lets the requesting stage use what was added. Detection never looks at game
names; it reads only what the stage itself declared.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

ORDER = ("rule_ir", "asset_ir", "scene_ir", "input_ir")
_NAMES = {"rule": "rule_ir", "asset": "asset_ir", "scene": "scene_ir", "input": "input_ir"}

INSTRUCTION = (
    "If this stage cannot be completed because an EARLIER IR lacks something (for example the Scene must show a "
    "winner but the Rule has no state or outcome for it, or Input needs a Rule action that does not exist), do not "
    "work around it and do not guess ids: add \"upstream_requests\": [{\"ir\": \"rule_ir\", \"requirement\": "
    "\"<exactly what is missing and why, with source lines>\"}]. The engine re-opens that IR once, then asks you "
    "again with the new ids. Still return your best definition for everything else.")


def upstream_requests(slot: str, reply: Optional[Mapping[str, Any]],
                      candidate: Optional[Mapping[str, Any]] = None) -> List[Dict[str, str]]:
    """Requirements that ``slot`` places on earlier IRs (earliest IR first, de-duplicated)."""
    if slot not in ORDER:
        return []
    earlier = ORDER[:ORDER.index(slot)]
    reply = reply if isinstance(reply, Mapping) else {}
    found: List[Dict[str, str]] = []
    for item in reply.get("upstream_requests") or []:
        if isinstance(item, Mapping) and item.get("ir") in earlier and str(item.get("requirement") or "").strip():
            found.append({"ir": str(item["ir"]), "requirement": str(item["requirement"]).strip()[:1500],
                          "declared_as": "upstream_requests"})
    definition = reply.get("definition") if isinstance(reply.get("definition"), Mapping) else {}
    items = list((candidate or {}).get("unresolved") or []) + list(definition.get("unresolved") or []) \
        + list(reply.get("unresolved") or [])
    for item in items:
        if not isinstance(item, Mapping) or item.get("required") is not True:
            continue
        owners = {_NAMES[word] for word in re.findall(r"(rule|asset|scene|input)", str(item.get("owner") or "").lower())}
        for ir in earlier:
            if ir in owners:
                text = "{0}: {1}".format(item.get("path", "/"), item.get("reason") or "required semantics missing")
                found.append({"ir": ir, "requirement": text[:1500], "declared_as": "unresolved owner " +
                              str(item.get("owner"))})
    unique: Dict[tuple, Dict[str, str]] = {}
    for item in found:
        unique.setdefault((item["ir"], item["requirement"]), item)
    return sorted(unique.values(), key=lambda item: ORDER.index(item["ir"]))


def reopen_feedback(upstream: str, requesting: str, requests: List[Mapping[str, str]]) -> List[str]:
    wanted = [item["requirement"] for item in requests if item["ir"] == upstream]
    return ["Stage {0} (built after this one) cannot be completed without a change to {1}. It needs: {2}. Add exactly "
            "what is needed (for example a state variable, an action or an outcome result), keep every existing id, "
            "behaviour and behavior test valid, and cite the source lines.".format(requesting, upstream,
                                                                                 " | ".join(wanted))]


def document_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """Ids added, removed or changed in each list-of-entries field (e.g. state.variables, actions, outcomes)."""
    def entries(document: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for key, value in document.items():
            if isinstance(value, Mapping):
                for inner, items in value.items():
                    if isinstance(items, list):
                        result["{0}.{1}".format(key, inner)] = {str(i["id"]): i for i in items
                                                               if isinstance(i, Mapping) and "id" in i}
            elif isinstance(value, list):
                result[key] = {str(i["id"]): i for i in value if isinstance(i, Mapping) and "id" in i}
        return result
    before, after = entries(old or {}), entries(new or {})
    diff: Dict[str, Dict[str, List[str]]] = {}
    for field in sorted(set(before) | set(after)):
        a, b = before.get(field, {}), after.get(field, {})
        change = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
                  "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}
        if any(change.values()):
            diff[field] = {k: v for k, v in change.items() if v}
    return diff


def resume_feedback(upstream: str, requesting: str, requests: List[Mapping[str, str]],
                    diff: Mapping[str, Any]) -> List[str]:
    wanted = [item["requirement"] for item in requests if item["ir"] == upstream]
    return ["The engine re-opened {0} for your requirement ({1}). {0} now has these changes: {2}. Re-author {3} "
            "using them and drop the unresolved/upstream items they satisfy.".format(
                upstream, " | ".join(wanted)[:1500], diff or "none (it already provided it; use the existing ids)",
                requesting)]
