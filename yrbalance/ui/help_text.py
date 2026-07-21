"""Help content shown by the script panel's [?] button and the Help menu."""

SCRIPT_HELP_HTML = """
<h3>Custom scripts</h3>
<p>A script is a target header in <b>[brackets]</b> followed by tag lines. It looks
like <code>rulesmd.ini</code> on purpose. Example:</p>
<pre>[$INFANTRY, $VEHICLE]
Cost = TechLevel * 100
Soylent = Cost * 0.5
Elite = yes</pre>

<h3>Target scopes (the header)</h3>
<p>Comma-separate any of these:</p>
<ul>
<li><b>Category keywords:</b> <code>$INFANTRY</code>, <code>$VEHICLE</code>,
<code>$NAVAL</code>, <code>$AIRCRAFT</code>, <code>$BUILDING</code>,
<code>$DEFENSE</code></li>
<li><b>Composites:</b> <code>$ALL</code>, <code>$UNIT</code> (inf+veh+nav+air),
<code>$STRUCTURE</code> (bld+def), <code>$GROUND</code> (inf+veh)</li>
<li><b>Any literal section name</b> — a specific unit, a weapon, a
<b>warhead</b>, a super-weapon, etc. Just type its section name, e.g.
<code>[NUKE]</code> or <code>[E1, GAWEAP, SA]</code>. Names not present in the
file are ignored.</li>
</ul>

<h3>Tag values — formula or literal (auto-detected)</h3>
<ul>
<li>If the value is math over known variables, it is a <b>formula</b>, evaluated
per techno: <code>Cost = TechLevel * 100</code>.</li>
<li>Otherwise it is a <b>literal</b>, written as-is: <code>Elite = yes</code>,
<code>Owner = Russians,Africans</code>. Wrap in quotes to force a literal.</li>
</ul>

<h3>Formulas</h3>
<ul>
<li><b>Operators:</b> <code>+ - * / // % **</code></li>
<li><b>Functions:</b> <code>min max round floor ceil abs clamp int</code></li>
<li><b>Grouping:</b> parentheses <code>( )</code> <i>or</i> brackets
<code>[ ]</code> — both work: <code>Cost = 20 * [Cost / TechLevel]</code></li>
<li><b>Variables:</b> the techno's own numeric tags (<code>Cost</code>,
<code>Strength</code>, <code>TechLevel</code>, <code>Speed</code>,
<code>Sight</code>, <code>BuildLimit</code>, <code>Points</code>, …), plus
<code>Damage</code>/<code>Range</code>/<code>ROF</code> from its primary weapon,
and <code>EffectiveTechLevel</code>. A value set earlier in the block is visible
to later formulas (so <code>Soylent = Cost * 0.5</code> uses the new Cost).</li>
<li><b>Placeholders:</b> <code>{name}</code> — <code>#param:</code> values,
<code>{NAME}</code> (the section name), and config keywords like
<code>{UNBUILDABLE}</code>.</li>
</ul>

<h3>Metadata and options (top of the script)</h3>
<pre>#name: My Modifier      ; shows in the dropdown
#desc: what it does
#param: factor = 100    ; a fill-in value, used as {factor}
#raw: true              ; write plain values (no ; vanilla= annotation)</pre>

<h3>Generators (advanced — make new sections)</h3>
<p>Host squads, platforms, AttachEffects, etc. use a <code>[+ PATTERN from
SCOPE]</code> header with <code>@clone</code>, <code>@emit</code>,
<code>@register</code>, and <code>@patch</code> directives. See
<code>docs/SCRIPTS.md</code> for the full reference.</p>
"""
