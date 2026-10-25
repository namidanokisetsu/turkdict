"""Export existing corpus ranks as a native Yomitan frequency dictionary."""
import argparse
from pathlib import Path
import sqlite3
import sys
import zipfile
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.export_prototype import archive
from turkdict.common import digest, read_json, write_json

TITLE = 'Frequency'
DESCRIPTION = ('Rank of the displayed headword spelling in the complete MaCoCu/OpenSubtitles '
               'inventory. Lower means more frequent; 1 is the most frequent. '
               'Equal-weight reciprocal rank fusion, offset 60. Inflected spellings are '
               'ranked separately, not summed into lemma families. Unattested headwords '
               'have no rank. This is a corpus estimate, not a vocabulary level.')


def frequency_rows(db):
    # DISTINCT prevents repeated senses/languages from duplicating metadata.
    # Preserve global corpus ranks: filtering to dictionary entries must not rerank.
    for word, rank in db.execute('''SELECT e.key,c.rank FROM
        (SELECT DISTINCT key FROM definitions.entries) e
        JOIN candidates c ON c.surface=e.key ORDER BY e.key'''):
        if not isinstance(rank, int) or rank < 1:
            raise ValueError('Invalid corpus rank: ' + word)
        yield [word, 'freq', rank]


def read_frequencies(path):
    if not path.exists():
        return {}
    ranks = {}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith('term_meta_bank_'):
                for word, mode, rank in json.loads(z.read(name)):
                    if mode != 'freq' or type(rank) is not int or rank < 1 or word in ranks:
                        raise ValueError('Invalid or duplicate frequency metadata')
                    ranks[word] = rank
    return ranks


def copy_frequency(source, destination):
    """Keep the existing rank banks while applying the current dictionary title."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, 'w') as output:
        for entry in original.infolist():
            content = original.read(entry)
            if entry.filename == 'index.json':
                index = json.loads(content)
                index['title'] = TITLE
                content = json.dumps(index, ensure_ascii=False).encode('utf-8')
            output.writestr(entry, content, compresslevel=9)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination) as output:
        if output.testzip() is not None:
            raise ValueError('Corrupt frequency archive')
        for name in original.namelist():
            if name != 'index.json' and original.read(name) != output.read(name):
                raise ValueError('Frequency rank data changed: ' + name)


def export_frequency(destination, definitions, corpus, revision):
    selection = read_json(corpus/'selection-inputs.json')
    inventory = read_json(corpus/'inventory.json')
    if 'complete observed union' not in inventory.get('analysis_scope', ''):
        raise ValueError('Frequency export needs the completed corpus inventory')
    with sqlite3.connect((corpus/'corpus.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
        if not db.execute('SELECT ok FROM selection_complete').fetchone():
            raise ValueError('Incomplete corpus selection')
        db.execute('ATTACH DATABASE ? AS definitions', (definitions.resolve().as_uri()+'?mode=ro',))
        count = archive(destination/'turkish-frequency.zip', TITLE, frequency_rows(db), revision,
                        bank_prefix='term_meta_bank',
                        index_metadata=dict(frequencyMode='rank-based', description=DESCRIPTION))
    result = dict(title=TITLE, entries=count, method=DESCRIPTION, selection=selection,
                  definitions_sha256=digest(definitions), ranking_scope=inventory['selected_forms'])
    write_json(destination/'frequency-report.json', result)
    print(f'frequency: {count:,} ranked headwords', flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--corpus', type=Path, default=ROOT/'build/yomitan-full-inventory')
    p.add_argument('--definitions', type=Path)
    args = p.parse_args()
    definitions = args.definitions or Path(read_json(ROOT/'build/macocu-baseline-latest.json')['outputs']['normalize'])/'definitions.sqlite3'
    args.destination.mkdir(parents=True, exist_ok=True)
    export_frequency(args.destination, definitions, args.corpus, '2026-10-05-frequency')


if __name__ == '__main__':
    main()
