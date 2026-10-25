"""Build a compact HTML content preview from normalized records, not a platform export."""
import html
import json
import re
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import key, read_json, write_json

BADGES = {
    'Noun': 'noun', 'Verb': 'verb', 'Adj': 'adjective', 'Adv': 'adverb', 'Pron': 'pronoun',
    'A3pl': 'plural', 'P1sg': 'my (possessive)', 'P2sg': 'your (possessive)', 'P3sg': 'his / her / its (possessive)',
    'P1pl': 'our (possessive)', 'P2pl': 'your (plural possessive)', 'P3pl': 'their (possessive)',
    'Acc': 'accusative (direct object)', 'Dat': 'dative (to / toward)', 'Loc': 'locative (in / at)',
    'Abl': 'ablative (from)', 'Gen': 'genitive (of)', 'Ins': 'instrumental (with / by)',
    'Neg': 'negative', 'Unable': 'inability (cannot)', 'Able': 'ability (can)', 'Past': 'past', 'Narr': 'reported past',
    'Prog1': 'present continuous', 'Prog2': 'progressive', 'Fut': 'future', 'Aor': 'aorist (general / habitual)',
    'Imp': 'imperative', 'Cond': 'conditional (if)', 'Neces': 'necessity (must)',
    'Pass': 'passive', 'Caus': 'causative (make / have)',
    'PastPart': '-DIK clause / participle', 'FutPart': 'future participle', 'PresPart': 'present participle',
    'A1sg': 'I', 'A2sg': 'you', 'A1pl': 'we', 'A2pl': 'you (plural)',
}
HINTS = {'A3pl': 'More than one, or they as the subject of a verb', 'P1sg': 'Belongs to me',
         'P2sg': 'Belongs to you', 'P3sg': 'Belongs to him, her, or it', 'P1pl': 'Belongs to us',
         'P2pl': 'Belongs to you as a group, or polite you', 'P3pl': 'Belongs to them',
         'Acc': 'Marks the specific thing affected by an action', 'Dat': 'Direction or recipient',
         'Loc': 'Location', 'Abl': 'Source or starting point; meaning depends on context',
         'Prog1': 'Progressive aspect, commonly taught as the present continuous; a following past marker changes the tense',
         'Prog2': 'Progressive aspect: an action in progress',
         'A1sg': 'First-person singular subject', 'A2sg': 'Second-person singular subject',
         'A1pl': 'First-person plural subject', 'A2pl': 'Second-person plural subject, or polite you'}
SOURCE = {'tdk': 'TDK', 'kenet': 'KeNet', 'tureng': 'Tureng', 'wty': 'Wiktionary'}


def content_text(node):
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        # Inline children already carry their own spaces (e.g. Yüz + ümü).
        return ''.join(content_text(n) for n in node)
    if isinstance(node, dict):
        marker = node.get('data', {}).get('content', '')
        if marker in ('preamble', 'backlink', 'details-entry-examples') or marker.startswith('example-'):
            return ''
        if marker == 'tag':
            # Human-readable usage labels carry meaning; raw HTML/data attributes do not.
            label = node.get('title') or content_text(node.get('content', ''))
            return '(' + label + ') ' if label else ''
        text = content_text(node.get('content', ''))
        return text + '\n' if node.get('tag') in ('div', 'p', 'li', 'br') else text
    return ''


def glosses(node):
    if isinstance(node, dict):
        if node.get('data', {}).get('content') == 'glosses':
            content = node.get('content', [])
            return [content_text(n).strip() for n in (content if isinstance(content, list) else [content])]
        return glosses(node.get('content', []))
    if isinstance(node, list):
        return [text for n in node for text in glosses(n)]
    return []


def more_list(items, visible, start=1, number_single=False):
    if len(items) == 1 and not number_single:
        return '<div class="sense">' + items[0] + '</div>'
    listing = lambda values, start=1: '<div class="senses">' + ''.join('<div class="sense">' + str(i) + '. ' + value + '</div>' for i, value in enumerate(values, start)) + '</div>'
    if not visible or len(items) <= visible + 1:
        return listing(items, start)
    return listing(items[:visible], start) + '<details><summary>More</summary>' + listing(items[visible:], start + visible) + '</details>'


def marked(node, marker):
    if isinstance(node, dict):
        if node.get('data', {}).get('content') == marker:
            return [node.get('content', '')]
        return marked(node.get('content', []), marker)
    if isinstance(node, list):
        return [item for child in node for item in marked(child, marker)]
    return []


def wiktionary_senses(node):
    for group in marked(node, 'glosses'):
        for sense in group if isinstance(group, list) else [group]:
            examples = [content_text(x) for x in marked(sense, 'example-sentence-a')]
            translations = [content_text(x) for x in marked(sense, 'example-sentence-b')]
            yield content_text(sense).strip(), examples, translations


def source_content(source, senses, lemma, settings):
    escape = html.escape
    if source == 'kenet':
        groups = []
        seen = set()
        tdk_texts = settings.get('tdk_sense_texts', [])
        for sense in sorted(senses, key=lambda s: int(s.get('member_sense') or 0)):
            synonyms = list(dict.fromkeys(m['spelling'] for m in sense.get('synonyms', []) if key(m['spelling']) != key(lemma)))
            if not synonyms and not settings.get('kenet_full_entries'):
                continue
            identity = (sense.get('synset_id'), tuple(synonyms))
            if identity in seen:
                continue
            seen.add(identity)
            text = sense.get('text', '').strip()
            # Keep KeNet's meaning independent of TDK sense numbers and labels.
            group = escape(text)
            if synonyms:
                group += '<div class="synonyms">' + ' · '.join(map(escape, synonyms)) + '</div>'
            groups.append(group)
        if groups:
            return more_list(groups, settings.get('visible_senses'))
        if tdk_texts:
            return ''
        # A KeNet-only headword still needs its definition when no synonyms exist.
    if source == 'tureng':
        words = list(dict.fromkeys(s['text'] for s in senses if s.get('text')))
        visible = settings.get('visible_translations') or 10
        if len(words) <= visible + 1:
            return '<p>' + '; '.join(map(escape, words)) + '</p>'
        return ('<p>' + '; '.join(map(escape, words[:visible])) + '</p>'
                '<details><summary>More</summary><p>' + '; '.join(map(escape, words[visible:])) + '</p></details>')
    items = []
    etymologies = []
    previous_grammar = None
    for sense in senses:
        examples = [e.get('text', '') if isinstance(e, dict) else e for e in sense.get('examples', [])]
        rows = [(sense['text'], examples, [])] if sense.get('text') else list(wiktionary_senses(sense.get('content')))
        for value in marked(sense.get('content'), 'Etymology-content'):
            text = content_text(value).replace('\\n', '\n').strip()
            if text.startswith('Etymology tree\n'):
                # WTY serializes the tree before its prose explanation.
                lines = text.splitlines()[1:]
                prose = next((i for i, line in enumerate(lines) if line.startswith(('Inherited from ', 'Borrowed from ', 'From ', 'Ultimately ', 'Compound of '))), None)
                text = '\n'.join(lines[prose:] if prose is not None else lines)
            if text and text not in etymologies:
                etymologies.append(text)
        synonyms = sorted({m['spelling'] for m in sense.get('synonyms', []) if key(m['spelling']) != key(lemma)})
        for text, examples, translations in rows:
            if not text.strip():
                continue
            grammar = None
            if source == 'wty':
                match = re.match(r'^\((transitive|intransitive|ditransitive) verb\)\s*', text)
                if match:
                    grammar = match[1]
                    text = text[match.end():]
            rendered = escape(text)
            if source == 'tdk':
                # Only this sense's own restrictions; never transfer to KeNet.
                labels = [x for x in sense.get('labels', []) if x not in ('isim', 'sıfat', 'zarf', 'zamir', 'nesnesiz', '-i', '-e', '-den', '-le', '-de')]
                if labels:
                    rendered = '<span class="usage">' + escape(', '.join(labels)) + '</span> ' + rendered
            if grammar and grammar != previous_grammar:
                rendered = '<span class="usage">' + escape(grammar) + '</span> ' + rendered
            previous_grammar = grammar
            if synonyms:
                rendered += '<div class="synonyms">≈ ' + ' · '.join(map(escape, synonyms)) + '</div>'
            if examples:
                for i, example in enumerate(examples):
                    rendered += '<div class="examples"><i>' + escape(example) + '</i>'
                    if i < len(translations):
                        rendered += '<div class="translation">' + escape(translations[i]) + '</div>'
                    rendered += '</div>'
            if rendered not in items:
                items.append(rendered)
    result = more_list(items, settings.get('visible_senses'), settings.get('sense_start', 1), settings.get('number_senses', False)) if items else ''
    etymology = '<details><summary>Etymology</summary>' + ''.join('<p>' + escape(t) + '</p>' for t in etymologies) + '</details>' if etymologies else ''
    if settings.get('etymology_only'):
        return etymology
    if settings.get('etymology') != 'separate':
        result += etymology
    return result


def morphology_rows(mappings):
    rows = []
    for record in mappings:
        if record['provenance']['raw'] == 'exact':
            continue
        parts = []
        pos = None
        feature_ids = {f['id'] for f in record['features']}
        for feature in record['features']:
            tag = feature['id']
            if tag in ('Noun', 'Verb', 'Adj', 'Adv', 'Pron'):
                pos = tag
            if tag in ('Noun', 'Verb', 'A3sg', 'Pnon', 'Nom', 'Pos', 'Zero', 'Pres'):
                continue
            if feature['label'].startswith('Engine tag:'):
                continue
            label = ('they' if pos == 'Verb' else 'plural') if tag == 'A3pl' else BADGES.get(tag, feature['label'])
            if tag.startswith('P') and tag in BADGES and feature_ids & {'PastPart', 'FutPart', 'Inf2', 'Inf3'}:
                label = {'P1sg': 'subject: I', 'P2sg': 'subject: you', 'P3sg': 'subject: he / she / it',
                         'P1pl': 'subject: we', 'P2pl': 'subject: you (plural)', 'P3pl': 'subject: they'}.get(tag, label)
            if tag == 'Prog1' and feature_ids & {'Past', 'Narr', 'Fut'}:
                label = 'progressive'
            hint = HINTS.get(tag, label)
            parts.append(f'<span class="badge" title="{html.escape(hint, quote=True)}">{html.escape(label)}</span>')
        row = ''.join(parts)
        if row and row not in rows:
            rows.append(row)
    return '<div class="morph">' + '<span class="alternative">or</span>'.join('<div class="analysis">' + row + '</div>' for row in rows) + '</div>'


def main():
    from scripts.export_prototype import render_definition

    settings = read_json(ROOT / 'config/build.json')['presentation']
    outputs = read_json(ROOT / 'build/latest.json')['outputs']
    db = sqlite3.connect(Path(outputs['normalize']) / 'definitions.sqlite3')
    samples = [('evlerimizden', 'ev'), ('kitabı', 'kitap'), ('geliyorum', 'gelmek')]
    mappings = {surface: [] for surface, _ in samples}
    with (Path(outputs['analyze']) / 'mappings.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            if record['surface'] in mappings:
                mappings[record['surface']].append(record)
    pages = []
    counts = {}
    try:
        for surface, lemma in samples:
            records = list(db.execute('SELECT id,language,senses,spelling FROM entries WHERE key=? ORDER BY id', (key(lemma),)))
            matching = [m for m in mappings[surface] if m['provenance']['analysis_lemma'] == key(lemma)]
            body = '<h2>' + html.escape(surface) + ' <span class="lemma">→ ' + html.escape(lemma) + '</span></h2>'
            body += morphology_rows(matching)
            for language in ('tr', 'en'):
                lexical_records = [(eid.split(':')[0], json.loads(raw), spelling)
                                   for eid, lang, raw, spelling in records if lang == language]
                rendered = render_definition(lemma, language, lexical_records, settings)
                for source in settings['source_order'][language]:
                    title = SOURCE[source]
                    if '<h4>' + title + '</h4>' in rendered:
                        counts[source] = counts.get(source, 0) + 1
                body += '<div class="dictionary">' + rendered + '</div>'
            pages.append('<article>' + body + '</article>')
    finally:
        db.close()
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Turkish dictionary preview</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f5f3;color:#242424;font:15px/1.45 system-ui,sans-serif}
main{max-width:650px;margin:24px auto;padding:0 16px}article{background:white;border:1px solid #ddd;border-radius:8px;margin:0 0 20px;padding:18px}
h2{font-size:21px;margin:0 0 4px}.lemma{font-weight:400;color:#666;font-size:17px}h4{font-size:12px;margin:0 0 4px;color:#666}
section{border-top:1px solid #e6e6e6;padding:9px 0 3px}p{margin:3px 0}ol{margin:3px 0;padding-left:22px}li{margin:3px 0}
.morph{display:flex;flex-wrap:wrap;align-items:center;gap:3px 6px;margin:0 0 7px}.analysis{display:inline-flex;flex-wrap:wrap;gap:3px;margin:0}.badge{font-size:10px;line-height:1.35;padding:0 3px;border:1px solid #ddd;border-radius:2px;color:#444;background:#f7f7f7}.alternative{font-size:10px;color:#666}
summary{font-size:12px;color:#666;cursor:pointer;margin:4px 0}.synonyms{font-size:13px;color:#555}.examples p{font-size:13px;color:#555}.dictionary+ .dictionary{margin-top:0}
</style><main>''' + ''.join(pages) + '</main></html>'
    destination = ROOT / 'build/preview.html'
    destination.write_text(document, encoding='utf-8')
    write_json(ROOT / 'build/preview-report.json', dict(source_sections=counts, samples=samples, canonical_outputs=outputs,
                                                       platform_export=False))
    print(destination)


if __name__ == '__main__':
    main()
