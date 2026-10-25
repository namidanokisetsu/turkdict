"""Check every StarDict index and native exact/English fallback lookups."""
import argparse
import html
import json
import mmap
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, read_json, write_json
from turkdict.editions import definition_packs, pack_basename, source_language


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--sdcv', type=Path, required=True)
    args = p.parse_args()
    dest = args.directory.resolve()
    report = read_json(dest / 'koreader-release-report.json')
    definitions = definition_packs(report)
    installation_packs = {pack_basename(pack): pack for pack in definitions}
    installation_packs.update({f'turkish-{kind}': dict(kind=kind) for kind in ('etymology', 'morphology')})
    for name, info in report['files'].items():
        if digest(dest / name) != info['sha256']:
            raise ValueError('Archive hash mismatch')
        basename = name.removeprefix('koreader-').removesuffix('.zip')
        spec = installation_packs[basename]
        kind = spec['kind']
        folder = dest / 'work' / basename
        if source_language(spec) == 'en':
            metadata = (folder/(basename + '.ifo')).read_text(encoding='utf-8')
            if 'lang=en-tr\n' not in metadata or info['alias_pairs'] != 0:
                raise ValueError('English dictionary has wrong direction or foreign form aliases')
        with zipfile.ZipFile(dest / name) as archive:
            if archive.testzip() is not None:
                raise ValueError('Bad ZIP CRC')
            for entry in archive.infolist():
                path = dest / 'work' / entry.filename
                import hashlib
                h = hashlib.sha256()
                with archive.open(entry) as stream:
                    while block := stream.read(1024*1024):
                        h.update(block)
                if h.hexdigest() != digest(path):
                    raise ValueError('Archive differs from tested extracted files')
        previous = None
        count = 0
        with (folder / (basename + '.idx')).open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            position = 0
            while position < len(data):
                end = data.find(b'\0', position)
                if end < 0 or end + 9 > len(data):
                    raise ValueError('Truncated index')
                word = data[position:end]
                word.decode('utf-8')
                key = (word.lower(), word)
                if previous is not None and key < previous:
                    raise ValueError('Wrong StarDict index order')
                offset, size = struct.unpack_from('>II', data, end+1)
                if size == 0 or offset+size > info['uncompressed_definition_bytes']:
                    raise ValueError('Invalid data pointer')
                previous = key
                position = end+9
                count += 1
        if count != info['index_records']:
            raise ValueError('Index count mismatch')
        print(f'{kind}: all {count:,} index records valid', flush=True)
    turkish_definitions = [pack for pack in definitions if source_language(pack) == 'tr']
    tureng_title = next((pack['title'] for pack in turkish_definitions if pack['kind'] == 'tureng'), 'Turkish Bilingual')
    name_kinds = {pack['title']: pack['kind'] for pack in definitions}
    name_kinds.update({'Turkish Etymology': 'etymology', 'Turkish Morphology': 'morphology'})
    canonical_cases = {(row['word'], tuple(sorted(row['names']))): row
                       for row in report.get('lookup_expectations', [])}
    if report.get('definition_packs'):
        cases = {(row['word'], tuple(sorted(row['names']))) for row in report['lookup_expectations']}
        groups = [(pack['title'],) for pack in turkish_definitions] + [tuple(pack['title'] for pack in turkish_definitions)]
        for pack in definitions:
            if source_language(pack) == 'en':
                cases.add(('house (Tureng)', (pack['title'],)))
                cases.add(('bank', tuple(sorted((pack['title'], tureng_title)))))
    else:
        lookups = read_json(ROOT / 'build/yomitan-browser-complete/browser-validation.json')['lookups']
        cases = {(row['word'], tuple(sorted(set(row['definitions'])))) for row in lookups if row['definitions']}
        groups = [('Turkish Monolingual',), ('Turkish Bilingual',), ('Turkish Monolingual', 'Turkish Bilingual')]
    # Negative controls, Turkish I variants, proper names, and long phrases.
    for names in groups:
        for word in ('zzzxqnonword', 'evde', 'yüz', 'İstanbul', "ankara'da", 'ışık', 'IŞIK', 'kitabı', 'ünlü', 'ağlaştılar', 'koskoca'):
            cases.add((word, names))
    if report.get('presentation'):
        cases.add(('ünlü (Tureng)', (tureng_title,)))
    for word in ('ev', 'kitap', 'okumak', 'zzzxqnonword'):
        cases.add((word, ('Turkish Etymology',)))
    morphology_samples = {sample['surface']: sample for sample in report.get('morphology', {}).get('samples', [])}
    if 'morphology' in report:
        for word in (*morphology_samples, 'zzzxqnonword', 'kitap', 'evde'):
            cases.add((word, ('Turkish Morphology',)))
        cases.add(('kitabı', ('Turkish Morphology', *(pack['title'] for pack in turkish_definitions))))
    long_db = sqlite3.connect(dest / ('work/tureng.sqlite3' if report.get('definition_packs') else 'work/bilingual.sqlite3'))
    for word, in long_db.execute('SELECT word FROM words WHERE length(word)>255'):
        cases.add((word.decode(), (tureng_title,)))
    long_db.close()
    evidence = []
    for word, names in sorted(cases):
        canonical = canonical_cases.get((word, tuple(sorted(names))))
        english_case = canonical and canonical.get('language') == 'en' and word != word.lower()
        command = [str(args.sdcv.resolve()), '-n', '-x', '-j', '-0', '-1', '--data-dir', str(dest / 'work')]
        if not english_case:
            command.append('-e')
        expected = {}
        expected_order = {}
        for name in names:
            command += ['-u', name]
            kind = name_kinds[name]
            db = sqlite3.connect(dest / ('work/' + kind + '.sqlite3'))
            # Exact sdcv search is case-sensitive; normal English lookup handles case.
            wanted = (word.lower() if english_case else word).encode()
            expected[name] = {lemma for lemma, in db.execute('SELECT DISTINCT lemma FROM words WHERE sortkey=? AND word=?', (wanted.lower(), wanted))}
            if 'priority' in {row[1] for row in db.execute('PRAGMA table_info(words)')}:
                expected_order[name] = [lemma for lemma, in db.execute(
                    'SELECT lemma FROM words WHERE sortkey=? AND word=? ORDER BY word,(CAST(word AS TEXT)=lemma) DESC,priority,lemma', (wanted.lower(), wanted))]
            db.close()
        start = time.perf_counter()
        command.append(word)
        env = dict(os.environ, SDCV_HISTFILE=str(dest / 'work/sdcv-history'), SDCV_HISTSIZE='0')
        try:
            run = subprocess.run(command, capture_output=True, timeout=15, env=env)
        except subprocess.TimeoutExpired as error:
            raise ValueError('Native reader timed out for ' + repr(word)) from error
        if run.returncode not in (0, 1, 2) or run.stderr:
            raise ValueError(f'Native reader failed for {word!r}: exit {run.returncode}, {run.stderr!r}')
        arrays = [json.loads(line.decode('utf-8')) for line in run.stdout.splitlines() if line.startswith(b'[')]
        if len(arrays) != 1:
            raise ValueError('Unexpected native JSON output for ' + repr(word))
        found = {name: set() for name in names}
        ordered = {name: [] for name in names}
        for row in arrays[0]:
            if row['dict'] == 'Turkish Morphology' and word in morphology_samples:
                # sdcv prefixes HTML fields with a newline; compare the payload.
                if row['definition'].strip() != morphology_samples[word]['html']:
                    raise ValueError('Native morphology lost or changed an analysis: ' + word)
            match = re.match(r'\s*<div><b>(.*?)</b></div>', row['definition'], re.S)
            if not match:
                raise ValueError('Missing target headword')
            target = html.unescape(match[1])
            if report.get('frequency') and row['dict'] in {pack['title'] for pack in turkish_definitions}:
                known_ranks = {'ev': 304, 'kitap': 592}
                if target in known_ranks and f'Frequency: {known_ranks[target]}' not in row['definition']:
                    raise ValueError('Missing embedded headword frequency: ' + target)
            if row['dict'] in {pack['title'] for pack in definitions if source_language(pack) == 'en'}:
                if 'class="frequency"' in row['definition']:
                    raise ValueError('Turkish frequency leaked into English dictionary')
            found[row['dict']].add(target)
            ordered[row['dict']].append(target)
        if found != expected:
            raise ValueError(f'Native lookup mismatch: {word!r}: {found!r} != {expected!r}')
        if canonical:
            terms = {term for targets in found.values() for term in targets}
            dictionaries = {name for name, targets in found.items() if targets}
            if terms != set(canonical['expectedTerms']) or dictionaries != set(canonical['expectedDictionaries']):
                raise ValueError('Native lookup differs from canonical source/form expectations: ' + word)
            if canonical.get('language') == 'en':
                content = html.unescape(''.join(row['definition'] for row in arrays[0]))
                if any(text not in content for text in canonical['expectedTranslations'][:10]):
                    raise ValueError('Native English lookup lost Turkish translations: ' + word)
        for name, targets in expected_order.items():
            if ordered[name] != targets:
                raise ValueError(f'Native lookup order mismatch: {word!r}: {ordered[name]!r} != {targets!r}')
        if word == 'ağlaştılar' and any(targets[0] != 'ağlaşmak' for name, targets in ordered.items()
                                      if targets and 'ağlaşmak' in targets and name != 'Turkish Morphology'):
            raise ValueError('ağlaşmak must precede ağ for ağlaştılar')
        if word == 'koskoca' and any(targets and 'koskoca' in targets and targets[0] != 'koskoca' for targets in ordered.values()):
            raise ValueError('koskoca must precede koca')
        if report.get('presentation'):
            for name in names:
                if word in found[name] and ordered[name][0] != word:
                    raise ValueError('Exact headword did not appear first: ' + word)
            if word == 'ünlü' and 'Turkish Bilingual' in names:
                definition = next(row['definition'] for row in arrays[0]
                                  if row['dict'] == 'Turkish Bilingual' and '<b>ünlü</b>' in row['definition'])
                if definition.index('Wiktionary') > definition.index('Tureng'):
                    raise ValueError('Wiktionary must precede Tureng')
                if 'bword://ünlü (Tureng)' not in definition or 'famously' in definition:
                    raise ValueError('Tureng overflow was not hidden behind More')
            if word == 'ünlü' and tureng_title in names and report.get('definition_packs'):
                definition = next(row['definition'] for row in arrays[0]
                                  if row['dict'] == tureng_title and '<b>ünlü</b>' in row['definition'])
                if 'bword://ünlü (Tureng)' not in definition or 'famously' in definition:
                    raise ValueError('Standalone Tureng overflow was not hidden behind More')
        if word == 'house (Tureng)':
            sample = next(case for case in report['lookup_expectations'] if case.get('language') == 'en' and case['word'] == 'house')
            content = html.unescape(''.join(row['definition'] for row in arrays[0]))
            if any(text not in content for text in sample['expectedTranslations']):
                raise ValueError('English overflow lost translations')
        evidence.append(dict(word=word, dictionaries=list(names), targets=ordered, english_case_fallback=bool(english_case),
                             process_ms=round((time.perf_counter()-start)*1000, 2)))
        if len(evidence) % 10 == 0:
            print(f'Passed {len(evidence)} native checks so far', flush=True)
    english_fallback = []
    for pack in definitions:
        if source_language(pack) != 'en':
            continue
        for word, target in [('walked', 'walk'), ('goes', 'go'), ('books', 'books'), ('went', 'went')]:
            command = [str(args.sdcv.resolve()), '-n', '-x', '-j', '-0', '-1', '--data-dir', str(dest/'work'),
                       '-u', pack['title'], '--', word]
            run = subprocess.run(command, capture_output=True, timeout=15,
                                 env=dict(os.environ, SDCV_HISTFILE=str(dest/'work/sdcv-history'), SDCV_HISTSIZE='0'))
            arrays = [json.loads(line.decode('utf-8')) for line in run.stdout.splitlines() if line.startswith(b'[')]
            found = [row['word'] for rows in arrays for row in rows]
            if run.returncode or run.stderr or target not in found:
                raise ValueError('Native English fallback failed: ' + word)
            english_fallback.append(dict(word=word, expected=target, found=found, dictionary=pack['title']))
    report['native_validation'] = dict(passed=True, reader_version=subprocess.check_output([str(args.sdcv.resolve()), '--version']).decode().strip(),
        reader_sha256=digest(args.sdcv),
        environment_note=('Windows validation used MSYS2 sdcv with a UTF-8 active-code-page manifest; reader code unchanged.'
                          if os.name == 'nt' and args.sdcv.name == 'sdcv-utf8.exe' else 'Native sdcv exact search.'),
        checks=len(evidence), lookups=evidence, timing_note='Each check starts a new desktop process; not device lookup latency.',
        full_index_validation=True, archive_crc_and_extracted_bytes_verified=True,
        english_fallback=english_fallback, english_fallback_note='English fallback requires Enable fuzzy search; exact inflected entries retain their own source meanings.')
    write_json(dest / 'koreader-release-report.json', report)
    (dest / 'SHA256SUMS-KOREADER.txt').write_text(''.join(
        f'{digest(path)}  {path.name}\n' for path in sorted(dest.iterdir()) if path.is_file() and not path.name.startswith('SHA256SUMS')), encoding='utf-8')
    print(f'Passed {len(evidence)} native dictionary lookups and {len(english_fallback)} English fallback checks', flush=True)


if __name__ == '__main__':
    main()
