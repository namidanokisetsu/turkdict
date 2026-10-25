"""Export baseline Yomitan dictionaries and previews from the same source content."""
from collections import defaultdict
from html.parser import HTMLParser
import html
import json
from itertools import islice
from pathlib import Path
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import read_json, write_json
from turkdict.presentation import compact_label, factor_paths
from scripts.preview import source_content, morphology_rows, SOURCE


class Structured(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.content = []
        self.stack = [self.content]

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        node = {'tag': 'span' if tag == 'i' else tag if tag in ('span', 'div', 'ol', 'ul', 'li', 'details', 'summary') else 'div', 'content': []}
        styles = {'section': {'marginTop': '0.45em'}, 'p': {'margin': '0.15em 0'},
                  'ol': {'margin': '0.15em 0', 'paddingLeft': '1.25em'},
                  'li': {'margin': '0.2em 0'}, 'i': {'fontStyle': 'italic'},
                  'summary': {'fontSize': '0.8em', 'cursor': 'pointer'},
                  'details': {'marginTop': '0.35em', 'marginLeft': '0', 'paddingLeft': '0'}}
        if tag in styles:
            node['style'] = styles[tag]
        if attrs.get('class') in ('senses', 'sense'):
            node['style'] = {'marginLeft': '0', 'paddingLeft': '0'}
        if tag == 'h4':
            node['style'] = {'fontSize': '0.75em', 'fontWeight': 'bold'}
        if tag == 'b':
            node['tag'] = 'span'
            node['style'] = {'fontWeight': 'bold'}
        if attrs.get('class') == 'badge':
            node['style'] = {'fontSize': '0.7em'}
        if attrs.get('class') in ('examples', 'synonyms', 'translation', 'sense-context'):
            node['style'] = {'fontSize': '0.9em', 'marginTop': '0.1em'}
        if attrs.get('class') == 'synonyms':
            node['style'].update(fontSize='0.8em', fontWeight='normal')
        if attrs.get('class') == 'usage':
            node['style'] = {'fontSize': '0.8em', 'fontStyle': 'italic'}
        if attrs.get('class') == 'lexical-group':
            node['style'] = {'marginTop': '0.5em'}
        if attrs.get('class') == 'entry-headword':
            node['style'] = {'fontWeight': 'bold', 'marginTop': '0.3em'}
        if node['tag'] in ('div', 'details'):
            node.setdefault('style', {}).update(marginLeft='0', paddingLeft='0')
        self.stack[-1].append(node)
        self.stack.append(node['content'])

    def handle_endtag(self, tag):
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].append(data)


def structured(fragment):
    parser = Structured()
    parser.feed(fragment)
    return {'type': 'structured-content', 'content': parser.content}


def archive(path, title, rows, revision='prototype-v8', *, bank_prefix='term_bank', index_metadata=None):
    count = 0
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        def write(name, value):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, json.dumps(value, ensure_ascii=False))
        metadata = dict(title=title, revision=revision, format=3,
                        sequenced=False, sourceLanguage='tr')
        metadata.update(index_metadata or {})
        write('index.json', metadata)
        iterator = iter(rows)
        bank = 1
        while chunk := list(islice(iterator, 5000)):
            write(f'{bank_prefix}_{bank}.json', chunk)
            count += len(chunk)
            bank += 1
    return count


def render_definition(lemma, language, records, settings):
    body = ''
    local_settings = dict(settings, tdk_sense_texts=[s.get('text', '') for src, items, spelling in records if src == 'tdk' for s in items])
    for source in settings['source_order'][language]:
        senses = [s for src, items, spelling in records if src == source for s in items]
        if not senses:
            continue
        groups = [(items, spelling) for src, items, spelling in records if src == source]
        if source in ('tdk', 'wty') and len(groups) > 1:
            fragments = []
            for group, spelling in groups:
                part = source_content(source, group, lemma,
                    dict(local_settings, visible_senses=1, sense_start=1, number_senses=False))
                if part:
                    heading = '<div class="entry-headword">' + html.escape(spelling) + '</div>'
                    fragments.append('<div class="lexical-group">' + heading + part + '</div>')
            fragment = ''.join(fragments)
        else:
            fragment = source_content(source, senses, lemma, local_settings)
        if fragment:
            heading = '<h4>' + SOURCE[source] + '</h4>' if settings.get('source_headings', True) else ''
            body += '<section>' + heading + fragment + '</section>'
    return body


def compact_alternatives(alternatives):
    """Keep every reading, but express their shared grammar only once."""
    grouped = defaultdict(list)
    for lemma, labels in alternatives:
        labels = [compact_label(label) for label in labels]
        if labels not in grouped[lemma]:
            grouped[lemma].append(labels)
    result = []
    for lemma, paths in grouped.items():
        if len(paths) == 1:
            result.append([lemma, paths[0]])
            continue
        prefix, paths, suffix = factor_paths(paths)
        middle = ' OR '.join(' · '.join(p) if p else 'unmarked' for p in paths)
        result.append([lemma, prefix + ['(' + middle + ')'] + suffix])
    return result


def main(latest=None, destination=None, revision='prototype-v8', corpus=False):
    latest = latest or read_json(ROOT / 'build/macocu-baseline-latest.json')['outputs']
    settings = read_json(ROOT / 'config/build.json')['presentation']
    db = sqlite3.connect(Path(latest['normalize']) / 'definitions.sqlite3')
    entries = {}
    spellings = {}
    mappings = defaultdict(list)
    feature_pool = {}
    exact_provenance = {'raw': 'exact'}
    analyzed_provenance = {'raw': 'analysis'}
    with (Path(latest['analyze']) / 'mappings.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            surface = record['surface']
            if corpus:
                # Raw evidence remains in the canonical JSONL; the renderer
                # only needs shared feature sequences and an exact-match flag.
                feature_key = json.dumps(record['features'], ensure_ascii=False, sort_keys=True)
                shared_features = feature_pool.setdefault(feature_key, record['features'])
                record = dict(target_lemma_id=record['target_lemma_id'], features=shared_features,
                              provenance=exact_provenance if record['provenance']['raw'] == 'exact' else analyzed_provenance)
            mappings[surface].append(record)
            eid = record['target_lemma_id']
            if eid not in entries:
                lemma, language, senses, spelling = db.execute('SELECT key,language,senses,spelling FROM entries WHERE id=?', (eid,)).fetchone()
                entries[eid] = (lemma, language, senses)
                spellings[eid] = spelling
    # Preserve the existing prototype baseline. General recovery and coverage
    # expansion belong to the corpus-based build, not this presentation pass.
    recovery_rules = [] if corpus else read_json(ROOT / 'config/wiktionary-recovery.json')['entries']
    failures = set() if corpus else {r['surface'] for r in read_json(ROOT / 'build/macocu-baseline-failures.json')['forms']}
    recovery_report = []
    for rule in recovery_rules:
        surface, target = rule['surface'], rule['target']
        if surface not in failures or surface in mappings:
            raise ValueError('Recovery must be an unlinked baseline form: ' + surface)
        redirects = [(json.loads(f), json.loads(p)) for f, p in db.execute(
            'SELECT features,provenance FROM redirects WHERE surface=? AND target=?', (surface, target))]
        matched = [(f, p) for f, p in redirects if f['raw'][1] == rule['source_labels']]
        if not matched:
            raise ValueError('Pinned Wiktionary recovery evidence changed: ' + surface)
        targets = list(db.execute('SELECT id,key,language,senses,spelling FROM entries WHERE key=?', (target,)))
        if not targets:
            raise ValueError('Recovery definition missing: ' + target)
        for eid, lemma, language, senses, spelling in targets:
            entries[eid] = (lemma, language, senses)
            spellings[eid] = spelling
            mappings[surface].append(dict(surface=surface, target_lemma_id=eid,
                features=[dict(id='WiktionaryRecovery', label=label) for label in rule['labels']],
                provenance=dict(raw='wiktionary recovery', analysis_lemma=target)))
        recovery_report.append(dict(**rule, provenance=matched[0][1], target_records=len(targets)))
    candidates = []
    for surface in sorted(failures):
        for target, features in db.execute('SELECT target,features FROM redirects WHERE surface=?', (surface,)):
            if db.execute('SELECT 1 FROM entries WHERE key=?', (target,)).fetchone():
                labels = json.loads(features)['raw'][1]
                candidate = dict(surface=surface, target=target, source_labels=labels,
                    enabled=any(r['surface'] == surface and r['target'] == target and r['source_labels'] == labels for r in recovery_rules))
                if candidate not in candidates:
                    candidates.append(candidate)
    recovery = dict(enabled=recovery_report, candidates=candidates)
    selected_lemmas = {value[0] for value in entries.values()}
    if corpus:
        # Canonical release mappings need only a representative target ID;
        # retain every source record/homograph for its lexical spelling.
        for lemma in sorted(selected_lemmas):
            for eid, language, senses, spelling in db.execute('SELECT id,language,senses,spelling FROM entries WHERE key=? ORDER BY id', (lemma,)):
                entries[eid] = (lemma, language, senses)
                spellings[eid] = spelling
    etymology_senses = defaultdict(list)
    for lemma, raw in db.execute("SELECT key,senses FROM entries WHERE id LIKE 'wty:%'"):
        if lemma in selected_lemmas:
            etymology_senses[lemma].extend(json.loads(raw))
    db.close()
    by_lemma = defaultdict(list)
    for eid, (lemma, language, senses) in entries.items():
        by_lemma[(lemma, language)].append((eid.split(':')[0], json.loads(senses), spellings[eid]))
    destination = destination or ROOT / 'build/prototype'
    destination.mkdir(parents=True, exist_ok=True)
    rendered = {}
    counts = {}
    etymology_rows = []
    for lemma, senses in sorted(etymology_senses.items()):
        fragment = source_content('wty', senses, lemma, dict(settings, etymology_only=True))
        if fragment:
            etymology_rows.append([lemma, '', '', '', 0, [structured(fragment)], -1, ''])
    archive(destination / 'turkish-etymology.zip', 'Turkish Etymology', etymology_rows, revision)
    counts['etymology'] = len(etymology_rows)
    for language, name in [('tr', 'mono'), ('en', 'bilingual')]:
        rows = []
        for (lemma, lang), records in sorted(by_lemma.items()):
            if lang != language:
                continue
            body = render_definition(lemma, language, records, settings)
            if body:
                rendered[(lemma, language)] = body
                rows.append([lemma, '', '', '', 0, [structured(body)], -1, ''])
        archive(destination / f'turkish-{name}.zip', 'Turkish Monolingual' if language == 'tr' else 'Turkish Bilingual', rows, revision)
        counts[name] = len(rows)
    forms = []
    for surface, records in sorted(mappings.items()):
        alternatives = []
        for record in records:
            lemma = entries[record['target_lemma_id']][0]
            if not any((lemma, lang) in rendered for lang in ('tr', 'en')) or surface == lemma:
                continue
            # Reuse precisely the learner labels from the visual preview.
            fragment = morphology_rows([record])
            parser = Structured()
            parser.feed(fragment)
            def texts(nodes):
                for node in nodes:
                    if isinstance(node, str):
                        if node.strip():
                            yield node.strip()
                    else:
                        yield from texts(node.get('content', []))
            labels = list(texts(parser.content)) or ['inflected form']
            alternative = [lemma, labels]
            if alternative not in alternatives:
                alternatives.append(alternative)
        if alternatives:
            forms.append([surface, '', '', '', 0, compact_alternatives(alternatives), -1, ''])
    archive(destination / 'turkish-forms.zip', 'Turkish Forms', forms, revision)
    counts['forms'] = len(forms)
    fixture = []
    for path in [destination / f'turkish-{name}.zip' for name in ('bilingual', 'etymology', 'forms', 'mono')]:
        with zipfile.ZipFile(path) as z:
            title = json.loads(z.read('index.json'))['title']
            for bank in z.namelist():
                if bank.startswith('term_bank_'):
                    fixture.extend(dict(term=r[0], dictionary=title, definitions=r[5]) for r in json.loads(z.read(bank)))
    write_json(destination / 'lookup-fixture.json', fixture)
    css = '''body{font:15px/1.45 system-ui;background:#f5f5f3;color:#242424;margin:0}main{max-width:620px;margin:24px auto;padding:0 16px}article{background:white;border:1px solid #ddd;border-radius:7px;padding:14px;margin:14px 0}h2{font-size:20px;margin:0 0 3px}.lemma{font-size:15px;font-weight:400;color:#666}h4{font-size:11px;color:#777;margin:7px 0 3px}section{border-top:1px solid #eee;margin-top:7px}p,li,.sense{margin:3px 0}ol{padding-left:22px;margin:3px 0}.morph,.analysis{display:inline-flex;flex-wrap:wrap;gap:3px;align-items:center}.morph{margin-bottom:5px}.badge{font-size:10px;line-height:1.35;padding:0 3px;background:#f6f6f6;border:1px solid #ddd;border-radius:2px}.alternative{font-size:10px;color:#777;margin:0 4px}summary{font-size:12px;color:#666;cursor:pointer}.synonyms,.examples{font-size:13px;color:#555}.sense-context{font-size:12px;color:#666}.usage{font-size:11px;font-style:italic;color:#777}nav{display:flex;gap:14px}a{color:#365575}'''
    examples = [('evlerden', 'ev'), ('kitabı', 'kitap'), ('geliyorum', 'gelmek')]
    for language, name in [('tr', 'mono'), ('en', 'bilingual')]:
        cards = []
        for wanted, lemma in examples:
            surface = wanted if wanted in mappings else next((w for w, records in mappings.items() if w != lemma and any(entries[r['target_lemma_id']][0] == lemma for r in records)), lemma)
            records = [r for r in mappings.get(surface, []) if entries[r['target_lemma_id']][0] == lemma]
            body = rendered.get((lemma, language), '')
            cards.append('<article><h2>' + html.escape(surface) + ' <span class="lemma">→ ' + html.escape(lemma) + '</span></h2>' + morphology_rows(records) + body + '</article>')
        document = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Turkish ' + name + '</title><style>' + css + '</style><main>' + ''.join(cards) + '</main>'
        (destination / f'{name}.html').write_text(document, encoding='utf-8')
    (destination / 'README.txt').write_text(('Corpus-backed Yomitan release candidate. ' if corpus else 'Yomitan prototype v8, retained 15,000-form baseline. ') + 'Replace previous imports with turkish-forms.zip and either or both definition ZIPs; etymology is optional. Enable dictionary deinflections for Turkish Forms, select Turkish, and use Word search resolution. HTML previews illustrate our intended layout; extension styling differs. Alternative root entries remain available because a static dictionary cannot disambiguate sentence context. See report.json and the release readiness report for measured limits.\n', encoding='utf-8')
    write_json(destination / 'wiktionary-recovery-audit.json', recovery)
    write_json(destination / 'report.json', dict(entries=counts, source_build=latest, macocu_included=corpus, actual_extension_import_tested=False,
                                               revision=revision, temporary_recovered_forms=len(recovery_report),
                                               linked_forms=len(mappings)))
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
