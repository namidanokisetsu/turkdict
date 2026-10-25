"""Explicitly record installed lexicon/grammar/frequency resources after dependency setup."""
import importlib.metadata
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, write_json

records = []
for package in ('NlpToolkit-MorphologicalAnalysis', 'NlpToolkit-Dictionary', 'wordfreq'):
    dist = importlib.metadata.distribution(package)
    for path in sorted(dist.files, key=str):
        name = str(path)
        relevant = (package != 'wordfreq' and '/data/' in name and name.endswith(('.txt', '.xml'))) or name.endswith('small_tr.msgpack.gz')
        if relevant:
            records.append(dict(id=package.lower() + ':' + path.name, package=package, version=dist.version,
                                snapshot=dist.version, path=name, sha256=digest(dist.locate_file(path)),
                                url=f'https://pypi.org/project/{package}/{dist.version}/',
                                language='tr', direction='frequency' if package == 'wordfreq' else 'morphology',
                                parser='package-native-v1'))
write_json(ROOT / 'config/resources.lock.json', records)
