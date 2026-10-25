"""Export completed Yomitan content as independently installable StarDict packs.

Aliases are ordinary index records sharing lemma data offsets (no .syn file).
Definitions retain every sense. Tureng overflow opens through a More link.
"""
import argparse
import gzip
import html
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_yomitan import banks
from scripts.export_frequency import read_frequencies
from scripts.preview import SOURCE
from turkdict.common import digest, read_json, write_json
from turkdict.editions import definition_packs, pack_basename, source_language

INDEX_QUERY = ('SELECT word,offset,size FROM words '
               'ORDER BY sortkey,word,(CAST(word AS TEXT)=lemma) DESC,priority,lemma')
DICTIONARY_CSS = ('body { margin: 0; text-align: left; } div { margin: 0.2em 0; } '
                  'ol, ul { padding-left: 1.2em; } '
                  '.source-label { font-size: 75%; font-weight: normal; } '
                  '.frequency { font-size: 85%; }\n')


def frequency_header(lemma, ranks):
    rank = ranks.get(lemma)
    return '<div class="frequency">Frequency: ' + str(rank) + '</div>' if rank is not None else ''


def render(node, *, source=None):
    if isinstance(node, str):
        return html.escape(node)
    if isinstance(node, list):
        return ''.join(render(child, source=source) for child in node)
    if node.get('type') == 'structured-content':
        return render(node['content'], source=source)
    tag = node.get('tag', 'span')
    if tag == 'summary':
        return ''
    if tag == 'details':
        tag = 'div'
    if tag not in ('span', 'div', 'ol', 'ul', 'li', 'br'):
        raise ValueError('Unsupported content tag: ' + tag)
    if tag == 'br':
        return '<br/>'
    style = node.get('style', {})
    if (source and style.get('fontSize') == '0.75em'
            and style.get('fontWeight') == 'bold' and node.get('content') == [SOURCE[source]]):
        return ''
    # Source labels carry a smaller font in Yomitan; preserve that in StarDict.
    attrs = ' class="source-label"' if style.get('fontSize') == '0.75em' else ''
    if style.get('fontSize') and not attrs:
        attrs = ' style="font-size:' + html.escape(style['fontSize'], quote=True) + '"'
    body = render(node.get('content', []), source=source)
    if style.get('fontWeight') == 'bold' and 'source-label' not in attrs:
        body = '<b>' + body + '</b>'
    if style.get('fontStyle') == 'italic':
        body = '<i>' + body + '</i>'
    return '<' + tag + attrs + '>' + body + '</' + tag + '>'


def bilingual_content(glossary, lemma, *, source=None):
    """Put Wiktionary first and move Tureng overflow to a linked full entry."""
    sections = [section for item in glossary for section in item['content']]
    def title(section):
        return SOURCE[source] if source else ''.join(section['content'][0]['content'])
    sections.sort(key=lambda section: {'Wiktionary': 0, 'Tureng': 1}.get(title(section), 2))
    parts = []
    extra = None
    for section in sections:
        content = section['content']
        if title(section) != 'Tureng' or not any(node.get('tag') == 'details' for node in content):
            parts.append(render(section, source=source))
            continue
        visible = [node for node in content if node.get('tag') != 'details']
        remainder = [node for node in content if node.get('tag') == 'details'][0]
        # Keep the two semicolon lists verbatim; do not split source glosses.
        complete = render(visible[-1]['content']) + '; ' + render(remainder['content'][1]['content'])
        extra_word = lemma + ' (Tureng)'
        href = 'bword://' + extra_word
        parts.append('<div>' + render(visible, source=source) + '<a href="' + html.escape(href, quote=True) + '">More</a></div>')
        heading = '' if source else '<div class="source-label">Tureng</div>'
        extra = (extra_word, '<div><b>' + html.escape(extra_word) + '</b></div>' + heading + '<p>' + complete + '</p>')
    return ''.join(parts), extra


def dictzip(source, destination, chunk_size=58315):
    """Deterministic gzip with dictzip RA table and independently readable chunks."""
    count = (source.stat().st_size + chunk_size - 1) // chunk_size
    if count == 0 or count > 32762:
        raise ValueError('Payload outside single-member dictzip size range')
    extra_size = 10 + count * 2
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    sizes = []
    crc = total = 0
    with source.open('rb') as inp, destination.open('w+b') as out:
        out.write(b'\x1f\x8b\x08\x04' + struct.pack('<I', 0) + b'\x02\x03')
        out.write(struct.pack('<H', extra_size))
        out.write(b'RA' + struct.pack('<HHHH', 6 + count * 2, 1, chunk_size, count))
        table_offset = out.tell()
        out.write(b'\0' * (count * 2))
        while block := inp.read(chunk_size):
            crc = zlib.crc32(block, crc)
            total += len(block)
            data = compressor.compress(block) + compressor.flush(zlib.Z_FULL_FLUSH)
            if len(data) > 65535:
                raise ValueError('Compressed chunk too large')
            sizes.append(len(data))
            out.write(data)
        out.write(compressor.flush(zlib.Z_FINISH))
        out.write(struct.pack('<II', crc, total & 0xffffffff))
        out.seek(table_offset)
        out.write(struct.pack('<' + 'H' * count, *sizes))
    # Independent standard gzip implementation checks the complete CRC/size.
    import hashlib
    check = hashlib.sha256()
    with gzip.open(destination, 'rb') as inp:
        while block := inp.read(1024 * 1024):
            check.update(block)
    if check.hexdigest() != digest(source):
        raise ValueError('Dictzip round-trip mismatch')


def archive(folder, destination):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.iterdir()):
            info = zipfile.ZipInfo(folder.name + '/' + path.name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED if path.suffix == '.dz' else zipfile.ZIP_DEFLATED
            with path.open('rb') as inp, z.open(info, 'w', force_zip64=True) as out:
                shutil.copyfileobj(inp, out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT / 'build/releases/2026-10-01-complete')
    p.add_argument('--destination', type=Path, default=ROOT / 'build/releases/2026-10-02-koreader-v0.1.1')
    p.add_argument('--morphology-source', type=Path, help='Reuse a validated morphology pack with identical Forms and canonical definitions')
    args = p.parse_args()
    source = args.source.resolve()
    dest = args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    dest.mkdir(parents=True)
    upstream = read_json(source / 'release-report.json')
    ranks = read_frequencies(source/'turkish-frequency.zip')
    for name, info in upstream['files'].items():
        if digest(source / name) != info['sha256']:
            raise ValueError('Source artifact hash mismatch: ' + name)
    work = dest / 'work'
    work.mkdir()
    packs = {}
    definitions = definition_packs(upstream)
    for spec in definitions + [dict(kind='etymology', title='Turkish Etymology', language='en')]:
        kind, title, lang = spec['kind'], spec['title'], spec['language']
        basename = pack_basename(spec)
        folder = work / basename
        folder.mkdir()
        db = sqlite3.connect(work / (kind + '.sqlite3'))
        db.execute('PRAGMA journal_mode=OFF')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('CREATE TABLE words(word BLOB, sortkey BLOB, offset INTEGER, size INTEGER, lemma TEXT, priority INTEGER)')
        targets = {}
        extras = set()
        offset = 0
        with (folder / (basename + '.dict')).open('wb') as out:
            for row in banks(source / (basename + '.zip')):
                lemma = row[0]
                source_id = spec.get('source')
                content, extra = (bilingual_content(row[5], lemma, source=source_id)
                                  if kind in ('bilingual', 'tureng', 'tureng-en-tr')
                                  else (render(row[5], source=source_id), None))
                frequency = frequency_header(lemma, ranks) if kind != 'etymology' and source_language(spec) == 'tr' else ''
                body = ('<div><b>' + html.escape(lemma) + '</b></div>' + frequency + content).encode('utf-8')
                if offset + len(body) >= 2**32:
                    raise ValueError('32-bit StarDict data limit exceeded')
                word = lemma.encode('utf-8')
                if b'\0' in word or b'\0' in body:
                    raise ValueError('NUL in dictionary text')
                targets[lemma] = (offset, len(body))
                db.execute('INSERT INTO words VALUES(?,?,?,?,?,?)', (word, word.lower(), offset, len(body), lemma, -1))
                out.write(body)
                offset += len(body)
                if extra:
                    extra_word, extra_content = extra
                    extra_body = extra_content.encode('utf-8')
                    if offset + len(extra_body) >= 2**32:
                        raise ValueError('32-bit StarDict data limit exceeded')
                    extras.add(extra_word)
                    word = extra_word.encode('utf-8')
                    db.execute('INSERT INTO words VALUES(?,?,?,?,?,?)', (word, word.lower(), offset, len(extra_body), extra_word, -1))
                    out.write(extra_body)
                    offset += len(extra_body)
        if extras.intersection(targets):
            raise ValueError('Supplement headword collides with a lexical headword')
        db.commit()
        packs[kind] = dict(db=db, folder=folder, title=title, lang=lang, source_language=source_language(spec),
                           basename=basename, targets=targets,
                           definition_headwords=len(targets), aliases=0, data_bytes=offset, overflow_entries=len(extras))
        print(f'{kind}: {len(targets):,} complete definitions', flush=True)
    forms = links = 0
    for row in banks(source / 'turkish-forms.zip'):
        surface = row[0]
        word = surface.encode('utf-8')
        if b'\0' in word:
            raise ValueError('NUL in surface')
        for priority, (lemma, labels) in enumerate(row[5]):
            matched = False
            for kind in (spec['kind'] for spec in definitions if source_language(spec) == 'tr'):
                pack = packs[kind]
                target = pack['targets'].get(lemma)
                if target:
                    pack['db'].execute('INSERT INTO words VALUES(?,?,?,?,?,?)',
                                       (word, word.lower(), *target, lemma, priority))
                    pack['aliases'] += 1
                    matched = True
            if not matched or lemma == surface or not labels:
                raise ValueError('Invalid alias: ' + surface)
            links += 1
        forms += 1
        if forms % 250000 == 0:
            for pack in packs.values():
                pack['db'].commit()
            print(f'Indexed {forms:,} form spellings', flush=True)
    if forms != upstream['inflected_spellings'] or links != upstream['distinct_surface_lemma_links']:
        raise ValueError('Coverage count mismatch')
    report = dict(format='StarDict 2.4.2, HTML, dictzip, merged aliases',
                  forms_spellings=forms, distinct_surface_lemma_pairs=links,
                  source_artifact_hashes={name: info['sha256'] for name, info in upstream['files'].items()},
                  device_tested=False, grammar_note='Aliases show target definitions. Per-form grammar remains in Yomitan and canonical data.',
                  presentation='Exact headwords precede aliases; aliases retain Yomitan order within each dictionary; compact source labels; Wiktionary first; Tureng overflow behind More links.',
                  files={})
    if ranks:
        report['frequency'] = upstream['frequency']
    if upstream.get('definition_packs'):
        report['definition_packs'] = definitions
        report['lookup_expectations'] = upstream['lookup_expectations']
        report['presentation'] = 'Separate source dictionaries; no redundant source headings; Tureng overflow behind More links; all other senses expanded.'
    if 'english_tureng' in upstream:
        report['english_tureng'] = upstream['english_tureng']
    if 'canonical_forms_sha256' in upstream:
        report['canonical_forms_sha256'] = upstream['canonical_forms_sha256']
    for kind, pack in packs.items():
        db = pack['db']
        db.commit()
        if upstream.get('definition_packs'):
            db.execute('CREATE INDEX words_sortkey ON words(sortkey)')
        folder = pack['folder']
        stem = folder / pack['basename']
        count = 0
        with stem.with_suffix('.idx').open('wb') as out:
            for word, offset, size in db.execute(INDEX_QUERY):
                out.write(word + b'\0' + struct.pack('>II', offset, size))
                count += 1
        db.close()
        text = ("StarDict's dict ifo file\nversion=2.4.2\n" +
                f'bookname={pack["title"]}\nwordcount={count}\nidxfilesize={stem.with_suffix(".idx").stat().st_size}\n' +
                'sametypesequence=h\nlang=' + pack['source_language'] + '-' + pack['lang'] + '\ndate=' + upstream['revision'][:10] + '\n' +
                ('description=English-to-Turkish translations.\n' if pack['source_language'] == 'en' else
                 'description=Complete dictionary-plus-corpus edition. Static aliases; alternative lemmas retained.\n'))
        stem.with_suffix('.ifo').write_text(text, encoding='utf-8', newline='\n')
        stem.with_suffix('.css').write_text(DICTIONARY_CSS, encoding='utf-8')
        print(f'{kind}: compressing {pack["data_bytes"]:,} data bytes; {count:,} index records', flush=True)
        dictzip(stem.with_suffix('.dict'), Path(str(stem) + '.dict.dz'))
        stem.with_suffix('.dict').unlink()
        filename = 'koreader-' + pack['basename'] + '.zip'
        archive(folder, dest / filename)
        report['files'][filename] = dict(sha256=digest(dest / filename), download_bytes=(dest / filename).stat().st_size,
            installed_bytes=sum(p.stat().st_size for p in folder.iterdir()), definition_headwords=pack['definition_headwords'],
            alias_pairs=pack['aliases'], index_records=count, uncompressed_definition_bytes=pack['data_bytes'],
            tureng_overflow_entries=pack['overflow_entries'])
        print(json.dumps({filename: report['files'][filename]}), flush=True)
    if args.morphology_source:
        previous = args.morphology_source.resolve()
        prior = read_json(previous/'koreader-release-report.json')
        if (not prior.get('native_validation', {}).get('passed')
                or prior['source_artifact_hashes']['turkish-forms.zip'] != report['source_artifact_hashes']['turkish-forms.zip']
                or prior.get('canonical_forms_sha256') != report.get('canonical_forms_sha256')
                or prior['morphology']['canonical_definitions_sha256'] != upstream['dictionary_expansion']['definitions_sha256']):
            raise ValueError('Reused morphology does not match these Forms and definitions')
        filename = 'koreader-turkish-morphology.zip'
        if digest(previous/filename) != prior['files'][filename]['sha256']:
            raise ValueError('Reused morphology archive changed')
        shutil.copy2(previous/filename, dest/filename)
        folder = work/'turkish-morphology'
        folder.mkdir()
        for suffix in ('.ifo', '.idx', '.dict.dz', '.css'):
            name = 'turkish-morphology'+suffix
            shutil.copy2(previous/'work/turkish-morphology'/name, folder/name)
        shutil.copy2(previous/'work/morphology.sqlite3', work/'morphology.sqlite3')
        report['files'][filename] = prior['files'][filename]
        report['morphology'] = dict(prior['morphology'], reused_from=str(previous))
        print('Reused the matching validated morphology pack', flush=True)
    shutil.copy2(source / 'source-manifest.json', dest / 'source-manifest.json')
    write_json(dest / 'koreader-release-report.json', report)
    (dest / 'INSTALL-KOREADER.txt').write_text(
        'Extract the selected KOReader definition ZIPs into koreader/data/dict/.\n'
        'Keep each dictionary folder and its .ifo, .idx, .dict.dz and .css together.\n'
        'On Android, use the KOReader data directory containing data/dict (not the APK folder).\n'
        'Restart KOReader; enable the packs under Dictionary settings. Etymology is optional.\n'
        'No separate Forms pack or runtime analyzer is required for these StarDict packs.\n'
        'Aliases retrieve definitions directly, preserving alternative lemmas. Per-form grammar labels\n'
        'are retained in the canonical/Yomitan data, but are not shown by these StarDict aliases.\n'
        'Choose dictionary order in Dictionary settings. Tureng shows its opening translations; More opens the full list\n'
        'in another dictionary popup. All other definition senses are expanded.\n'
        'Frequency ranks appear under headwords where attested: smaller means more frequent.\n'
        'Physical-reader latency and rendering remain unmeasured.\n', encoding='utf-8')
    (dest / 'SHA256SUMS-KOREADER.txt').write_text(''.join(
        f'{digest(path)}  {path.name}\n' for path in sorted(dest.iterdir()) if path.is_file() and not path.name.startswith('SHA256SUMS')), encoding='utf-8')


if __name__ == '__main__':
    main()
