"""Add an optional StarDict morphology pack from uncompacted canonical evidence."""
import argparse
from collections import defaultdict
from functools import lru_cache
import hashlib
import html
from itertools import groupby
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_yomitan import source_redirect_labels
from scripts.export_koreader import DICTIONARY_CSS, INDEX_QUERY, archive, dictzip
from scripts.preview import BADGES
from turkdict.common import digest, read_json, write_json
from turkdict.presentation import compact_label, compact_chain


def chain_labels(features):
    """Preserve feature order and separate readings, including unmarked grammar."""
    labels = []
    pos = None
    ids = {feature['id'] for feature in features}
    for feature in features:
        tag = feature['id']
        if tag in ('Noun', 'Verb', 'Adj', 'Adv', 'Pron', 'Num'):
            pos = tag
        if tag == 'DB':
            continue  # Adjacent part-of-speech labels already show the derivation.
        label = BADGES.get(tag, feature['label'])
        if label.startswith('Engine tag:'):
            label = tag  # Retain unknown source features rather than drop an analysis.
        if tag == 'A3sg':
            label = 'he / she / it' if pos == 'Verb' else 'singular'
        elif tag == 'A3pl':
            label = 'they' if pos == 'Verb' else 'plural'
        elif tag == 'Pnon':
            label = 'no possessive'
        elif tag == 'Prog1' and ids & {'Past', 'Narr', 'Fut'}:
            label = 'progressive'
        elif tag in ('P1sg', 'P2sg', 'P3sg', 'P1pl', 'P2pl', 'P3pl') and ids & {'PastPart', 'FutPart', 'Inf2', 'Inf3'}:
            label = {'P1sg': 'subject: I', 'P2sg': 'subject: you',
                     'P3sg': 'subject: he / she / it', 'P1pl': 'subject: we',
                     'P2pl': 'subject: you (plural)', 'P3pl': 'subject: they'}[tag]
        labels.append(label)
    return labels or ['inflected form']


def render_morphology(surface, alternatives):
    """Share common grammar while keeping each correlated alternative intact."""
    parts = ['<div><b>' + html.escape(surface) + '</b></div>']
    for lemma, paths in alternatives:
        href = html.escape('bword://' + lemma, quote=True)
        labels = [[compact_label(label) for label in path] for path in paths]
        parts.append('<div class="lemma"><a href="' + href + '">' + html.escape(lemma) + '</a></div>')
        parts.append('<div class="grammar">' + html.escape(compact_chain(labels)) + '</div>')
    return ''.join(parts)


def dictionary_paths(rows, has_corpus):
    """Match Forms precedence while keeping every dictionary-only reading."""
    for (surface, lemma), group in groupby(rows, key=lambda row: row[:2]):
        corpus = has_corpus(surface, lemma)
        for _, _, raw in group:
            labels = source_redirect_labels(json.loads(raw)['raw'])
            if surface == lemma or labels is None:
                raise ValueError('Invalid retained dictionary evidence')
            yield surface, lemma, labels, corpus


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, default=ROOT/'build/releases/2026-10-02-koreader-v0.1.2')
    p.add_argument('--canonical', type=Path, default=ROOT/'build/yomitan-dictionary-plus-corpus')
    p.add_argument('--destination', type=Path, default=ROOT/'build/releases/2026-10-02-koreader-morphology-v0.1.3')
    args = p.parse_args()
    base, canonical, dest = (path.resolve() for path in (args.base, args.canonical, args.destination))
    if dest.exists():
        raise ValueError('Use a fresh destination')
    report = read_json(base/'koreader-release-report.json')
    if not report.get('native_validation', {}).get('passed'):
        raise ValueError('Base definition packs require native validation')
    if 'morphology' in report:
        raise ValueError('Use a base export without a morphology pack')
    export = read_json(canonical/'report.json')
    expected_forms_hash = report.get('canonical_forms_sha256', report['source_artifact_hashes']['turkish-forms.zip'])
    if digest(canonical/'turkish-forms.zip') != expected_forms_hash:
        raise ValueError('Canonical forms do not match the installed definition aliases')
    definitions = Path(export['source_build']['normalize'])/'definitions.sqlite3'
    if digest(definitions) != export['dictionary_expansion']['definitions_sha256']:
        raise ValueError('Canonical definition targets changed')
    mappings = Path(export['dictionary_expansion']['base_export']).parent/'mappings.jsonl'
    dest.mkdir(parents=True)
    work = dest/'work'
    work.mkdir()
    for name, info in report['files'].items():
        if digest(base/name) != info['sha256']:
            raise ValueError('Base artifact changed: ' + name)
        shutil.copy2(base/name, dest/name)
        kind = name.removeprefix('koreader-turkish-').removesuffix('.zip')
        shutil.copytree(base/'work'/('turkish-'+kind), work/('turkish-'+kind))
        shutil.copy2(base/'work'/(kind+'.sqlite3'), work/(kind+'.sqlite3'))
    db = sqlite3.connect(work/'morphology.sqlite3')
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('CREATE TABLE analyses(surface TEXT,lemma TEXT,labels TEXT,PRIMARY KEY(surface,lemma,labels)) WITHOUT ROWID')
    db.execute('CREATE TABLE words(word BLOB,sortkey BLOB,offset INTEGER,size INTEGER,lemma TEXT,priority INTEGER)')
    defs = sqlite3.connect(definitions.resolve().as_uri()+'?mode=ro', uri=True)

    @lru_cache(maxsize=None)
    def lemma_for(eid):
        row = defs.execute('SELECT key FROM entries WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ValueError('Missing canonical definition target: ' + eid)
        return row[0]

    @lru_cache(maxsize=100000)
    def labels_for(raw):
        return json.dumps(chain_labels(json.loads(raw)), ensure_ascii=False)

    def add(surface, lemma, labels):
        db.execute('INSERT OR IGNORE INTO analyses VALUES(?,?,?)', (surface, lemma, labels))

    h = hashlib.sha256()
    total = 0
    with mappings.open('rb') as stream:
        for line in stream:
            h.update(line)
            record = json.loads(line)
            lemma = lemma_for(record['target_lemma_id'])
            if record['surface'] != lemma:
                add(record['surface'], lemma, labels_for(json.dumps(record['features'], ensure_ascii=False)))
            total += 1
            if total % 250000 == 0:
                db.commit()
                print(f'Read {total:,} canonical mapping records', flush=True)
    db.commit()
    defs.close()
    forms = sqlite3.connect((canonical/'forms.sqlite3').as_uri()+'?mode=ro', uri=True)
    dictionary_records = dictionary_overlaps = 0
    evidence = forms.execute('SELECT surface,target,features FROM dictionary_evidence ORDER BY surface,target,features')
    def has_corpus(surface, lemma):
        return db.execute('SELECT 1 FROM analyses WHERE surface=? AND lemma=? LIMIT 1',
                          (surface, lemma)).fetchone() is not None
    for surface, lemma, labels, corpus in dictionary_paths(evidence, has_corpus):
        if corpus:
            dictionary_overlaps += 1
        else:
            add(surface, lemma, json.dumps(labels, ensure_ascii=False))
        dictionary_records += 1
        if dictionary_records % 250000 == 0:
            db.commit()
            print(f'Read {dictionary_records:,} dictionary evidence records', flush=True)
    db.commit()
    folder = work/'turkish-morphology'
    folder.mkdir()
    stem = folder/'turkish-morphology'
    offset = spellings = pairs = analyses = 0
    sample_words = {'evde', 'kitabı', 'evlerimizden', 'yaptıklarımızdan', 'ağlaştılar', 'orjinal', 'yüz', 'koskoca'}
    samples = []
    with stem.with_suffix('.dict').open('wb') as out:
        for surface, raw in forms.execute('SELECT surface,payload FROM forms ORDER BY surface'):
            row = json.loads(raw)
            paths = defaultdict(list)
            for lemma, labels in db.execute('SELECT lemma,labels FROM analyses WHERE surface=? ORDER BY lemma,labels', (surface,)):
                paths[lemma].append(json.loads(labels))
            expected = [lemma for lemma, _ in row[5]]
            if set(paths) != set(expected):
                raise ValueError('Canonical analysis targets differ from shipped forms: ' + surface)
            alternatives = [(lemma, paths[lemma]) for lemma in expected]
            body = render_morphology(surface, alternatives).encode('utf-8')
            word = surface.encode('utf-8')
            if b'\0' in word or b'\0' in body or offset+len(body) >= 2**32:
                raise ValueError('Invalid StarDict record')
            db.execute('INSERT INTO words VALUES(?,?,?,?,?,?)', (word, word.lower(), offset, len(body), surface, -1))
            out.write(body)
            offset += len(body)
            spellings += 1
            pairs += len(expected)
            analyses += sum(len(p) for _, p in alternatives)
            if surface in sample_words:
                samples.append(dict(surface=surface, alternatives=alternatives, html=body.decode()))
            if spellings % 250000 == 0:
                db.commit()
                print(f'Exported {spellings:,} morphology entries ({offset:,} raw bytes)', flush=True)
    forms.close()
    if (spellings, pairs) != (report['forms_spellings'], report['distinct_surface_lemma_pairs']):
        raise ValueError('Morphology coverage differs from definition aliases')
    db.commit()
    with stem.with_suffix('.idx').open('wb') as out:
        for word, start, size in db.execute(INDEX_QUERY):
            out.write(word+b'\0'+struct.pack('>II', start, size))
    db.close()
    stem.with_suffix('.ifo').write_text("StarDict's dict ifo file\nversion=2.4.2\nbookname=Turkish Morphology\n" +
        f'wordcount={spellings}\nidxfilesize={stem.with_suffix(".idx").stat().st_size}\n' +
        'sametypesequence=h\nlang=tr-en\ndate=2026-10-02\n' +
        'description=Separate source-attested analyses; no context disambiguation or generated forms.\n', encoding='utf-8')
    stem.with_suffix('.css').write_text(DICTIONARY_CSS + '.lemma { margin-top: 0.4em; font-weight: bold; } .grammar { font-size: 90%; }\n', encoding='utf-8')
    print(f'Compressing {offset:,} morphology bytes', flush=True)
    dictzip(stem.with_suffix('.dict'), Path(str(stem)+'.dict.dz'))
    stem.with_suffix('.dict').unlink()
    name = 'koreader-turkish-morphology.zip'
    archive(folder, dest/name)
    report.pop('native_validation', None)
    report['grammar_note'] = 'Optional Turkish Morphology pack uses linguistic labels and shares common grammar; parenthesized alternatives retain whole analysis paths.'
    report['files'][name] = dict(sha256=digest(dest/name), download_bytes=(dest/name).stat().st_size,
        installed_bytes=sum(p.stat().st_size for p in folder.iterdir()), index_records=spellings,
        uncompressed_definition_bytes=offset, form_spellings=spellings, surface_lemma_pairs=pairs,
        displayed_analysis_paths=analyses)
    report['morphology'] = dict(canonical_mappings_sha256=h.hexdigest(), canonical_mapping_records=total,
        dictionary_evidence_records=dictionary_records, dictionary_records_with_corpus_paths=dictionary_overlaps,
        source_precedence='All corpus chains for existing pairs; all dictionary labels for dictionary-only pairs, matching Forms policy',
        canonical_forms_db_sha256=digest(canonical/'forms.sqlite3'),
        canonical_definitions_sha256=digest(definitions), complete_form_and_target_preservation=True,
        alternative_layout='Shared prefix/suffix, parenthesized whole-path alternatives, compact linguistic labels',
        samples=samples, runtime_analysis=False, sentence_disambiguation=False)
    write_json(dest/'koreader-release-report.json', report)
    shutil.copy2(base/'source-manifest.json', dest/'source-manifest.json')
    (dest/'INSTALL-KOREADER.txt').write_text((base/'INSTALL-KOREADER.txt').read_text(encoding='utf-8') +
        '\nOptional morphology: copy turkish-morphology into data/dict/ and enable Turkish Morphology.\n'
        'It appears as a separate dictionary result; swipe between it and definitions.\n'
        'Shared grammar appears once; parenthesized alternatives are separate possible readings. Lemma links open dictionary lookups.\n'
        'Dictionary ordering controls whether grammar or definitions appear first.\n', encoding='utf-8')
    (dest/'SHA256SUMS-KOREADER.txt').write_text(''.join(f'{digest(path)}  {path.name}\n' for path in sorted(dest.iterdir())
        if path.is_file() and not path.name.startswith('SHA256SUMS')), encoding='utf-8')
    print(json.dumps(report['files'][name]), flush=True)


if __name__ == '__main__':
    main()
