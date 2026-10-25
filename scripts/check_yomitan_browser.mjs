// Actual installed Yomitan code, extension origin, native IndexedDB, isolated Edge profile.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {cpSync, createReadStream, existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync} from 'node:fs';
import path from 'node:path';

const packs = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const resume = process.argv.includes('--resume');
const reuseArg = process.argv.find(arg => arg.startsWith('--reuse-from='));
const reuseFrom = reuseArg ? path.resolve(reuseArg.slice('--reuse-from='.length)) : null;
assert(!(resume && reuseFrom), 'Choose resume or reuse, not both');
const formFile = process.argv[4] && !process.argv[4].startsWith('--') ? process.argv[4] : 'turkish-forms.zip';
const profile = path.join(output, 'profile');
assert(resume ? existsSync(profile) : !existsSync(profile), 'Use a fresh output directory, or --resume for a completed import');
mkdirSync(output, {recursive: true});
const extensionId = 'likgccmbimhjbgkjambclfkhldnlhbnn';
const base = path.join(process.env.LOCALAPPDATA, 'Google/Chrome/User Data/Default/Extensions', extensionId);
const extension = path.join(base, readdirSync(base).sort().at(-1));
const manifest = JSON.parse(readFileSync(path.join(extension, 'manifest.json')));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const release = JSON.parse(readFileSync(path.join(packs, 'release-report.json'), 'utf8'));
const definitionPacks = release.definition_packs;
const definitionNames = definitionPacks ? definitionPacks.filter(pack => (pack.source_language || 'tr') === 'tr').map(pack => pack.title) : ['Turkish Monolingual', 'Turkish Bilingual'];
const englishNames = (definitionPacks || []).filter(pack => pack.source_language === 'en').map(pack => pack.title);
const files = [formFile, ...(definitionPacks ? definitionPacks.map(pack => `${pack.basename || `turkish-${pack.kind}`}.zip`) : ['turkish-mono.zip', 'turkish-bilingual.zip']), 'turkish-etymology.zip'];
const hasFrequency = existsSync(path.join(packs, 'turkish-frequency.zip'));
const frequencyName = release.frequency?.title || release.source_separation?.frequency_title || 'Turkish Frequency';
if (hasFrequency) files.push('turkish-frequency.zip');
let priorValidation;
if (reuseFrom) {
    priorValidation = JSON.parse(readFileSync(path.join(reuseFrom, 'browser-validation.json'), 'utf8'));
    assert(priorValidation.passed && priorValidation.clean_profile, 'Reuse requires completed clean-profile validation');
    assert.equal(priorValidation.environment.version, manifest.version, 'Importer version changed');
    for (const imported of priorValidation.imports) assert.deepEqual(imported.errors, []);
    cpSync(path.join(reuseFrom, 'profile'), profile, {recursive: true});
}
const server = createServer((req, res) => {
    const name = decodeURIComponent(req.url.slice(1));
    if (!files.includes(name)) {res.writeHead(404).end(); return;}
    res.writeHead(200, {'Content-Type': 'application/zip', 'Access-Control-Allow-Origin': '*'});
    createReadStream(path.join(packs, name)).pipe(res);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const packUrl = `http://127.0.0.1:${server.address().port}/`;
if ((resume || reuseFrom) && existsSync(path.join(profile, 'DevToolsActivePort'))) unlinkSync(path.join(profile, 'DevToolsActivePort'));
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless=new', '--no-first-run', '--disable-sync', '--disable-background-networking',
    '--no-default-browser-check', `--user-data-dir=${profile}`, '--remote-debugging-port=0',
    `--disable-extensions-except=${extension}`, `--load-extension=${extension}`, 'about:blank',
], {windowsHide: true, stdio: 'ignore'});
let ws;
try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    for (let i = 0; i < 200 && !existsSync(portFile); i++) await wait(100);
    const port = readFileSync(portFile, 'utf8').split('\n')[0];
    let target;
    for (let i = 0; i < 100 && !target; i++) {
        target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === 'page' && (t.url.startsWith(`chrome-extension://${extensionId}/`) || ((resume || reuseFrom) && t.url === 'about:blank')));
        if (!target) await wait(100);
    }
    assert(target, 'Isolated Yomitan extension did not load');
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
    let nextId = 0;
    const pending = new Map();
    const browserErrors = [];
    ws.onmessage = event => {
        const msg = JSON.parse(event.data);
        if (msg.method === 'Runtime.exceptionThrown') browserErrors.push(msg.params.exceptionDetails);
        if (!msg.id) return;
        const callback = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) callback.reject(new Error(JSON.stringify(msg.error)));
        else callback.resolve(msg.result);
    };
    const cdp = (method, params = {}) => new Promise((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, {resolve, reject});
        ws.send(JSON.stringify({id, method, params}));
    });
    await cdp('Runtime.enable');
    async function evaluate(expression) {
        const result = await cdp('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    }
    if (resume || reuseFrom) {
        await wait(1500);
        await cdp('Page.navigate', {url:`chrome-extension://${extensionId}/search.html`});
        await wait(1000);
    }
    const environment = await evaluate(`(async () => {
        const {DictionaryDatabase} = await import('/js/dictionary/dictionary-database.js');
        const {DictionaryImporter} = await import('/js/dictionary/dictionary-importer.js');
        const {Translator} = await import('/js/language/translator.js');
        window.releaseDB = new DictionaryDatabase(); await releaseDB.prepare();
        window.releaseImporter = new DictionaryImporter({getImageDetails(){throw new Error('Unexpected media')}});
        window.releaseTranslator = new Translator(releaseDB); releaseTranslator.prepare();
        return {userAgent:navigator.userAgent, version:chrome.runtime.getManifest().version,
                existing:await releaseDB.getDictionaryInfo(), storage:await navigator.storage.estimate()};
    })()`);
    const hashes = Object.fromEntries(files.map(name=>[name,createHash('sha256').update(readFileSync(path.join(packs,name))).digest('hex')]));
    const report = resume ? JSON.parse(readFileSync(path.join(output, 'browser-validation.json'), 'utf8')) :
        {environment, imports: [], extension_origin: true, clean_profile: true, schema_validation: 'actual Yomitan importer',
        artifact_hashes:Object.fromEntries(files.map(name=>[name,createHash('sha256').update(readFileSync(path.join(packs,name))).digest('hex')]))};
    if (reuseFrom) {
        assert.equal(environment.existing.length, priorValidation.imports.length, 'Previous inventory changed');
        const reused = priorValidation.imports.filter(imported => files.includes(imported.file) &&
            hashes[imported.file] === priorValidation.artifact_hashes[imported.file]);
        for (const imported of priorValidation.imports) {
            const saved = environment.existing.find(item => item.title === imported.result.title);
            assert(saved && saved.revision === imported.result.revision && saved.importDate === imported.result.importDate, 'Previous inventory changed');
        }
        const keep = reused.map(imported => imported.result.title);
        const remove = environment.existing.filter(item => !keep.includes(item.title)).map(item => item.title);
        await evaluate(`(async()=>{for(const name of ${JSON.stringify(remove)}) await releaseDB.deleteDictionary(name,1000,()=>{});})()`);
        report.imports = reused;
        report.reused_validation = {directory: reuseFrom, files: reused.map(imported => imported.file), previous_hashes: priorValidation.artifact_hashes};
        console.log(JSON.stringify({reused: report.reused_validation.files, removed_from_isolated_copy: remove}));
    }
    if (resume) {
        assert.deepEqual(report.artifact_hashes, hashes, 'Changed archives require a fresh import');
        assert.equal(environment.version, report.environment.version, 'Extension version changed');
        assert.equal(report.imports.length, files.length, 'Resume requires all imports to be complete');
        assert.equal(environment.existing.length, files.length);
        for (const imported of report.imports) {
            assert.deepEqual(imported.errors, []);
            const saved = environment.existing.find(item => item.title === imported.result.title);
            assert(saved && saved.revision === imported.result.revision && saved.importDate === imported.result.importDate,
                'Imported inventory changed');
        }
        report.validation_resumed = true;
    } else if (!reuseFrom) assert.equal(environment.existing.length, 0);
    for (const name of resume ? [] : files.filter(name => !report.imports.some(imported => imported.file === name))) {
        const result = await evaluate(`(async () => {
            const bytes=await (await fetch(${JSON.stringify(packUrl + name)})).arrayBuffer();
            const start=performance.now();
            const result=await releaseImporter.importDictionary(releaseDB,bytes,{prefixWildcardsSupported:false,yomitanVersion:${JSON.stringify(manifest.version)}});
            return {seconds:(performance.now()-start)/1000,errors:result.errors.map(e=>String(e)),result:result.result,
                    storage:await navigator.storage.estimate()};
        })()`);
        assert.deepEqual(result.errors, [], `Import errors: ${name}`);
        report.imports.push({file: name, ...result});
        writeFileSync(path.join(output, 'browser-validation.json'), JSON.stringify(report, null, 2));
        console.log(JSON.stringify({file: name, seconds: result.seconds, storage: result.storage}));
    }
    const cases = [['evde','ev'], ['kitabı','kitap'], ['geliyorum','gelmek'], ['okumadım','okumak'],
        ['gerektiğini','gerekmek'], ['deyip','demek'], ['evlerimizden','ev'], ['yaptıklarımızdan','yapmak'],
        ['gözlükçü','gözlükçü'], ['orjinal','orijinal'], ['nerden','nereden'], ['ağlaştılar','ağlaşmak'], ['koskoca','koskoca']];
    // Pinned dictionary form absent from the complete observed corpus inventory.
    if (report.imports[0].result.revision.includes('dictionary-plus-corpus')) cases.push(['evlersin','ev']);
    report.lookups = await evaluate(`(async () => {
        async function lookup(word, names, forms=true, deinflections=true, language='tr', deinflect=true) {
            function contentText(node) {
                if (typeof node === 'string') return node;
                if (Array.isArray(node)) return node.map(contentText).join('');
                return contentText(node.content || []);
            }
            const dictionaries=forms?['Turkish Forms',...names]:[...names];
            if (${hasFrequency} && language === 'tr') dictionaries.push(${JSON.stringify(frequencyName)});
            const start=performance.now();
            const result=await releaseTranslator.findTerms('simple',word,{
                matchType:'exact',deinflect,mainDictionary:names[0],sortFrequencyDictionary:null,
                sortFrequencyDictionaryOrder:'descending',removeNonJapaneseCharacters:false,
                primaryReading:'',textReplacements:[null],
                enabledDictionaryMap:new Map(dictionaries.map((name,index)=>[name,{index,alias:name,
                    allowSecondarySearches:false,partsOfSpeechFilter:language !== 'en',useDeinflections:deinflections}])),
                excludeDictionaryDefinitions:null,searchResolution:'word',language,useAllFrequencyDictionaries:${hasFrequency} && language === 'tr',
            });
            return {word,ms:performance.now()-start,terms:result.dictionaryEntries.flatMap(e=>e.headwords.map(h=>h.term)),
                definitions:result.dictionaryEntries.flatMap(e=>e.definitions.map(d=>d.dictionary)),
                frequencies:result.dictionaryEntries.flatMap(e=>e.frequencies),
                translationText:contentText(result.dictionaryEntries.flatMap(e=>e.definitions.flatMap(d=>d.entries))),
                grammar:result.dictionaryEntries.flatMap(e=>e.inflectionRuleChainCandidates)};
        }
        const rows=[];
        if (${Boolean(definitionPacks)}) {
            for(const expected of ${JSON.stringify(release.lookup_expectations || [])}) {
                rows.push({...expected,...await lookup(expected.word,expected.names,expected.forms ?? true,
                    true,expected.language || 'tr',expected.deinflect ?? true)});
            }
        } else {
            for(const names of [['Turkish Monolingual'],['Turkish Bilingual'],['Turkish Monolingual','Turkish Bilingual']]){
                for(const [word,expected] of ${JSON.stringify(cases)}) rows.push({names,expected,...await lookup(word,names)});
            }
            for(const word of ['qzxqzx','evlerlerden','kitaplarden','geldimiz']) rows.push({negative:true,...await lookup(word,['Turkish Monolingual','Turkish Bilingual'])});
            rows.push({expected:'yine de',...await lookup('yinede',['Turkish Bilingual'])});
        }
        rows.push({disabledForms:true,...await lookup('kitabı',[${JSON.stringify(definitionNames[0])}],false)});
        rows.push({disabledDeinflections:true,...await lookup('kitabı',[${JSON.stringify(definitionNames[0])}],true,false)});
        for (const name of ${JSON.stringify(englishNames)}) {
            for (const [word, expected] of [['walked','walk'],['books','book']]) {
                rows.push({language:'en',expected,names:[name],...await lookup(word,[name],false,true,'en')});
            }
        }
        return rows;
    })()`);
    report.dictionaryInventory = await evaluate('releaseDB.getDictionaryInfo()');
    if (definitionPacks) {
        assert.equal(report.dictionaryInventory.length, files.length, 'Unexpected native inventory');
        for (const pack of definitionPacks) {
            const imported = report.dictionaryInventory.find(item => item.title === pack.title);
            assert(imported?.importSuccess, 'Dictionary import was incomplete: ' + pack.title);
            assert.equal(imported.counts.terms.total, release.definitions[pack.kind], 'Native headword count changed: ' + pack.title);
        }
    }
    writeFileSync(path.join(output, 'browser-validation.json'), JSON.stringify(report, null, 2));
    if (hasFrequency) {
        const kitab = report.lookups.find(row => row.word === 'kitabı');
        assert(kitab.frequencies.some(f => f.dictionary === frequencyName && f.frequency === 592), 'Headword frequency missing after deinflection');
        report.frequency_lookup_passed = true;
    }
    const uniqueSorted = values => [...new Set(values)].sort();
    report.failures = report.lookups.filter(row => row.expectedTerms ?
        JSON.stringify(uniqueSorted(row.terms)) !== JSON.stringify(uniqueSorted(row.expectedTerms)) ||
        JSON.stringify(uniqueSorted(row.definitions)) !== JSON.stringify(row.expectedDictionaries) :
        row.expected ? !row.terms.includes(row.expected) : row.terms.length > 0);
    report.failures.push(...report.lookups.filter(row => ['ağlaştılar', 'koskoca'].includes(row.word) &&
        (row.expected || row.expectedTerms?.includes(row.word === 'koskoca' ? 'koskoca' : 'ağlaşmak')) &&
        row.terms[0] !== (row.expected || (row.word === 'koskoca' ? 'koskoca' : 'ağlaşmak'))));
    report.failures.push(...report.lookups.filter(row => row.expectedTranslations?.some(text => !row.translationText.includes(text))));
    report.passed = report.failures.length === 0;
    writeFileSync(path.join(output, 'browser-validation.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({passed:report.passed, failures:report.failures, cases:report.lookups.length}));
    await evaluate('releaseDB.close()');
    // Close other pages in this isolated browser so stale settings controllers
    // cannot reconcile the new dictionary list against a pre-import inventory.
    const {targetInfos} = await cdp('Target.getTargets');
    for (const info of targetInfos) {
        if (info.type === 'page' && info.targetId !== target.id) {
            await cdp('Target.closeTarget', {targetId: info.targetId});
        }
    }
    await evaluate(`(async () => {
        const call=async(action,params)=>{const r=await chrome.runtime.sendMessage({action,params}); if(r.error)throw new Error(JSON.stringify(r.error));return r.result;};
        await call('triggerDatabaseUpdated',{type:'dictionary',cause:'import'});
        const options=await call('optionsGetFull');
        const profile=options.profiles[options.profileCurrent].options;
        profile.general.language='tr';profile.general.popupTheme='dark';
        profile.general.customPopupCss='';
        profile.translation.searchResolution='word';
        profile.dictionaries=${JSON.stringify(['Turkish Forms',...definitionNames,'Turkish Etymology', ...(hasFrequency ? [frequencyName] : [])])}.map(name=>({
            name,alias:'',enabled:true,allowSecondarySearches:false,definitionsCollapsible:'not-collapsible',partsOfSpeechFilter:true,useDeinflections:true}));
        await call('setAllSettings',{value:options,source:'isolated-release-validation'});
    })()`);
    await cdp('Emulation.setDeviceMetricsOverride',{width:1100,height:1200,deviceScaleFactor:1,mobile:false});
    await cdp('Page.bringToFront');
    await cdp('Page.navigate',{url:`chrome-extension://${extensionId}/search.html?query=${encodeURIComponent('kitabı')}`});
    let visibleText='';
    for(let i=0;i<300;i++){
        await wait(100);
        try {visibleText=await evaluate('document.body.innerText');} catch {continue;}
        if(visibleText.includes('kitap') && visibleText.includes('TDK'))break;
    }
    report.native_search_rendered=visibleText.includes('kitap') && visibleText.includes('TDK');
    report.native_location = await evaluate('({href:location.href,query:new URLSearchParams(location.search).get("query")})');
    report.browser_errors = browserErrors;
    report.native_options = await evaluate(`(async()=>{const r=await chrome.runtime.sendMessage({action:'optionsGetFull'});const o=r.result.profiles[r.result.profileCurrent].options;return {general:o.general,dictionaries:o.dictionaries,translation:o.translation};})()`);
    report.native_search_text=visibleText;
    if (definitionPacks) {
        report.source_titles_rendered = definitionNames.every(name => visibleText.includes(name === 'TR-EN Tureng' ? 'Tureng' : name));
        report.passed &&= report.source_titles_rendered;
    }
    if (hasFrequency) {
        report.frequency_rendered = visibleText.includes('592');
        report.passed &&= report.frequency_rendered;
    }
    report.wiktionary_first = visibleText.indexOf('Wiktionary') >= 0 && visibleText.indexOf('Wiktionary') < visibleText.indexOf('Tureng');
    report.passed &&= report.wiktionary_first;
    const screenshot=await cdp('Page.captureScreenshot',{format:'png'});
    writeFileSync(path.join(output,'native-search.png'),Buffer.from(screenshot.data,'base64'));
    report.passed &&= report.native_search_rendered;
    if (englishNames.length) {
        await evaluate(`(async()=>{
            const response=await chrome.runtime.sendMessage({action:'optionsGetFull'});
            const options=response.result;
            const current=options.profiles[options.profileCurrent].options;
            current.general.language='en';
            current.dictionaries=${JSON.stringify(englishNames)}.map(name=>({name,alias:'',enabled:true,
                allowSecondarySearches:false,definitionsCollapsible:'not-collapsible',partsOfSpeechFilter:false,useDeinflections:true}));
            const saved=await chrome.runtime.sendMessage({action:'setAllSettings',params:{value:options,source:'isolated-release-validation'}});
            if(saved.error)throw new Error(JSON.stringify(saved.error));
        })()`);
        await cdp('Page.navigate',{url:`chrome-extension://${extensionId}/search.html?query=house`});
        let englishText='';
        for(let i=0;i<300;i++){
            await wait(100);
            try {englishText=await evaluate('document.body.innerText');}catch{continue;}
            if(englishText.includes('house') && englishText.includes('Tureng') && englishText.includes('konut'))break;
        }
        report.english_search_text=englishText;
        report.english_search_rendered=englishText.includes('Tureng') && englishText.includes('ev') && englishText.includes('konut');
        report.passed &&= report.english_search_rendered;
        const englishScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
        writeFileSync(path.join(output,'native-search-english.png'),Buffer.from(englishScreenshot.data,'base64'));
    }
    // Browser.close flushes the isolated IndexedDB; directory bytes include its logs and metadata.
    try {await cdp('Browser.close');} catch {}
    await wait(1500);
    const indexed = path.join(profile, 'Default/IndexedDB');
    function size(folder) {return existsSync(folder) ? readdirSync(folder).reduce((n,f)=>{const p=path.join(folder,f); return n+(statSync(p).isDirectory()?size(p):statSync(p).size);},0):0;}
    report.indexedDB_directory_bytes = size(indexed);
    report.storage_note = reuseFrom ?
        'Isolated copy of previously validated IndexedDB, with unchanged archives retained and replaced dictionaries removed; includes database metadata/logs. Storage estimates are browser accounting, not ZIP sizes.' :
        'Isolated extension IndexedDB after clean imports; includes database metadata/logs. Storage estimates are browser accounting, not ZIP sizes.';
    writeFileSync(path.join(output, 'browser-validation.json'), JSON.stringify(report, null, 2));
    assert(report.passed, 'Whole-word lookup regressions failed; see browser-validation.json');
} finally {
    ws?.close();
    browser.kill();
    server.close();
}
