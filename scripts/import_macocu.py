"""Stream the pinned MaCoCu archive into separate, filtered frequency counts."""
import argparse
from collections import Counter
import hashlib
import io
import html
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import write_json

EXPECTED_MD5 = '089e4929ed1ec6f0d9241483b7530477'
TOKEN = re.compile(r"[^\W_]+(?:['’ʼ][^\W_]+)*", re.UNICODE)
VALID = re.compile(r"[abcçdefgğhıijklmnoöprsştuüvyzâîûqwx]+(?:'[abcçdefgğhıijklmnoöprsştuüvyzâîûqwx]+)?\Z")
TRANSLATE = str.maketrans("Iİ’ʼ", "ıi''")


def tokens(text):
    text = unicodedata.normalize('NFC', text).translate(TRANSLATE).lower()
    return [w for w in TOKEN.findall(text) if len(w) <= 64 and VALID.fullmatch(w)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT / 'MaCoCu-tr-2.0.xml.zip')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/macocu')
    parser.add_argument('--limit-documents', type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / 'frequency.sqlite3'
    if target.exists():
        raise SystemExit('Output already exists; use a different output directory.')
    started = time.time()
    with args.archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'md5').hexdigest()
    if checksum != EXPECTED_MD5:
        raise ValueError('Archive does not match the pinned publisher checksum')
    print('Archive checksum verified', flush=True)
    db = sqlite3.connect(target)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE counts(surface TEXT PRIMARY KEY, occurrences INTEGER NOT NULL) WITHOUT ROWID')
    counts = Counter()
    stats = Counter()
    qualities, languages = Counter(), Counter()

    def flush():
        db.executemany('INSERT INTO counts VALUES(?,?) ON CONFLICT(surface) DO UPDATE SET occurrences=occurrences+excluded.occurrences', counts.items())
        db.commit()
        counts.clear()

    def report(complete=False):
        value = dict(complete=complete, archive_md5=checksum, parser_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     source='MaCoCu-tr-2.0', source_url='https://www.clarin.si/repository/xmlui/handle/11356/1802',
                     filters=dict(language='tr', quality=['good', 'good_wo_punct']),
                     stats=dict(stats), quality_counts=dict(qualities), language_counts=dict(languages),
                     elapsed_seconds=round(time.time()-started, 1), limited_documents=args.limit_documents,
                     note='Paragraph text only. No new deduplication beyond publisher cleaning. Counts are occurrences, not document counts. A prefix sample is not representative.')
        write_json(args.output / 'report.json', value)
        print(json.dumps(value['stats']), flush=True)

    with zipfile.ZipFile(args.archive) as archive:
        with archive.open('MaCoCu-tr-2.0.xml') as stream:
            active = None
            paragraph = []
            size = 0
            for line in io.TextIOWrapper(stream, encoding='utf-8'):
                stripped = line.strip()
                if stripped.startswith('<p '):
                    if active is not None:
                        raise ValueError('Nested paragraph')
                    # A fresh parser per header avoids retaining unique corpus
                    # attributes/names in one enormous Expat parsing context.
                    active = ET.fromstring(stripped + '</p>').attrib
                    paragraph = []
                    size = 0
                elif stripped == '</p>':
                    if active is None:
                        raise ValueError('Paragraph end without start')
                    stats['paragraphs'] += 1
                    quality, lang = active.get('quality', 'missing'), active.get('lang', 'missing')
                    qualities[quality] += 1
                    languages[lang] += 1
                    if lang == 'tr' and quality in ('good', 'good_wo_punct') and size <= 8_000_000:
                        words = tokens(html.unescape(' '.join(paragraph)))
                        counts.update(words)
                        stats['accepted_paragraphs'] += 1
                        stats['accepted_tokens'] += len(words)
                    else:
                        stats['excluded_paragraphs'] += 1
                        stats['oversized_paragraphs'] += size > 8_000_000
                    active = None
                    paragraph = []
                elif stripped == '</doc>':
                    if active is not None:
                        raise ValueError('Unclosed paragraph')
                    stats['documents'] += 1
                    if stats['documents'] % 10000 == 0:
                        flush()
                        report()
                    if args.limit_documents and stats['documents'] >= args.limit_documents:
                        break
                elif active is not None:
                    size += len(line)
                    if size <= 8_000_000:
                        paragraph.append(line)
            if active is not None:
                raise ValueError('Truncated paragraph')
    flush()
    stats['distinct_forms'] = db.execute('SELECT count(*) FROM counts').fetchone()[0]
    db.execute('CREATE INDEX frequency_order ON counts(occurrences DESC, surface)')
    db.commit()
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.close()
    report(complete=True)


if __name__ == '__main__':
    main()
