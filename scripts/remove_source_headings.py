"""Refresh split Yomitan dictionaries without rebuilding their source inventory."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_yomitan import checksums
from scripts.preview import SOURCE
from turkdict.common import digest, read_json, write_json
from turkdict.editions import definition_packs, pack_basename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    source, dest = args.source.resolve(), args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    report = read_json(source/'release-report.json')
    packs = {pack_basename(pack) + '.zip': pack for pack in definition_packs(report)}
    if not all(pack.get('source') for pack in packs.values()):
        raise ValueError('Source-separated dictionaries required')
    dest.mkdir(parents=True)
    removed = {}
    for name, info in report['files'].items():
        if digest(source/name) != info['sha256']:
            raise ValueError('Source archive changed: ' + name)
        if name not in packs:
            shutil.copy2(source/name, dest/name)
            continue
        title = SOURCE[packs[name]['source']]
        removed[name] = 0
        with zipfile.ZipFile(source/name) as old, zipfile.ZipFile(dest/name, 'w') as new:
            for member in old.infolist():
                data = old.read(member.filename)
                if member.filename.startswith('term_bank_'):
                    rows = json.loads(data)
                    for row in rows:
                        for item in row[5]:
                            if not isinstance(item, dict) or item.get('type') != 'structured-content':
                                continue
                            for section in item['content']:
                                content = section.get('content', [])
                                if not content or not isinstance(content[0], dict):
                                    continue
                                heading = content[0]
                                if (heading.get('content') == [title]
                                        and heading.get('style', {}).get('fontSize') == '0.75em'
                                        and heading.get('style', {}).get('fontWeight') == 'bold'):
                                    del content[0]
                                    removed[name] += 1
                    data = json.dumps(rows, ensure_ascii=False).encode('utf-8')
                new.writestr(member, data)
        with zipfile.ZipFile(dest/name) as zipped:
            info.update(sha256=digest(dest/name), download_bytes=(dest/name).stat().st_size,
                        uncompressed_json_bytes=sum(member.file_size for member in zipped.infolist()))
        print(f'{name}: removed {removed[name]:,} source headings', flush=True)
    report.pop('browser_validation', None)
    report.update(revision='2026-10-10-no-source-headings', ready_to_import=False,
                  status='formatting rebuilt; tests omitted at user request',
                  source_heading_refresh=dict(source=str(source), removed=removed, source_headings=False))
    manifest = read_json(source/'source-manifest.json')
    manifest['source_heading_refresh'] = report['source_heading_refresh']
    manifest['source_heading_code'] = {name: digest(ROOT/name) for name in (
        'scripts/remove_source_headings.py', 'scripts/export_prototype.py',
        'scripts/separate_dictionaries.py', 'scripts/add_english_tureng.py', 'scripts/export_koreader.py')}
    write_json(dest/'release-report.json', report)
    write_json(dest/'source-manifest.json', manifest)
    shutil.copy2(source/'INSTALL.txt', dest/'INSTALL.txt')
    checksums(dest)


if __name__ == '__main__':
    main()
