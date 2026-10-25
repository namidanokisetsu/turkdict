"""Package one dictionary edition with auditable coverage and release evidence."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from turkdict.common import digest, read_json, write_json


def banks(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:raise ValueError('Corrupt ZIP: '+str(path))
        for name in z.namelist():
            if name.startswith('term_bank_'):yield from json.loads(z.read(name))


def checksums(destination):
    (destination/'SHA256SUMS.txt').write_text(''.join(f'{digest(path)}  {path.name}\n' for path in sorted(destination.iterdir())
        if path.is_file() and path.name!='SHA256SUMS.txt'),encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=ROOT/'build/yomitan-full-inventory')
    p.add_argument('--destination',type=Path,default=ROOT/'build/releases/2026-10-01')
    p.add_argument('--browser-report',type=Path,default=ROOT/'build/yomitan-browser-release/browser-validation.json')
    p.add_argument('--export-directory',type=Path,help='Completed dictionary-plus-corpus export; omitted uses observed baseline')
    p.add_argument('--record-browser-validation',action='store_true',help='Attach matching native evidence without rebuilding or repeating schema checks')
    args=p.parse_args()
    if args.record_browser_validation:
        destination=args.destination.resolve()
        manifest=read_json(destination/'release-report.json')
        browser=read_json(args.browser_report)
        hashes={name:digest(destination/name) for name in manifest['files']}
        if not browser.get('passed') or browser.get('artifact_hashes')!=hashes or hashes!={name:info['sha256'] for name,info in manifest['files'].items()}:
            raise ValueError('Native validation does not match the checked artifacts')
        manifest.update(status='built and native-import-tested',ready_to_import=True,browser_validation=browser)
        write_json(destination/'release-report.json',manifest)
        checksums(destination)
        print('Matching native import, lookup and rendering evidence recorded',flush=True)
        return
    corpus=args.corpus.resolve()
    inventory=read_json(corpus/'inventory.json')
    size=inventory['selected_forms']
    sample=corpus/f'sample-{size}'
    coverage=read_json(sample/'coverage.json')
    if coverage['counts']['observed_forms']!=size:
        raise ValueError('Incomplete inventory analysis/linking')
    if 'complete observed union' not in inventory.get('analysis_scope',''):
        raise ValueError('The release must cover the entire observed inventory, not a sample')
    source=args.export_directory.resolve() if args.export_directory else sample/'yomitan-full'
    export=read_json(source/'report.json')
    expanded=export.get('forms_policy')=='dictionary-attested plus corpus-observed'
    destination=args.destination.resolve();destination.mkdir(parents=True,exist_ok=True)
    names=[f'turkish-{name}.zip' for name in ('forms','mono','bilingual','etymology')]
    for name in names:shutil.copy2(source/name,destination/name)
    print('Checking exported forms against corpus/dictionary evidence and direct definition targets',flush=True)
    known={r[0] for name in ('mono','bilingual') for r in banks(destination/f'turkish-{name}.zip')}
    dictionary_defs=None
    dictionary_current=None
    base_current=None
    if expanded:
        from collections import Counter
        from scripts.extend_dictionary_forms import usable_groups
        normalized=Path(export['source_build']['normalize'])/'definitions.sqlite3'
        if digest(normalized)!=export['dictionary_expansion']['definitions_sha256']:
            raise ValueError('Dictionary expansion source changed')
        dictionary_defs=sqlite3.connect(normalized.resolve().as_uri()+'?mode=ro',uri=True)
        dictionary_groups=iter(usable_groups(dictionary_defs,known,Counter()))
        dictionary_current=next(dictionary_groups,None)
        base_path=Path(export['dictionary_expansion']['base_export'])/'turkish-forms.zip'
        if digest(base_path)!=export['dictionary_expansion']['base_forms_sha256']:
            raise ValueError('Observed baseline changed')
        base_rows=iter(banks(base_path))
        base_current=next(base_rows,None)
    db=sqlite3.connect((corpus/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True)
    observed=iter(db.execute('SELECT surface FROM candidates ORDER BY surface'))
    current=next(observed,(None,))[0]
    previous=None
    spellings=links=0
    for row in banks(destination/'turkish-forms.zip'):
        word=row[0]
        if previous is not None and word<=previous:raise ValueError('Duplicate or unsorted form: '+word)
        previous=word
        while current is not None and current<word:current=next(observed,(None,))[0]
        source_backed=expanded and dictionary_current is not None and dictionary_current[0]==word
        if current!=word and not source_backed:raise ValueError('Neither observed nor dictionary-attested: '+word)
        targets=set()
        for lemma,labels in row[5]:
            if lemma not in known or not labels or lemma==word or lemma in targets:
                raise ValueError('Invalid, duplicate or dangling redirect: '+word)
            targets.add(lemma)
        if expanded:
            if dictionary_current is not None and dictionary_current[0]<word:
                raise ValueError('Missing dictionary form: '+dictionary_current[0])
            if source_backed:
                if not {lemma for lemma,_ in dictionary_current[1]}<=targets:
                    raise ValueError('Missing dictionary target: '+word)
                dictionary_current=next(dictionary_groups,None)
            if base_current is not None and base_current[0]<word:
                raise ValueError('Missing observed baseline form: '+base_current[0])
            if base_current is not None and base_current[0]==word:
                if not {lemma for lemma,_ in base_current[5]}<=targets:
                    raise ValueError('Missing observed baseline target: '+word)
                base_current=next(base_rows,None)
        spellings+=1;links+=len(targets)
    db.close()
    if expanded:
        dictionary_defs.close()
        if dictionary_current is not None or base_current is not None:
            raise ValueError('Incomplete dictionary/corpus union')
    if spellings!=export['entries']['forms'] or links!=export['form_lemma_links']:
        raise ValueError('Export counts do not match ZIP payloads')
    sys.path.insert(0,str(ROOT/'tools/prototype-validation'))
    import fastjsonschema
    schemas=ROOT/'research/yomitan/ext/data/schemas'
    validate_index=fastjsonschema.compile(read_json(schemas/'dictionary-index-schema.json'))
    validate_terms=fastjsonschema.compile(read_json(schemas/'dictionary-term-bank-v3-schema.json'))
    files={}
    for name in names:
        path=destination/name
        print('Validating '+name,flush=True)
        with zipfile.ZipFile(path) as z:
            validate_index(json.loads(z.read('index.json')))
            for bank in z.namelist():
                if bank.startswith('term_bank_'):validate_terms(json.loads(z.read(bank)))
            files[name]=dict(sha256=digest(path),download_bytes=path.stat().st_size,
                            uncompressed_json_bytes=sum(i.file_size for i in z.infolist()))
    manifest=dict(revision='2026-10-01',edition='single dictionary-plus-corpus edition' if expanded else 'single maximum-coverage observed edition',
        status='built; native validation pending',ready_to_import=False,
        inflected_spellings=spellings,distinct_surface_lemma_links=links,
        desired_link_target=5000000,desired_link_target_met=links>=5000000,
        definitions=export['entries'],definition_kinds=export['definition_kinds'],files=files,
        checks=dict(archive_integrity=True,official_schemas=True,observed_only=not expanded,
                    dictionary_or_corpus_attested=True,complete_dictionary_redirects=expanded,
                    direct_definition_targets=True,complete_inventory_processed=True),
        coverage=coverage,inventory=inventory,
        limitations=['Linguistic precision has not been independently reviewed',
                     'Unresolved spellings remain unresolved; they are not all proven noise',
                     'Static alternatives do not perform sentence-context disambiguation',
                     'Corpus link coverage is in-sample, not held-out reading correctness'],
        policy=dict(generated_forms=False,locally_generated_forms=False,wordfreq='reference-only',fuzzy_spelling_recovery=False,
                    wiktionary='All usable source-attested direct redirects; source labels preserved; case-only aliases use normalization' if expanded else 'Observed, source-attested direct redirects with source labels preserved; case-only aliases use normalization',
                    definition_scope=export['definition_scope'],regular_expanded_split=False))
    if expanded:manifest['dictionary_expansion']=export['dictionary_expansion']
    if args.browser_report.exists():
        browser=read_json(args.browser_report)
        if browser.get('passed') and browser.get('artifact_hashes')=={name:files[name]['sha256'] for name in names}:
            manifest.update(status='built and native-import-tested',ready_to_import=True,browser_validation=browser)
    if (corpus/'draft-case-review.json').exists():manifest['draft_case_review']=read_json(corpus/'draft-case-review.json')
    if (ROOT/'build/wiktionary-observed-audit.json').exists():
        manifest['wiktionary_inventory_comparison']=read_json(ROOT/'build/wiktionary-observed-audit.json')
    write_json(destination/'release-report.json',manifest)
    write_json(destination/'source-manifest.json',dict(sources=read_json(ROOT/'config/sources.lock.json'),
        corpus=read_json(ROOT/'data/macocu/report.json'),selection=read_json(corpus/'selection-inputs.json'),
        morphology=read_json(corpus/'analysis-inputs.json'),redirects=read_json(corpus/'redirect-inputs.json'),
        code={str(path.relative_to(ROOT)):digest(path) for path in [Path(__file__),ROOT/'scripts/build_full_inventory.py',
              ROOT/'scripts/build_yomitan.py',ROOT/'scripts/export_yomitan.py',ROOT/'scripts/extend_dictionary_forms.py',ROOT/'scripts/export_prototype.py',ROOT/'scripts/preview.py']},
        presentation=read_json(ROOT/'config/build.json')['presentation']))
    shutil.copy2(ROOT/'config/yomitan-popup.css',destination/'yomitan-popup.css')
    (destination/'INSTALL.txt').write_text(
        'Turkish dictionaries for Yomitan - one complete edition\n\n'
        '1. Remove previous Turkish Forms, Monolingual, Bilingual and Etymology imports to avoid duplicates.\n'
        '2. Import turkish-forms.zip.\n'
        '3. Import turkish-mono.zip (Turkish definitions), turkish-bilingual.zip (English definitions), or both.\n'
        '4. Optionally import turkish-etymology.zip.\n'
        '5. Select Turkish, enable dictionary deinflections for Turkish Forms, and use Word dictionary search resolution.\n'
        '6. Optional popup CSS is supplied for flat disclosures and hidden dictionary-name badges.\n\n'+
        ('Forms include every usable direct dictionary redirect plus the complete definition-linked MaCoCu/subtitle inventory. '
         if expanded else 'Forms are drawn from the complete observed MaCoCu/subtitle union, not a frequency-limited sample. ')+
        'Only exact definitions or supported direct definition links qualify; no synthetic inflections or fuzzy spelling guesses are added. '
        'Wiktionary variants retain their source labels. Complete definition headwords/phrases are included independently of Forms. '
        'IPA and audio are excluded.\n\n'
        'See release-report.json for exact counts, native-browser validation, installed storage and linguistic limitations. '
        'The five-million-link preference is a measured target, not a padding quota. There is no Regular/Expanded split.\n\n'
        'Rollback: remove these imports and reinstall the retained build/prototype ZIPs. '
        'source-manifest.json records source snapshots, checksums and build code.\n',encoding='utf-8')
    checksums(destination)
    print(json.dumps(dict(destination=str(destination),spellings=spellings,links=links,ready_to_import=manifest['ready_to_import'],files=files)),flush=True)


if __name__=='__main__':main()
