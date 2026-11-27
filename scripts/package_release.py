"""Publish individually installable, hash-verified Yomitan and StarDict archives."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, read_json
from turkdict.editions import definition_packs, pack_basename


def stardict_archive(destination, source, members):
    """Write a deterministic installable archive and verify its member hashes."""
    hashes = {}
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, 'w') as out:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED if name.endswith('.dz') else zipfile.ZIP_DEFLATED
            h = hashlib.sha256()
            with original.open(name) as stream, out.open(info, 'w', force_zip64=True) as target:
                while block := stream.read(1024 * 1024):
                    h.update(block)
                    target.write(block)
            hashes[name] = h.hexdigest()
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('StarDict archive CRC failure: ' + destination.name)
        for name, expected in hashes.items():
            h = hashlib.sha256()
            with archive.open(name) as stream:
                while block := stream.read(1024 * 1024):
                    h.update(block)
            if h.hexdigest() != expected:
                raise ValueError('StarDict archive member differs from input: ' + name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--yomitan', type=Path, default=ROOT/'build/releases/2026-10-01-complete')
    p.add_argument('--koreader', type=Path, default=ROOT/'build/releases/2026-10-02-koreader-v0.1.1')
    p.add_argument('--destination', type=Path, default=ROOT/'build/releases/2026-10-02-publish')
    p.add_argument('--skip-native-validation', action='store_true', help='Package without repeating native checks when explicitly requested')
    args = p.parse_args()
    dest = args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    y = read_json(args.yomitan/'release-report.json')
    k = read_json(args.koreader/'koreader-release-report.json')
    if not args.skip_native_validation:
        if not y['ready_to_import'] or not y['browser_validation']['passed'] or not k['native_validation']['passed']:
            raise ValueError('Both editions require matching native validation')
        if y['browser_validation']['artifact_hashes'] != {name: info['sha256'] for name, info in y['files'].items()}:
            raise ValueError('Yomitan native evidence does not match the dictionary ZIPs')
    if k['source_artifact_hashes'] != {name: info['sha256'] for name, info in y['files'].items()}:
        raise ValueError('Editions have different source content')
    for directory, report in ((args.yomitan, y), (args.koreader, k)):
        for name, info in report['files'].items():
            if digest(directory/name) != info['sha256']:
                raise ValueError('Artifact changed after validation: ' + name)
    definitions = definition_packs(y)
    if definition_packs(k) != definitions:
        raise ValueError('Editions have different definition packs')
    if any(pack['kind'] in ('mono', 'bilingual') for pack in definitions):
        raise ValueError('Separate the source dictionaries before packaging public assets')
    expected_yomitan = {pack_basename(pack) + '.zip' for pack in definitions} | {'turkish-forms.zip', 'turkish-etymology.zip'}
    if not expected_yomitan <= set(y['files']) or set(y['files']) - expected_yomitan - {'turkish-frequency.zip'}:
        raise ValueError('Unexpected Yomitan installation files')
    omitted = {pack_basename(pack) for pack in definitions if pack['kind'] in ('wiktionary', 'etymology')}
    omitted.add('turkish-etymology')
    dest.mkdir(parents=True)
    published = []
    for name, info in sorted(y['files'].items()):
        basename = Path(name).stem
        if basename in omitted:
            continue
        target = dest / (basename + '-yomitan.zip')
        shutil.copyfile(args.yomitan / name, target)
        if digest(target) != info['sha256']:
            raise ValueError('Copied artifact differs from input: ' + name)
        published.append(target)
    basenames = {pack_basename(pack) for pack in definitions} | {'turkish-etymology', 'turkish-morphology'}
    for name in sorted(k['files']):
        with zipfile.ZipFile(args.koreader/name) as archive:
            basename = name.removeprefix('koreader-').removesuffix('.zip')
            expected = {f'{basename}/{basename}{suffix}'
                        for suffix in ('.ifo', '.idx', '.dict.dz', '.css')}
            cache = f'{basename}/{basename}.idx.oft'
            found = set(archive.namelist())
            if (basename not in basenames
                    or not expected <= found or found - expected - {cache}):
                raise ValueError('Unexpected KOReader installation files: ' + name)
        if basename in omitted:
            continue
        target = dest / (basename + '-stardict.zip')
        stardict_archive(target, args.koreader / name, expected)
        published.append(target)
    for target in published:
        print(f'{target.name}: {target.stat().st_size:,} bytes; SHA-256 {digest(target)}', flush=True)


if __name__ == '__main__':
    main()
