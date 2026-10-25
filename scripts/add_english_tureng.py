"""Add the pinned Tureng English-to-Turkish section to an existing Yomitan edition."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.export_prototype import archive, render_definition, structured
from scripts.export_frequency import copy_frequency
from scripts.package_yomitan import checksums
from turkdict.common import digest, read_json, write_json
from turkdict.editions import ENGLISH_TURENG, definition_packs, pack_basename


def english_key(text):
    return unicodedata.normalize('NFC', text).translate(str.maketrans('\u2019\u02bc', "''")).lower().strip()


def english_entries(data):
    """Keep source translation order, merging only equivalent lookup spellings."""
    entries = {}
    counts = Counter(records=len(data), rejected_records=0, source_translation_items=0)
    for spelling, values in data.items():
        if not isinstance(spelling, str) or not isinstance(values, list):
            raise ValueError('Unexpected Tureng record')
        word = english_key(spelling)
        meanings = [value for value in values if isinstance(value, str) and value.strip()]
        if not word or not meanings:
            counts['rejected_records'] += 1
            continue
        if '\0' in word or any('\0' in value for value in meanings):
            raise ValueError('NUL in Tureng record')
        counts['source_translation_items'] += len(meanings)
        entries.setdefault(word, {}).update(dict.fromkeys(meanings))
    return entries, counts


def english_row(word, meanings, settings):
    body = render_definition(word, 'tr', [('tureng', [dict(text=text) for text in meanings], word)],
                             dict(settings, source_order={'tr': ['tureng']}, source_headings=False))
    return [word, '', '', '', 0, [structured(body)], -1, '']


def rename_dictionary(source, destination, title):
    """Change the displayed title while verifying every other member byte for byte."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(destination, 'w') as new:
        for info in old.infolist():
            content = old.read(info.filename)
            if info.filename == 'index.json':
                index = json.loads(content)
                index['title'] = title
                content = json.dumps(index, ensure_ascii=False).encode('utf-8')
            new.writestr(info, content)
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(destination) as new:
        if old.namelist() != new.namelist() or new.testzip() is not None:
            raise ValueError('Dictionary title rewrite changed archive inventory')
        for member in old.namelist():
            if member != 'index.json' and old.read(member) != new.read(member):
                raise ValueError('Dictionary title rewrite changed content')
        old_index, new_index = json.loads(old.read('index.json')), json.loads(new.read('index.json'))
        old_index['title'] = title
        if old_index != new_index:
            raise ValueError('Dictionary metadata changed beyond title')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'build/releases/2026-10-09-yomitan-source-separated')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    source, dest = args.source.resolve(), args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    report = read_json(source/'release-report.json')
    if any(pack_basename(pack) == ENGLISH_TURENG['basename'] for pack in definition_packs(report)):
        raise ValueError('English Tureng already exists')
    for name, info in report['files'].items():
        if digest(source/name) != info['sha256']:
            raise ValueError('Source archive changed: ' + name)
    snapshot = next(item for item in read_json(ROOT/'config/sources.lock.json') if item['id'] == 'tureng')
    raw = ROOT/snapshot['path']
    if digest(raw) != snapshot['sha256']:
        raise ValueError('Tureng snapshot changed')
    with zipfile.ZipFile(raw) as zipped:
        if zipped.testzip() is not None:
            raise ValueError('Corrupt Tureng snapshot')
        data = json.loads(zipped.read('tureng_database.json'))['ing']
    entries, counts = english_entries(data)
    del data
    lexical_kinds = Counter('phrases' if any(c.isspace() for c in word) else 'single_tokens' for word in entries)
    words = ['house', 'home', 'book', 'run', 'bank', 'light', 'i', 'internet', 'ice cream', 'look up', 'qzxqzx']
    samples = {word: list(entries.get(word, {})) for word in words}
    settings = read_json(ROOT/'config/build.json')['presentation']
    dest.mkdir(parents=True)
    for name in report['files']:
        if name == 'turkish-tureng.zip':
            rename_dictionary(source/name, dest/name, 'TR-EN Tureng')
        elif name == 'turkish-frequency.zip':
            copy_frequency(source/name, dest/name)
        else:
            shutil.copy2(source/name, dest/name)
    revision = '2026-10-09-english-tureng'
    name = ENGLISH_TURENG['basename'] + '.zip'
    count = archive(dest/name, ENGLISH_TURENG['title'],
                    (english_row(word, entries[word], settings) for word in sorted(entries)), revision,
                    index_metadata={'sourceLanguage': 'en', 'targetLanguage': 'tr'})
    sys.path.insert(0, str(ROOT/'tools/prototype-validation'))
    import fastjsonschema
    schemas = ROOT/'research/yomitan/ext/data/schemas'
    validate_index = fastjsonschema.compile(read_json(schemas/'dictionary-index-schema.json'))
    validate_terms = fastjsonschema.compile(read_json(schemas/'dictionary-term-bank-v3-schema.json'), use_default=False)
    with zipfile.ZipFile(dest/name) as zipped:
        if zipped.testzip() is not None:
            raise ValueError('Corrupt English dictionary')
        validate_index(json.loads(zipped.read('index.json')))
        checked = 0
        for member in zipped.namelist():
            if member.startswith('term_bank_'):
                rows = json.loads(zipped.read(member))
                validate_terms(rows)
                for row in rows:
                    expected = english_row(row[0], entries.pop(row[0]), settings)
                    if row != expected:
                        raise ValueError('English translations changed: ' + row[0])
                    checked += 1
        if entries or checked != count:
            raise ValueError('Missing English entries')
        info = dict(sha256=digest(dest/name), download_bytes=(dest/name).stat().st_size,
                    uncompressed_json_bytes=sum(item.file_size for item in zipped.infolist()))
    for old_name, old_info in report['files'].items():
        if old_name in ('turkish-tureng.zip', 'turkish-frequency.zip'):
            with zipfile.ZipFile(dest/old_name) as zipped:
                old_info.update(sha256=digest(dest/old_name), download_bytes=(dest/old_name).stat().st_size,
                    uncompressed_json_bytes=sum(item.file_size for item in zipped.infolist()))
        elif digest(dest/old_name) != old_info['sha256']:
            raise ValueError('Existing dictionary changed')
    report['files'][name] = info
    for pack in definition_packs(report):
        if pack['kind'] == 'tureng':
            pack['title'] = 'TR-EN Tureng'
    for case in report.get('lookup_expectations', []):
        for field in ('names', 'expectedDictionaries'):
            case[field] = ['TR-EN Tureng' if title == 'Tureng' else title for title in case[field]]
        case['expectedDictionaries'].sort()
    report['definition_packs'] = definition_packs(report) + [ENGLISH_TURENG]
    report['definitions'][ENGLISH_TURENG['kind']] = count
    report['definition_kinds'][ENGLISH_TURENG['kind']] = dict(lexical_kinds)
    report.setdefault('source_separation', {}).update(frequency_title='Frequency', frequency_rank_banks_unchanged=True)
    report.pop('browser_validation', None)
    report.update(revision=revision, ready_to_import=False, status='English added; native validation pending',
                  english_tureng=dict(counts, headwords=count, schema_and_content_verified=True,
                      snapshot=snapshot['snapshot'], source_sha256=snapshot['sha256'], section='ing',
                      normalization='NFC, English lowercase, apostrophe variants; source translation strings preserved',
                      english_form_aliases=False, frequency=False))
    for word in words:
        meanings = samples[word]
        for query in [word, word.upper()] if word in ('i', 'internet', 'bank') else [word]:
            report.setdefault('lookup_expectations', []).append(dict(word=query, language='en', forms=False,
                deinflect=query != word, names=[ENGLISH_TURENG['title']], expectedTerms=[word] if meanings else [],
                expectedDictionaries=[ENGLISH_TURENG['title']] if meanings else [],
                expectedTranslations=list(dict.fromkeys(meanings))))
    manifest = read_json(source/'source-manifest.json')
    manifest.update(definition_packs=report['definition_packs'], english_tureng=report['english_tureng'],
                    english_export_code={path: digest(ROOT/path) for path in (
                        'scripts/add_english_tureng.py', 'scripts/export_prototype.py', 'scripts/export_frequency.py', 'turkdict/editions.py')})
    write_json(dest/'source-manifest.json', manifest)
    write_json(dest/'release-report.json', report)
    (dest/'INSTALL.txt').write_text((source/'INSTALL.txt').read_text(encoding='utf-8') +
        '\nFor English to Turkish, import english-tureng.zip and select English.\n', encoding='utf-8')
    checksums(dest)
    print(f'{name}: {count:,} headwords; {info["download_bytes"]:,} ZIP bytes', flush=True)


if __name__ == '__main__':
    main()
