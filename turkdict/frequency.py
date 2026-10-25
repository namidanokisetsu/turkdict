import importlib.metadata
import sqlite3
from collections import Counter
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET
import zipfile

from .common import digest, identity, key, valid_token


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li'):
            self.parts.append(' ')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def epub(path):
    """Read the EPUB spine, not navigation and duplicate archive members."""
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read('META-INF/container.xml'))
        opf = next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('rootfile'))
        root = ET.fromstring(z.read(opf))
        base = opf.rsplit('/', 1)[0] + '/' if '/' in opf else ''
        items = {e.attrib['id']: e.attrib['href'] for e in root.iter() if e.tag.endswith('}item')}
        texts = []
        for e in root.iter():
            if e.tag.endswith('}itemref'):
                import posixpath
                member = posixpath.normpath(base + items[e.attrib['idref']])
                parser = Text()
                parser.feed(z.read(member).decode('utf-8-sig'))
                texts.append(''.join(parser.parts))
        text = ' '.join(texts)
        tokens = [key(t) for t in re.findall(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)?", text) if valid_token(t)]
        metadata = {e.tag.rsplit('}', 1)[-1]: e.text for e in root.iter()
                    if e.tag.rsplit('}', 1)[-1] in ('title', 'creator', 'identifier')}
        return dict(file_sha256=digest(path), text_sha256=identity(tokens), metadata=metadata), Counter(tokens)


def build(sources, root, output, config):
    db = sqlite3.connect(output / 'frequency.sqlite3')
    try:
        return populate(db, sources, root, config)
    finally:
        db.close()


def populate(db, sources, root, config):
    db.execute('CREATE TABLE signals(surface TEXT,source TEXT,value REAL,rank INTEGER,PRIMARY KEY(surface,source))')
    db.execute('CREATE INDEX signal_rank ON signals(source,rank)')
    report = {}
    for source in sources:
        counts = Counter()
        rejected = 0
        previous = None
        with (root / source['path']).open(encoding='utf-8') as f:
            for line in f:
                token, raw = line.rstrip().rsplit(' ', 1)
                count = int(raw)
                if count <= 0 or (previous is not None and count > previous):
                    raise ValueError('Invalid or unsorted frequency counts')
                previous = count
                if valid_token(token):
                    counts[key(token)] += count
                else:
                    rejected += 1
        ordered = sorted(counts.items(), key=lambda p: (-p[1], p[0]))
        db.executemany('INSERT INTO signals VALUES(?,?,?,?)', ((w, source['id'], n, rank) for rank, (w, n) in enumerate(ordered, 1)))
        report[source['id']] = dict(usable=len(counts), rejected=rejected, snapshot=source['snapshot'])
    if config.get('wordfreq'):
        from wordfreq import get_frequency_dict, available_languages
        version = importlib.metadata.version('wordfreq')
        if version != config['wordfreq']:
            raise ValueError('wordfreq version mismatch')
        count, rejected = 0, 0
        seen = set()
        for word, value in sorted(get_frequency_dict('tr', wordlist='best').items(), key=lambda p: (-p[1], p[0])):
            if not valid_token(word):
                rejected += 1
                continue
            word = key(word)
            if word in seen:
                continue
            seen.add(word)
            count += 1
            db.execute('INSERT INTO signals VALUES(?,?,?,?)', (word, 'wordfreq', value, count))
        report['wordfreq'] = dict(version=version, usable=count, rejected=rejected,
                                 data_file=available_languages('best')['tr'].rsplit('/', 1)[-1].rsplit('\\', 1)[-1],
                                 data_sha256=digest(available_languages('best')['tr']),
                                 note='Static multi-source ranking signal; overlaps subtitles. Not an independent corpus or current-language census.')
    seen_books = set()
    occurrences, books = Counter(), Counter()
    report['books'] = []
    for relative in sorted(config.get('books', [])):
        info, counts = epub(root / relative)
        info['duplicate'] = info['text_sha256'] in seen_books
        report['books'].append(info)
        if not info['duplicate']:
            seen_books.add(info['text_sha256'])
            occurrences.update(counts)
            books.update(counts.keys())
    for name, counts in [('book_occurrences', occurrences), ('book_documents', books)]:
        db.executemany('INSERT INTO signals VALUES(?,?,?,?)', ((w, name, n, rank) for rank, (w, n) in enumerate(sorted(counts.items(), key=lambda p: (-p[1], p[0])), 1)))
    db.commit()
    return report


def ranked(db, weights):
    # Reciprocal rank fusion: heterogeneous counts never add directly.
    query = ' + '.join('COALESCE(SUM(CASE WHEN source=? THEN ?/(60.0+rank) END),0)' for _ in weights)
    args = [v for source, weight in sorted(weights.items()) for v in (source, weight)]
    return db.execute(f'SELECT surface,{query} AS score FROM signals GROUP BY surface ORDER BY score DESC,surface', args)
