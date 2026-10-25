"""Separate pinned definitions by source while preserving the shipped compact display."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from itertools import groupby
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
from scripts.export_frequency import copy_frequency
from turkdict.common import digest, read_json, write_json
from turkdict.editions import SOURCE_PACKS


def separated_rows(lemma, records, settings):
    for pack in SOURCE_PACKS:
        selected = [record for record in records if record[0] == pack['source']]
        if not selected:
            continue
        local = dict(settings, source_order={pack['language']: [pack['source']]},
                     kenet_full_entries=pack['source'] == 'kenet', source_headings=False)
        body = render_definition(lemma, pack['language'], selected, local)
        if body:
            yield pack, [lemma, '', '', '', 0, [structured(body)], -1, '']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'build/releases/2026-10-05-yomitan-frequency-compact')
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--export-report', type=Path, default=ROOT/'build/yomitan-dictionary-plus-corpus/report.json')
    args = parser.parse_args()
    source, dest = args.source.resolve(), args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    report = read_json(source/'release-report.json')
    export = read_json(args.export_report)
    path = Path(export['source_build']['normalize'])/'definitions.sqlite3'
    if digest(path) != report['dictionary_expansion']['definitions_sha256']:
        raise ValueError('Canonical definitions changed')
    for name, info in report['files'].items():
        if digest(source/name) != info['sha256']:
            raise ValueError('Source archive changed: ' + name)
    dest.mkdir(parents=True)
    work = dest/'work'
    work.mkdir()
    settings = read_json(ROOT/'config/build.json')['presentation']
    counts = Counter()
    lexical_kinds = defaultdict(Counter)
    old = {language: iter(banks(source/('turkish-'+kind+'.zip')))
           for language, kind in [('tr', 'mono'), ('en', 'bilingual')]}
    checked = Counter()
    with ExitStack() as stack:
        streams = {p['kind']: stack.enter_context((work/(p['kind']+'.jsonl')).open('w', encoding='utf-8', newline='\n')) for p in SOURCE_PACKS}
        db = stack.enter_context(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True))
        audit = stack.enter_context(sqlite3.connect(work/'headwords.sqlite3'))
        audit.execute('CREATE TABLE headwords(word TEXT,kind TEXT,PRIMARY KEY(word,kind)) WITHOUT ROWID')
        rows = db.execute('SELECT key,id,language,senses,spelling FROM entries ORDER BY key,id')
        for lemma, group in groupby(rows, key=lambda row: row[0]):
            records = [(eid.split(':')[0], json.loads(raw), spelling, language) for _, eid, language, raw, spelling in group]
            for language in ('tr', 'en'):
                selected = [(src, senses, spelling) for src, senses, spelling, lang in records if lang == language]
                body = render_definition(lemma, language, selected, settings)
                if not body:
                    continue
                expected = [lemma, '', '', '', 0, [structured(body)], -1, '']
                shipped = next(old[language], None)
                if shipped != expected:
                    raise ValueError('Shipped content does not match canonical compact display: ' + lemma)
                checked[language] += 1
            output = list(separated_rows(lemma, [(src, senses, spelling) for src, senses, spelling, _ in records], settings))
            for pack, row in output:
                streams[pack['kind']].write(json.dumps(row, ensure_ascii=False)+'\n')
                audit.execute('INSERT INTO headwords VALUES(?,?)', (lemma, pack['kind']))
                counts[pack['kind']] += 1
                lexical_kinds[pack['kind']]['phrases' if any(c.isspace() for c in lemma) else 'single_tokens'] += 1
            if (checked['tr']+checked['en']) % 100000 == 0 and checked:
                print(f'Checked {sum(checked.values()):,} existing headwords', flush=True)
        if any(next(iterator, None) is not None for iterator in old.values()):
            raise ValueError('Existing headwords were omitted')
        audit.commit()
        union = {language: audit.execute('SELECT count(DISTINCT word) FROM headwords WHERE kind IN (?,?)',
                 tuple(p['kind'] for p in SOURCE_PACKS if p['language'] == language)).fetchone()[0] for language in ('tr', 'en')}
        if union != dict(checked):
            raise ValueError('Definition headword union changed: ' + str(union))
        forms_db = stack.enter_context(sqlite3.connect((Path(args.export_report).parent/'forms.sqlite3').resolve().as_uri()+'?mode=ro', uri=True))
        lookup_cases = []
        words = ['ev', 'kitap', 'y\u00fcz', 'gitmek', 'evde', 'kitab\u0131', 'geliyorum', 'okumad\u0131m',
                 'gerekti\u011fini', 'deyip', 'evlerimizden', 'yapt\u0131klar\u0131m\u0131zdan',
                 'g\u00f6zl\u00fck\u00e7\u00fc', 'orjinal', 'nerden', 'a\u011fla\u015ft\u0131lar', 'koskoca', 'evlersin', 'yinede',
                 'qzxqzx', 'evlerlerden', 'kitaplarden', 'geldimiz']
        groups = [[p] for p in SOURCE_PACKS] + [[SOURCE_PACKS[0], SOURCE_PACKS[1]], SOURCE_PACKS]
        for packs in groups:
            for word in words:
                found = forms_db.execute('SELECT payload FROM forms WHERE surface=?', (word,)).fetchone()
                targets = [word] + ([lemma for lemma, _ in json.loads(found[0])[5]] if found else [])
                supported = {target: {kind for kind, in audit.execute('SELECT kind FROM headwords WHERE word=?', (target,))} for target in targets}
                titles = {p['kind']: p['title'] for p in packs}
                expected = list(dict.fromkeys(target for target in targets if supported[target] & titles.keys()))
                dictionaries = sorted({titles[kind] for target in expected for kind in supported[target] & titles.keys()})
                lookup_cases.append(dict(word=word, names=list(titles.values()), expectedTerms=expected, expectedDictionaries=dictionaries))
    revision = '2026-10-09-source-separated'
    files = {}
    for pack in SOURCE_PACKS:
        kind = pack['kind']
        name = 'turkish-'+kind+'.zip'
        with (work/(kind+'.jsonl')).open(encoding='utf-8') as stream:
            count = archive(dest/name, pack['title'], (json.loads(line) for line in stream), revision,
                            index_metadata={'targetLanguage': pack['language']})
        if count != counts[kind]:
            raise ValueError('Missing source definitions')
        files[name] = {}
        print(f'{pack["title"]}: {count:,} headwords', flush=True)
    for kind in ('forms', 'frequency', 'etymology'):
        name = 'turkish-'+kind+'.zip'
        if kind == 'frequency':
            copy_frequency(source/name, dest/name)
        else:
            shutil.copy2(source/name, dest/name)
        files[name] = {}
    sys.path.insert(0, str(ROOT/'tools/prototype-validation'))
    import fastjsonschema
    schema_dir = ROOT/'research/yomitan/ext/data/schemas'
    validate_index = fastjsonschema.compile(read_json(schema_dir/'dictionary-index-schema.json'))
    validators = {'term_bank_': fastjsonschema.compile(read_json(schema_dir/'dictionary-term-bank-v3-schema.json')),
                  'term_meta_bank_': fastjsonschema.compile(read_json(schema_dir/'dictionary-term-meta-bank-v3-schema.json'))}
    for name in files:
        with zipfile.ZipFile(dest/name) as archive_file:
            if archive_file.testzip() is not None:
                raise ValueError('Corrupt output ZIP: ' + name)
            validate_index(json.loads(archive_file.read('index.json')))
            for member in archive_file.namelist():
                for prefix, validate in validators.items():
                    if member.startswith(prefix):
                        validate(json.loads(archive_file.read(member)))
            files[name] = dict(sha256=digest(dest/name), download_bytes=(dest/name).stat().st_size,
                              uncompressed_json_bytes=sum(i.file_size for i in archive_file.infolist() if i.filename.endswith('.json')))
        print('Validated '+name, flush=True)
    report.pop('browser_validation', None)
    report.update(revision=revision, definition_packs=SOURCE_PACKS, files=files, status='separated; native validation pending',
                  ready_to_import=False, source_separation=dict(previous_content_verified=dict(checked),
                      union_headwords=union, compact_wiktionary_preserved=True, kenet_all_senses=True,
                      shared_archives_unchanged=['turkish-forms.zip', 'turkish-etymology.zip'],
                      frequency_rank_banks_unchanged=True, frequency_title='Frequency'),
                  definitions=dict(counts, forms=report['definitions']['forms'], etymology=report['definitions']['etymology']),
                  definition_kinds=dict(lexical_kinds), lookup_expectations=lookup_cases)
    manifest = read_json(source/'source-manifest.json')
    manifest.update(definition_packs=SOURCE_PACKS, presentation=dict(settings, kenet_full_entries=True),
                    separation_code={name: digest(ROOT/name) for name in ('scripts/separate_dictionaries.py', 'scripts/preview.py', 'turkdict/editions.py')})
    write_json(dest/'source-manifest.json', manifest)
    write_json(dest/'release-report.json', report)
    (dest/'INSTALL.txt').write_text('Import turkish-forms.zip and any of turkish-tdk.zip, turkish-wiktionary.zip, turkish-tureng.zip, turkish-kenet.zip.\n'
        'Remove previous Turkish dictionaries first. Select Turkish and Word search resolution; enable dictionary deinflections for Turkish Forms.\n'
        'turkish-frequency.zip and turkish-etymology.zip are optional. Lower frequency ranks mean more common spellings.\n', encoding='utf-8')
    checksums(dest)


if __name__ == '__main__':
    main()
