"""Render actual translator output for a manual learner audit (not native popup UI)."""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/learner-review'
OUT.mkdir(exist_ok=True)

def render(node):
    if isinstance(node, str):
        return html.escape(node)
    if isinstance(node, list):
        return ''.join(map(render, node))
    if not isinstance(node, dict):
        return ''
    tag = node.get('tag', 'div')
    style = ';'.join(re.sub(r'[A-Z]', lambda m: '-' + m[0].lower(), k) + ':' + str(v)
                     for k, v in node.get('style', {}).items())
    return f'<{tag} style="{html.escape(style, quote=True)}">{render(node.get("content", ""))}</{tag}>'

css = '''body{background:#222;color:#eee;font:17px/1.45 Arial;margin:24px}
main{display:grid;grid-template-columns:460px 460px;gap:24px}article{border-top:1px solid #666;padding:12px 0}
h1{font-size:24px}h2{font-size:15px;color:#aaa}h3{font-size:28px;font-weight:400;margin:0 0 6px}
.grammar{color:#aaa;font-size:14px}summary{color:#aaa;cursor:pointer}a{color:#9cd} .note{font-size:13px;color:#aaa}'''
rows = json.loads((ROOT / 'build/learner-review-lookup.json').read_text(encoding='utf-8'))
links = []
for row in rows:
    columns = []
    for dictionary in ('Turkish Monolingual', 'Turkish Bilingual'):
        entries = []
        for entry in row['entries']:
            definitions = [d for d in entry['definitions'] if d['dictionary'] == dictionary]
            if not definitions:
                continue
            title = ' / '.join(h['term'] for h in entry['headwords'])
            grammar = []
            for chain in entry['inflectionRuleChainCandidates']:
                labels = ' · '.join(r['name'] for r in chain['inflectionRules'])
                if labels:
                    grammar.append('<div class="grammar">' + html.escape(labels) + '</div>')
            body = ''.join(render(v.get('content', '') if isinstance(v, dict) else v)
                           for d in definitions for v in d['entries'])
            entries.append(f'<article><h3>{html.escape(title)}</h3>{"".join(grammar)}{body}</article>')
        columns.append(f'<section><h2>{dictionary}</h2>{"".join(entries) or "No full-word match."}</section>')
    word = row['word']
    page = f'<!doctype html><meta charset="utf-8"><title>{word} review</title><style>{css}</style><h1>{word}</h1><p class="note">Actual exported content and translator results. Approximate popup typography; 460px columns. More starts closed.</p><main>{"".join(columns)}</main>'
    (OUT / f'{word}.html').write_text(page, encoding='utf-8')
    links.append(f'<li><a href="{word}.html">{word}</a></li>')
(OUT / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Learner review</title><style>' + css + '</style><h1>Ten lookup samples</h1><ul>' + ''.join(links) + '</ul>', encoding='utf-8')
print(OUT / 'index.html')
