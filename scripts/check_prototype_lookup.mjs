import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
// The research checkout has no bundled Japanese/Korean libraries. Fail if this
// Turkish-only test unexpectedly tries to use either of these dependencies.
registerHooks({resolve(specifier, context, nextResolve) {
    if (specifier.endsWith('/lib/kanji-processor.js')) {
        return {url: 'data:text/javascript,export function convertVariants(){throw new Error("Japanese dependency used");}', shortCircuit: true};
    }
    if (specifier.endsWith('/lib/hangul-js.js')) {
        return {url: 'data:text/javascript,export const Hangul=new Proxy({},{get(){throw new Error("Korean dependency used");}});', shortCircuit: true};
    }
    return nextResolve(specifier, context);
}});
const {Translator} = await import('../research/yomitan/ext/js/language/translator.js');

import {readFileSync} from 'node:fs';
const records = JSON.parse(readFileSync(new URL('../build/prototype/lookup-fixture.json', import.meta.url),'utf8'));
async function run(includeForms = true, useDeinflections = true, dictionaries = ['Turkish Monolingual', 'Turkish Bilingual']) {
    const translator = new Translator({async findTermsBulk(terms, dictionaries) {
        return terms.flatMap((term, index) => records.flatMap((record, id) =>
            record.term === term && dictionaries.has(record.dictionary) ? [{
                ...record, index, id, matchType: 'exact', matchSource: 'term',
                reading: '', definitionTags: [], termTags: [], rules: [], score: 1, sequence: -1,
            }] : []));
    }});
    translator.prepare();
    return translator.findTerms('simple', 'kitabı', {
        matchType: 'exact', deinflect: true, mainDictionary: 'Turkish Monolingual',
        sortFrequencyDictionary: null, sortFrequencyDictionaryOrder: 'descending',
        removeNonJapaneseCharacters: false, primaryReading: '', textReplacements: [null],
        enabledDictionaryMap: new Map((includeForms ? ['Turkish Forms', ...dictionaries] : dictionaries).map((name, index) => [name, {
            index, alias: name, allowSecondarySearches: false, partsOfSpeechFilter: true, useDeinflections,
        }])),
        excludeDictionaryDefinitions: null, searchResolution: 'word', language: 'tr', useAllFrequencyDictionaries: false,
    });
}
const result = await run();
assert.equal(result.dictionaryEntries.length, 2);
assert.deepEqual(new Set(result.dictionaryEntries.flatMap(e => e.definitions.map(d => d.dictionary))), new Set(['Turkish Monolingual', 'Turkish Bilingual']));
for (const entry of result.dictionaryEntries) {
    assert.equal(entry.headwords[0].term, 'kitap');
    assert(entry.inflectionRuleChainCandidates.some(c => c.source === 'dictionary' && c.inflectionRules.some(r => r.name.includes('accusative (direct object)') && r.name.includes(' OR '))));
}
assert.equal((await run(false)).dictionaryEntries.length, 0);
assert.equal((await run(true, false)).dictionaryEntries.length, 0);
console.log(JSON.stringify({passed: true, entries: result.dictionaryEntries.map(e => ({term: e.headwords[0].term, dictionaries: e.definitions.map(d => d.dictionary), reasons: e.inflectionRuleChainCandidates}))}, null, 2));

for (const name of ['Turkish Monolingual', 'Turkish Bilingual', 'Turkish Etymology']) { const r = await run(true, true, [name]); assert.equal(r.dictionaryEntries.length, 1); assert.equal(r.dictionaryEntries[0].headwords[0].term, 'kitap'); }
console.log('Independent mono and bilingual lookup passed');
