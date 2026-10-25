"""Compare a completed book benchmark with pinned Wiktionary and shipped Forms."""
import argparse
from collections import Counter
import csv
import html
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.measure_book_coverage import coverage
from turkdict.common import digest, read_json, write_json


def read_only(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def compare(directory):
    report = read_json(directory / 'report.json')
    details = read_json(directory / 'forms.json')
    export = ROOT / 'build/yomitan-dictionary-plus-corpus'
    exported = read_json(export / 'report.json')
    definitions = Path(exported['source_build']['normalize']) / 'definitions.sqlite3'
    if digest(definitions) != exported['dictionary_expansion']['definitions_sha256']:
        raise ValueError('Canonical definitions differ from the shipped Forms build')
    source = next(s for s in read_json(ROOT / 'config/sources.lock.json') if s['parser'] == 'wty-v1')
    source_path = ROOT / source['path']
    if digest(source_path) != source['sha256']:
        raise ValueError('Pinned Wiktionary pack checksum mismatch')
    with read_only(definitions) as db:
        heads = {r[0] for r in db.execute("SELECT key FROM entries WHERE id LIKE 'wty:%'")}
        # A form-only row counts only when it resolves to a definition in this
        # same pack. An unresolved redirect is not a successful dictionary result.
        baseline = heads | {surface for surface, target in db.execute('SELECT surface,target FROM redirects')
                            if target in heads}
    mapped = set(heads)
    with read_only(export / 'forms.sqlite3') as db:
        for row in details:
            surface = row['surface']
            found = db.execute('SELECT payload FROM forms WHERE surface=?', (surface,)).fetchone()
            if found and any(lemma in heads for lemma, labels in json.loads(found[0])[5]):
                mapped.add(surface)
    both = {row['surface'] for row in details if row['covered']['both']}
    hits = {'wiktionary_pack': baseline, 'wiktionary_with_shipped_forms': mapped,
            'combined_dictionaries': both}
    counts = Counter({row['surface']: row['story_count'] for row in details if row['story_count']})
    metrics = {name: coverage(counts, words) for name, words in hits.items()}
    for metric, value in metrics['combined_dictionaries'].items():
        if report['scopes']['story']['both'][metric] != value:
            raise ValueError('Combined results disagree with the existing benchmark')
    if any(report['native_validation']['index_disagreements'].values()):
        raise ValueError('Native lookup validation has disagreements')
    result = dict(book=report['book'], scope='story', metrics=metrics,
        method=report['method'], comparison_method=(
            'All columns use identical normalized narrative tokens. Wiktionary pack: '
            'exact canonical Wiktionary headword or source redirect to a definition '
            'in the same pinned pack. Unresolved redirects do not count. '
            'Wiktionary with shipped forms: exact Wiktionary definition or a '
            'shipped Forms alternative whose lemma has a Wiktionary definition. '
            'Combined: native-verified exported monolingual/bilingual index hits. '
            'No runtime morphology or book-fed changes. Optional etymology and '
            'morphology packs are excluded. Lookup availability is not contextual accuracy.'),
        inputs=dict(wiktionary=dict(path=str(source_path.resolve()), sha256=source['sha256'],
                                   revision=source['snapshot']),
                    definitions=dict(path=str(definitions), sha256=digest(definitions)),
                    forms=dict(path=str((export / 'forms.sqlite3').resolve()),
                               sha256=digest(export / 'forms.sqlite3')),
                    dictionary_indexes=report['dictionary_indexes']),
        native_validation=report['native_validation'])
    return result, counts, hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Completed measure_book_coverage output')
    args = parser.parse_args()
    result, counts, hits = compare(args.directory)
    write_json(args.directory / 'comparison.json', result)
    names = list(result['metrics'])
    labels = ['Wiktionary pack', 'Same Wiktionary definitions, with shipped form mappings',
              'Two dictionaries combined']
    rows = [
        ('Different word forms recognized', 'covered_spellings', 'spellings'),
        ('Percentage recognized', 'spelling_percent', None),
        ('Different word forms with no result', 'missing_spellings', None),
        ('Words recognized, including repetitions', 'covered_tokens', 'tokens'),
        ('Percentage recognized, including repetitions', 'token_percent', None),
        ('Words with no result, including repetitions', 'missing_tokens', None),
    ]
    table = [['Metric', *labels]]
    for label, field, denominator in rows:
        cells = []
        for name in names:
            data = result['metrics'][name]
            if denominator:
                cell = f'{data[field]:,} / {data[denominator]:,}'
            elif field.endswith('percent'):
                cell = f'{data[field]:.2f}%'
            else:
                cell = f'{data[field]:,}'
            cells.append(cell)
        table.append([label, *cells])
    with (args.directory / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as out:
        csv.writer(out).writerows(table)
    with (args.directory / 'comparison-forms.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(['surface', 'story_count', *names])
        for word in sorted(counts):
            writer.writerow([word, counts[word], *(word in hits[name] for name in names)])
    page = args.directory / 'report.html'
    original = page.read_text(encoding='utf-8')
    start, end = '<!-- dictionary comparison -->', '<!-- end dictionary comparison -->'
    if start in original:
        left, rest = original.split(start, 1)
        original = left + rest.split(end, 1)[1]
    rendered = ''.join('<tr>' + ''.join(f'<td>{html.escape(cell)}</td>' for cell in row) + '</tr>'
                       for row in table[1:])
    fragment = (start + '<h2>Dictionary comparison</h2><table><tr>' +
                ''.join(f'<th>{html.escape(cell)}</th>' for cell in table[0]) + '</tr>' + rendered +
                '</table><p>' + html.escape(result['comparison_method']) +
                '</p><p><a href="comparison.json">Comparison evidence</a>; '
                '<a href="comparison.csv">Comparison table CSV</a>; '
                '<a href="comparison-forms.csv">Every narrative form and its lookup results</a>.</p>' + end)
    position = original.index('<table>')
    page.write_text(original[:position] + fragment + original[position:], encoding='utf-8')
    print(json.dumps(result['metrics'], indent=2))


if __name__ == '__main__':
    main()
