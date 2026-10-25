"""Measure held-out EPUB lookup coverage against exported KOReader indexes."""
import argparse
from collections import Counter
import csv
import html
import json
import mmap
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, key, write_json
from turkdict.frequency import Text
from turkdict.morphology import Zemberek


class BodyText(Text):
    def __init__(self, exclude_headings=False):
        super().__init__()
        self.in_body = False
        self.exclude_headings = exclude_headings

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.in_body = True
        if self.in_body:
            if self.exclude_headings and tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                self.hidden += 1
            super().handle_starttag(tag, attrs)
            if tag in ('h1', 'h2', 'h3', 'table', 'td'):
                self.parts.append(' ')

    def handle_endtag(self, tag):
        if self.in_body:
            super().handle_endtag(tag)
            if self.exclude_headings and tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                self.hidden = max(0, self.hidden - 1)
            if tag in ('h1', 'h2', 'h3', 'table', 'td'):
                self.parts.append(' ')
        if tag == 'body':
            self.in_body = False

    def handle_data(self, text):
        if self.in_body:
            super().handle_data(text)


def read_book(path, story_pattern):
    sections = []
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read('META-INF/container.xml'))
        opf = next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('rootfile'))
        root = ET.fromstring(archive.read(opf))
        items = {e.attrib['id']: e.attrib['href'] for e in root.iter() if e.tag.endswith('}item')}
        for e in root.iter():
            if not e.tag.endswith('}itemref'):
                continue
            member = posixpath.normpath(posixpath.join(posixpath.dirname(opf), unquote(items[e.attrib['idref']])) )
            section = dict(member=member, story=bool(re.search(story_pattern, member)))
            for name, exclude in [('words', False), ('prose_words', True)]:
                parser = BodyText(exclude)
                parser.feed(archive.read(member).decode('utf-8-sig'))
                text = unicodedata.normalize('NFC', ''.join(parser.parts))
                section[name] = re.findall(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)?", text)
            sections.append(section)
        metadata = {e.tag.rsplit('}', 1)[-1]: e.text for e in root.iter()
                    if e.tag.rsplit('}', 1)[-1] in ('title', 'creator', 'identifier')}
    return metadata, sections


def index_hits(path, wanted):
    wanted_bytes = {w.encode('utf-8') for w in wanted}
    hits = set()
    records = 0
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        offset = 0
        while offset < len(data):
            end = data.find(b'\0', offset)
            if end < offset or end + 9 > len(data):
                raise ValueError('Malformed StarDict index')
            word = data[offset:end]
            if word in wanted_bytes:
                hits.add(word.decode('utf-8'))
            offset = end + 9
            records += 1
    return hits, records


def coverage(counts, hits):
    total = sum(counts.values())
    covered = sum(n for w, n in counts.items() if w in hits)
    types = sum(w in hits for w in counts)
    return dict(tokens=total, covered_tokens=covered, missing_tokens=total-covered,
                token_percent=round(100*covered/total, 4) if total else None,
                spellings=len(counts), covered_spellings=types, missing_spellings=len(counts)-types,
                spelling_percent=round(100*types/len(counts), 4) if counts else None)


def native_hits(words, directory, executable, history):
    result = {}
    env = dict(os.environ, SDCV_HISTFILE=str(history.resolve()), SDCV_HISTSIZE='0')
    words = sorted(words)
    for start in range(0, len(words), 80):
        batch = words[start:start+80]
        command = [str(executable.resolve()), '-n', '-x', '-e', '-j', '-0', '-1',
                   '--data-dir', str(directory.resolve()), '-u', 'Turkish Monolingual', '-u', 'Turkish Bilingual', *batch]
        run = subprocess.run(command, capture_output=True, env=env, timeout=30)
        if run.returncode not in (0, 1, 2) or run.stderr:
            raise ValueError(f'Native lookup failed: {run.returncode}, {run.stderr!r}')
        arrays = [json.loads(line.decode('utf-8')) for line in run.stdout.splitlines() if line.startswith(b'[')]
        if len(arrays) != len(batch):
            raise ValueError('Native lookup returned unexpected number of results')
        for word, rows in zip(batch, arrays):
            result[word] = {'mono': any(r['dict']=='Turkish Monolingual' for r in rows),
                            'bilingual': any(r['dict']=='Turkish Bilingual' for r in rows)}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('book', type=Path)
    p.add_argument('--release', type=Path, default=ROOT/'build/releases/2026-10-02-koreader-v0.1.2')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--story-pattern', required=True, help='Regex selecting narrative spine members')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    metadata, sections = read_book(args.book, args.story_pattern)
    scopes = {'story': Counter(key(w) for s in sections if s['story'] for w in s['prose_words']),
              'chapters_with_headings': Counter(key(w) for s in sections if s['story'] for w in s['words']),
              'whole_spine': Counter(key(w) for s in sections for w in s['words'])}
    if not scopes['story']:
        raise ValueError('Story pattern selected no words')
    words = set(scopes['whole_spine'])
    # Analysis is diagnostic only, never used to improve the baseline dictionaries.
    engine = Zemberek(ROOT)
    try:
        analyses = {w: engine.analyze(w) for w in sorted(words)}
    finally:
        engine.close()
    wanted = words | {a['lemma'] for rows in analyses.values() for a in rows}
    hits, inputs = {}, {}
    for pack, folder in [('mono','turkish-mono'), ('bilingual','turkish-bilingual')]:
        path = args.release/'work'/folder/(folder+'.idx')
        hits[pack], records = index_hits(path, wanted)
        inputs[pack] = dict(path=str(path.resolve()), sha256=digest(path), records=records)
        print(f'Checked {pack}: {records:,} exported index records', flush=True)
    hits['both'] = hits['mono'] | hits['bilingual']
    executable = ROOT/'tools/sdcv/sdcv-utf8.exe'
    native = native_hits(words, args.release/'work', executable, args.output/'sdcv-history')
    disagreements = {pack: [w for w in sorted(words) if native[w][pack] != (w in hits[pack])]
                     for pack in ('mono', 'bilingual')}
    write_json(args.output/'native-lookups.json', native)
    report = dict(book=dict(path=str(args.book.resolve()), sha256=digest(args.book), metadata=metadata),
                  dictionary_indexes=inputs, scopes={}, sections=[], diagnostic_engine=engine.identity,
                  native_validation=dict(executable_sha256=digest(executable), normalized_spellings_checked=len(native), index_disagreements=disagreements),
                  method='Visible body text in EPUB spine; story excludes headings (Roman chapter numbers), with separate chapters-with-headings and whole-spine scopes. NFC and Turkish-aware lowercase; letter tokens with optional internal apostrophe; numbers excluded. No fuzzy lookup, spelling repairs, runtime morphology, or book-fed dictionary changes in baseline. Lookup hits are availability, not semantic or grammatical correctness. Named entities and foreign words retained. Desktop sdcv verifies every normalized spelling; device selection/casing behavior is not measured. Zemberek diagnoses potential recovery; TRmorph has not been tested.')
    details = []
    for w,n in scopes['whole_spine'].most_common():
        linked = {pack: sorted({a['lemma'] for a in analyses[w] if a['lemma'] in hits[pack]}) for pack in hits}
        details.append(dict(surface=w, story_count=scopes['story'][w], whole_spine_count=n,
                            covered={pack: w in h for pack,h in hits.items()},
                            diagnostic_linked_lemmas=linked, analyses=analyses[w]))
    for scope, counts in scopes.items():
        report['scopes'][scope] = {pack: coverage(counts,h) for pack,h in hits.items()}
        for pack,h in hits.items():
            missing = [r for r in details if counts[r['surface']] and not r['covered'][pack]]
            groups = {'analyzer_links_to_existing_entry': [], 'analyses_without_shipped_target': [], 'no_analysis': []}
            for r in missing:
                category = ('analyzer_links_to_existing_entry' if r['diagnostic_linked_lemmas'][pack]
                            else 'analyses_without_shipped_target' if r['analyses'] else 'no_analysis')
                groups[category].append(r['surface'])
            report['scopes'][scope][pack]['miss_diagnosis'] = {
                name: dict(spellings=len(ws),tokens=sum(counts[w] for w in ws),forms=ws)
                for name,ws in groups.items()}
    for s in sections:
        counts = Counter(key(w) for w in (s['prose_words'] if s['story'] else s['words']))
        report['sections'].append(dict(member=s['member'], story=s['story'], coverage=coverage(counts,hits['both'])))
    write_json(args.output/'report.json', report)
    write_json(args.output/'forms.json', details)
    table = ''.join('<tr><td>'+pack+'</td><td>'+str(data['covered_tokens'])+' / '+str(data['tokens'])+
                    '</td><td>'+str(data['token_percent'])+'%</td><td>'+str(data['missing_spellings'])+'</td></tr>'
                    for pack,data in report['scopes']['story'].items())
    misses = ''.join('<tr><td>'+html.escape(r['surface'])+'</td><td>'+str(r['story_count'])+
                     '</td><td>'+html.escape(', '.join(r['diagnostic_linked_lemmas']['both']) or
                     ('No shipped target for returned analyses' if r['analyses'] else 'No Zemberek analysis'))+'</td></tr>'
                     for r in details if r['story_count'] and not r['covered']['both'])
    (args.output/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>Book coverage</title>'
        '<style>body{font:17px system-ui;max-width:1000px;margin:40px auto;padding:0 20px}table{border-collapse:collapse;width:100%}'
        'td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}</style>'
        '<h1>'+html.escape(metadata.get('title') or args.book.stem)+'</h1><p>'+html.escape(report['method'])+'</p>'
        '<table><tr><th>Dictionary</th><th>Covered occurrences</th><th>Coverage</th><th>Missing spellings</th></tr>'+table+'</table>'
        '<h2>Story misses with both dictionaries</h2><table><tr><th>Spelling</th><th>Occurrences</th>'
        '<th>Diagnostic recovery candidate</th></tr>'+misses+'</table>'
        '<p>Full measurements: <a href="report.json">report.json</a>; <a href="missing.csv">missing.csv</a>. '
        'Analyzer candidates require linguistic review and do not establish TRmorph coverage.</p>',encoding='utf-8')
    with (args.output/'missing.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['surface','story_count','whole_spine_count','mono_hit','bilingual_hit','diagnostic_linked_lemmas','all_diagnostic_lemmas'])
        for r in details:
            if not all(r['covered'].values()):
                writer.writerow([r['surface'],r['story_count'],r['whole_spine_count'],r['covered']['mono'],r['covered']['bilingual'],
                                 '; '.join(r['diagnostic_linked_lemmas']['both']),'; '.join(sorted({a['lemma'] for a in r['analyses']}))])
    print(json.dumps({scope: {pack: {k:v for k,v in data.items() if k != 'miss_diagnosis'}
                             for pack,data in packs.items()} for scope,packs in report['scopes'].items()}, indent=2))


if __name__ == '__main__':
    main()
