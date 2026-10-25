"""Fetch the checked-in manifest without regenerating pinned source metadata."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turkdict.common import read_json
from turkdict.sources import fetch


def main():
    for source in read_json(ROOT / 'config/sources.lock.json'):
        path = fetch(source, ROOT)
        print(source['id'], path.stat().st_size, flush=True)


if __name__ == '__main__':
    main()
