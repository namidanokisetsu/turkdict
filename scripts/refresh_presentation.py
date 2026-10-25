"""Rebuild definition presentation while preserving a validated release's coverage."""
import argparse
from itertools import groupby, zip_longest
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.export_prototype import archive, render_definition, structured
from scripts.package_yomitan import banks, checksums
from scripts.export_frequency import export_frequency
from turkdict.presentation import compact_label
from turkdict.common import digest, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'build/releases/2026-10-01-complete')
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--export-report', type=Path, default=ROOT/'build/yomitan-dictionary-plus-corpus/report.json')
    parser.add_argument('--corpus', type=Path, default=ROOT/'build/yomitan-full-inventory')
    args = parser.parse_args()
    dest = args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    report = read_json(args.source/'release-report.json')
    export = read_json(args.export_report)
    definitions = Path(export['source_build']['normalize'])/'definitions.sqlite3'
    if digest(definitions) != report['dictionary_expansion']['definitions_sha256']:
        raise ValueError('Canonical definitions changed')
    for name, info in report['files'].items():
        if digest(args.source/name) != info['sha256']:
            raise ValueError('Source hash mismatch: ' + name)
    dest.mkdir(parents=True)
    settings = read_json(ROOT/'config/build.json')['presentation']
    revision = '2026-10-05-frequency-compact'
    with sqlite3.connect(definitions.resolve().as_uri()+'?mode=ro', uri=True) as db:
        for kind, language, title in [('mono', 'tr', 'Turkish Monolingual'), ('bilingual', 'en', 'Turkish Bilingual')]:
            name = 'turkish-' + kind + '.zip'
            def rows():
                cursor = db.execute('SELECT key,id,senses,spelling FROM entries WHERE language=? ORDER BY key,id', (language,))
                def regenerated():
                    for lemma, group in groupby(cursor, key=lambda r: r[0]):
                        records = [(eid.split(':')[0], json.loads(raw), spelling) for _, eid, raw, spelling in group]
                        body = render_definition(lemma, language, records, settings)
                        if body:
                            yield [lemma, '', '', '', 0, [structured(body)], -1, '']
                for old, new in zip_longest(banks(args.source/name), regenerated()):
                    if old is None or new is None or old[:5] != new[:5] or old[6:] != new[6:]:
                        raise ValueError('Definition inventory changed')
                    yield new
            count = archive(dest/name, title, rows(), revision)
            print(f'{kind}: {count:,} headwords preserved', flush=True)
        def forms():
            for row in banks(args.source/'turkish-forms.zip'):
                # Only display strings change; term order and every target survive.
                row[5] = [[lemma, [compact_label(label) if ' OR ' not in label or
                                  (label.startswith('(') and label.endswith(')')) else
                                  '(' + compact_label(label) + ')' for label in labels]]
                          for lemma, labels in row[5]]
                yield row
        count = archive(dest/'turkish-forms.zip', 'Turkish Forms', forms(), revision)
        if count != report['inflected_spellings']:
            raise ValueError('Forms inventory changed')
        print(f'forms: {count:,} spellings preserved', flush=True)
        for name in ('turkish-etymology.zip', 'source-manifest.json'):
            shutil.copy2(args.source/name, dest/name)
    shutil.copy2(ROOT/'config/yomitan-popup.css', dest/'yomitan-popup.css')
    frequency = export_frequency(dest, definitions, args.corpus, revision)
    (dest/'INSTALL.txt').write_text(
        'Import turkish-forms.zip plus turkish-mono.zip, turkish-bilingual.zip, or both.\n'
        'Import turkish-frequency.zip for frequency ranks; smaller numbers mean more frequent.\n'
        'Ranks describe the displayed headword spelling; missing data has no rank. Etymology is optional.\n'
        'Remove previous Turkish dictionary imports before importing replacements.\n'
        'Select Turkish, enable dictionary deinflections for Turkish Forms, and use Word search resolution.\n'
        'Optional sorting: choose Frequency and ascending frequency order.\n'
        'Morphology uses linguistic labels; parentheses group alternative readings.\n', encoding='utf-8')
    report.pop('browser_validation', None)
    report.update(revision=revision, ready_to_import=False, status='rebuilt; native validation pending',
                  presentation=dict(settings, morphology_labels='compact linguistic terms; scoped alternatives'),
                  frequency=frequency,
                  canonical_forms_sha256=digest(Path(args.export_report).parent/'turkish-forms.zip'))
    report['files']['turkish-frequency.zip'] = {}
    sys.path.insert(0, str(ROOT/'tools/prototype-validation'))
    import fastjsonschema
    schemas = ROOT/'research/yomitan/ext/data/schemas'
    validate_index = fastjsonschema.compile(read_json(schemas/'dictionary-index-schema.json'))
    validators = {prefix: fastjsonschema.compile(read_json(schemas/filename)) for prefix, filename in
                  [('term_bank_', 'dictionary-term-bank-v3-schema.json'),
                   ('term_meta_bank_', 'dictionary-term-meta-bank-v3-schema.json')]}
    for name, info in report['files'].items():
        with zipfile.ZipFile(dest/name) as z:
            if z.testzip() is not None:
                raise ValueError('Corrupt output archive: ' + name)
            validate_index(json.loads(z.read('index.json')))
            for member in z.namelist():
                for prefix, validate in validators.items():
                    if member.startswith(prefix):
                        validate(json.loads(z.read(member)))
            info.update(sha256=digest(dest/name), download_bytes=(dest/name).stat().st_size,
                        uncompressed_json_bytes=sum(i.file_size for i in z.infolist() if i.filename.endswith('.json')))
        print('Validated ' + name, flush=True)
    manifest = read_json(dest/'source-manifest.json')
    manifest['presentation_code'] = {name: digest(ROOT/name) for name in
        ('scripts/refresh_presentation.py', 'scripts/export_prototype.py', 'scripts/preview.py',
         'scripts/export_frequency.py', 'turkdict/presentation.py', 'config/build.json')}
    manifest['presentation'] = settings
    manifest['frequency'] = frequency
    write_json(dest/'source-manifest.json', manifest)
    write_json(dest/'release-report.json', report)
    checksums(dest)


if __name__ == '__main__':
    main()
