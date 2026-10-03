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
                 top_k=6):
        self.client = client
        self.knowledge = knowledge
        self.model = model
        self.index = index          # KnowledgeIndex for retrieval (optional)
        self.num_ctx = num_ctx      # model context window (optional)
        self.top_k = top_k

    def _knowledge_text(self, query):
        """Return (context_text, sources): retrieval if an index exists, else the
        capped dump (whose sources are all ingested docs)."""
        if self.index is not None:
            try:
                text, sources = self.index.retrieve(query, top_k=self.top_k)
                if text.strip():
                    return text, sources
            except Exception:
                pass  # fall back to the plain dump
        if self.knowledge is not None:
            text = self.knowledge.context_text()
            sources = ([{"name": n, "score": None}
                        for n, _ in self.knowledge.list_docs()]
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
