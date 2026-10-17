"""
Eva — turns a natural-language request into a YRBalanceTool modifier script.

Eva never edits the rules file directly. She emits a modifier script (the tool's
DSL), which the engine applies deterministically with Preview + Undo. She is
grounded in: the DSL reference, a compact summary of the loaded rules (real
section/tag names), and the user's knowledge docs.
"""

import re
from dataclasses import dataclass, field

from ..engine import (
    build_category_lists, get_val, parse_script, ScriptError, ModConfig,
    validate_spec, ALL_SOURCES, MAX_COLUMNS,
)

DSL_REFERENCE = """\
A modifier script is a target header in [brackets] followed by Tag = value lines.

Target scopes (comma-separate):
  $INFANTRY $VEHICLE $NAVAL $AIRCRAFT $BUILDING $DEFENSE
  composites: $ALL $UNIT (inf+veh+nav+air) $STRUCTURE (bld+def) $GROUND (inf+veh)
  or any literal section name (a unit like E1, a weapon, a warhead, [General], a
  super-weapon like NUKE).

Tag values:
  - A formula if it's math over the techno's own tags: Cost = TechLevel * 100.
    Operators + - * / // % **; functions min max round floor ceil abs clamp;
    grouping with ( ) or [ ]. Variables = the techno's numeric tags (Cost,
    Strength, TechLevel, Speed, Sight, BuildLimit, ...), plus Damage/Range/ROF of
    its primary weapon. A value set earlier in a block is visible to later ones.
  - Otherwise a literal, written verbatim: Elite = yes, Owner = Russians,Africans.

Filtering (optional), anywhere in a block, one condition per line:
  {EXCLUDE}            drop a techno matching ANY condition
  TechLevel = -1       exact; or <1 >10 <=N >=N for comparisons
  BuildLimit = !       has the tag defined
  Prerequisite = dummy   unbuildable (also 'impossible')
  Primary = M60        exact match
  [ADOG]               this specific section
  {/EXCLUDE}
  {INCLUDE} ... {/INCLUDE}   whitelist: keep only technos that match

Options at the top: #name:  #desc:  #param: factor = 100  (use as {factor})

Output RULES: reply with ONE fenced code block containing only the script, and at
most a one-sentence explanation outside it. Use real section/tag names from the
rules summary. Do not invent tags.
"""


@dataclass
class EvaReply:
    text: str = ""            # full model reply
    script: str = ""          # extracted modifier script (may be "")
    valid: bool = False       # did the script parse?
    error: str = ""           # parse error, if any
    sources: list = field(default_factory=list)  # [{name, score}] consulted


@dataclass
class TagPlan:
    """A structured unit-tagging action Eva produced from a plain-English request."""
    tag: str = ""
    action: str = "add"       # "add" | "remove"
    name_contains: str = ""
    kind: str = ""            # infantry/vehicle/aircraft/building, or ""
    obtain: str = ""          # buildable/crate/other/dummy/civilian/unknown, or ""
    has_tag: str = ""         # only units already tagged with this substring
    raw: str = ""
    error: str = ""


_TAG_SYS = """You convert a unit-tagging request into ONE JSON action for a Red \
Alert 2 / Yuri's Revenge unit library. Output ONLY a JSON object, no prose:
{{
  "tag": "<the tag to apply or remove>",
  "action": "add" or "remove",
  "name_contains": "<substring of unit names, or empty>",
  "kind": "<one of: {kinds}, or empty>",
  "obtain": "<one of: {obtain}, or empty>",
  "has_tag": "<restrict to units already tagged with this substring, or empty>"
}}
Existing tags in the library: {existing}.
Choose the narrowest filters that satisfy the request. "tag" is required; leave a
filter empty if the request doesn't constrain it."""


def _json_block(text):
    import json
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except ValueError:
        return None


def plan_tags(client, model, request, kinds, obtain_classes, existing_tags,
              num_ctx=None):
    """Ask the local model to turn a tagging request into a TagPlan."""
    sys_prompt = _TAG_SYS.format(
        kinds=", ".join(kinds), obtain=", ".join(obtain_classes),
        existing=", ".join(existing_tags) or "(none)")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": request}]
    try:
        content = client.chat(messages, model, num_ctx=num_ctx)
    except Exception as e:
        return TagPlan(error=f"{type(e).__name__}: {e}")
    data = _json_block(content)
    if not isinstance(data, dict):
        return TagPlan(error="Eva didn't return a usable tag action.", raw=content)
    plan = TagPlan(
        tag=str(data.get("tag", "")).strip(),
        action=(str(data.get("action", "add")).strip().lower() or "add"),
        name_contains=str(data.get("name_contains", "")).strip(),
        kind=str(data.get("kind", "")).strip().lower(),
        obtain=str(data.get("obtain", "")).strip().lower(),
        has_tag=str(data.get("has_tag", "")).strip(),
        raw=content)
    if plan.action not in ("add", "remove"):
        plan.action = "add"
    if not plan.tag:
        plan.error = "Eva didn't name a tag to apply."
    return plan


_ADDON_SYS = """You design ONE extra tab for a Red Alert 2 / Yuri's Revenge mod \
library. The tab collects INI sections and shows chosen tags as columns.
Output ONLY a JSON object, no prose:
{{
  "name": "<short tab title>",
  "source": "<one of: {sources}>",
  "list_section": "<index section to enumerate, e.g. AttachmentTypes — only for source 'typelist'>",
  "section_prefix": "<section-name prefix — only for source 'sections'>",
  "require_tags": ["<tag a row MUST set>", "..."],
  "any_tags": ["<row must set at least one of these>", "..."],
  "columns": [{{"label": "<header>", "tag": "<INI tag to read>"}}],
  "group_by": "<one of your own column labels, or empty>",
  "notes": "<1-3 sentences: what this collects and why>"
}}

Rules:
- "typelist" enumerates one index section (the usual choice when the feature
  declares its own list, e.g. [AttachmentTypes]). "technos" walks all
  Infantry/Vehicle/Aircraft/BuildingTypes — use it when the feature is a tag
  applied TO units. "sections" matches section names by prefix.
- Use require_tags/any_tags to keep out rows that merely exist; prefer any_tags
  when a feature spreads over several alternative tags.
- At most {maxcols} columns. Pick the tags a modder would actually compare.
- Names must be plain INI names: no brackets, '=' or quotes.
- Invent nothing. Use only section and tag names given in the request or the
  reference material. If the request names no usable tags, return
  {{"error": "<what you need from the user>"}}."""


def plan_addon(client, model, request, reference="", num_ctx=None):
    """Turn a plain-English "I want a tab for X" into a validated AddonSpec.

    `reference` is whatever the user pasted to explain the feature — a types
    list, example unit lines, a tag table. Eva is told to invent nothing beyond
    it, because a hallucinated tag name produces a tab that silently finds
    nothing.

    Returns (spec, errors, raw). `spec` is already validated by the engine, so
    the caller never sees an unchecked model field; `errors` is non-empty when
    the spec is unusable.
    """
    sys_prompt = _ADDON_SYS.format(sources=", ".join(ALL_SOURCES),
                                   maxcols=MAX_COLUMNS)
    user = request if not reference else (
        f"{request}\n\nReference material from the user "
        f"(authoritative — prefer these names):\n{reference[:8000]}")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user}]
    try:
        content = client.chat(messages, model, num_ctx=num_ctx)
    except Exception as e:
        return None, [f"{type(e).__name__}: {e}"], ""
    data = _json_block(content)
    if not isinstance(data, dict):
        return None, ["Eva didn't return a usable addon spec."], content
    if data.get("error") and not data.get("name"):
        return None, [str(data["error"])[:500]], content
    spec, errors = validate_spec(data)
    if not spec.columns and "columns: expected a list" not in errors:
        errors.append("columns: Eva chose no tags to show")
    return spec, errors, content


def summarize_balance(client, model, label, balance, num_ctx=None):
    """Turn a mod's per-kind balance-culture factors into a short plain-English
    profile. `balance` is {kind: {stat: {factor, confidence, samples}}} with
    factor = mod/vanilla. Returns the prose, or an error string on failure."""
    if not balance:
        return "No vanilla baseline available — balance culture unknown."
    rows = []
    for kind, kd in balance.items():
        stat_bits = ", ".join(
            f"{stat} {d['factor']:.2f}× ({d['confidence']}, n={d['samples']})"
            for stat, d in kd.items())
        rows.append(f"- {kind}: {stat_bits}")
    facts = "\n".join(rows)
    sys_prompt = (
        "You are a Red Alert 2 / Yuri's Revenge balance analyst. You are given a "
        "mod's per-kind stat factors RELATIVE TO VANILLA (factor = mod / vanilla; "
        ">1 means bigger than vanilla, <1 smaller). Write 2-4 sentences describing "
        "this mod's 'balance culture' — its pace, economy, durability, tech feel — "
        "so a modder can judge how to convert content into a differently-balanced "
        "mod. Be concrete and cite the directions. No preamble, no markdown.")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user",
                 "content": f"Mod: {label}\nFactors vs vanilla:\n{facts}"}]
    try:
        return client.chat(messages, model, num_ctx=num_ctx).strip()
    except Exception as e:
        return f"(Eva unavailable: {type(e).__name__}: {e})"


def _code_block(text):
    """Extract a fenced code block from a model reply.

    Tolerant of things local models actually emit: CRLF line endings, an
    optional language tag, trailing spaces after the opening fence, and a
    missing closing fence (truncated output). Falls back to the whole reply
    when it has no fence but already looks like a bare script."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    # ``` [lang] [spaces] \n  ...  (closing ``` or end-of-text)
    m = re.search(r"```[ \t]*[A-Za-z0-9_+.-]*[ \t]*\n(.*?)(?:\n```|\Z)",
                  t, re.DOTALL)
    if m:
        return m.group(1).strip()
    # No fence: accept it only if it reads like a script (a $scope or a
    # [Section] header at line start, plus at least one assignment). Keep just
    # the leading run of script-like lines so a trailing prose sentence
    # ("This script increases…") doesn't get parsed as a tag.
    if re.search(r"(?m)^\s*(\$[A-Za-z]|\[)", t) and "=" in t:
        kept = []
        for ln in t.splitlines():
            if _script_line(ln):
                kept.append(ln)
            elif kept:
                break  # first prose line after the script ends it
        return "\n".join(kept).strip()
    return ""


def _script_line(ln):
    """Heuristic: does this line belong to a modifier script (vs prose)?"""
    s = ln.strip()
    if not s:
        return True  # blank lines are fine inside a script
    if s[0] in "[{$;@":
        return True  # header, filter marker, scope, comment, generator directive
    return bool(re.search(r"[=<>]", s))  # assignment or filter comparison


def rules_summary(section_map, config=None, max_general=1500):
    """A compact description of the loaded rules for grounding (bounded)."""
    if section_map is None:
        return "(no rules loaded)"
    if config is None:
        config = ModConfig.default()
    cl = build_category_lists(section_map, config)
    out = ["Category counts: "
           + ", ".join(f"{k}={len(v)}" for k, v in cl.items())]
    for key, label in (("inf", "infantry"), ("veh", "vehicles"),
                       ("bld", "buildings")):
        sample = cl.get(key, [])[:6]
        for t in sample:
            tags = []
            for tag in ("Cost", "Strength", "TechLevel", "Primary"):
                v = get_val(section_map, t, tag)
                if v:
                    tags.append(f"{tag}={v}")
            if tags:
                out.append(f"[{t}] ({label}) " + " ".join(tags))
    gen = section_map.get("general")
    if gen:
        names = sorted(kr for _, (_, kr, _) in gen["keys"].items())
        joined = ", ".join(names)
        out.append("[General] tags: " + joined[:max_general]
                   + (" …" if len(joined) > max_general else ""))
    return "\n".join(out)


class Eva:
    def __init__(self, client, knowledge, model, index=None, num_ctx=None,
                 top_k=6, excluded_docs=None):
        self.client = client
        self.knowledge = knowledge
        self.model = model
        self.index = index          # KnowledgeIndex for retrieval (optional)
        self.num_ctx = num_ctx      # model context window (optional)
        self.top_k = top_k
        # Feed docs to hide (DLLs not present in the loaded mod) — gating.
        self.excluded_docs = set(excluded_docs or set())

    def _knowledge_text(self, query):
        """Return (context_text, sources): retrieval if an index exists, else the
        capped dump (whose sources are all ingested docs). Docs in
        self.excluded_docs are suppressed (reference-feed DLL gating)."""
        ex = self.excluded_docs
        if self.index is not None:
            try:
                text, sources = self.index.retrieve(query, top_k=self.top_k,
                                                    exclude=ex)
                if text.strip():
                    return text, sources
            except Exception:
                pass  # fall back to the plain dump
        if self.knowledge is not None:
            text = self.knowledge.context_text(exclude=ex)
            sources = ([{"name": n, "score": None}
                        for n, _ in self.knowledge.list_docs() if n not in ex]
                       if text.strip() else [])
            return text, sources
        return "", []

    def system_prompt(self, rules_text, knowledge_text):
        parts = [
            "You are Eva, an assistant that writes YRBalanceTool modifier scripts "
            "to rebalance Command & Conquer: Red Alert 2 Yuri's Revenge rules.",
            "\n== MODIFIER SCRIPT DSL ==\n" + DSL_REFERENCE,
            "\n== LOADED RULES SUMMARY ==\n" + rules_text,
        ]
        if knowledge_text.strip():
            parts.append("\n== REFERENCE (most relevant to the request) ==\n"
                         + knowledge_text)
        return "\n".join(parts)

    def ask(self, user_message, section_map=None, config=None, history=None):
        rules_text = rules_summary(section_map, config)
        knowledge_text, sources = self._knowledge_text(user_message)
        messages = [{"role": "system",
                     "content": self.system_prompt(rules_text, knowledge_text)}]
        messages += history or []
        messages.append({"role": "user", "content": user_message})

        content = self.client.chat(messages, self.model, num_ctx=self.num_ctx)
        script = _code_block(content)
        reply = EvaReply(text=content, script=script, sources=sources)
        if script:
            try:
                parse_script(script)
                reply.valid = True
            except ScriptError as e:
                reply.error = str(e)
        return reply
