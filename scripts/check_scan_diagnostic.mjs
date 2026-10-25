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
async function run(resolution, includeForms = true, useDeinflections = true, dictionaries = ['Turkish Monolingual', 'Turkish Bilingual']) {
    const translator = new Translator({async findTermsBulk(terms, dictionaries) {
        return terms.flatMap((term, index) => records.flatMap((record, id) =>
            record.term === term && dictionaries.has(record.dictionary) ? [{
                ...record, index, id, matchType: 'exact', matchSource: 'term',
                reading: '', definitionTags: [], termTags: [], rules: [], score: 1, sequence: -1,
            }] : []));
    }});
    translator.prepare();
    return translator.findTerms('simple', 'okumadım', {
        matchType: 'exact', deinflect: true, mainDictionary: 'Turkish Monolingual',
        sortFrequencyDictionary: null, sortFrequencyDictionaryOrder: 'descending',
        removeNonJapaneseCharacters: false, primaryReading: '', textReplacements: [null],
        enabledDictionaryMap: new Map((includeForms ? ['Turkish Forms', ...dictionaries] : dictionaries).map((name, index) => [name, {
            index, alias: name, allowSecondarySearches: false, partsOfSpeechFilter: true, useDeinflections,
        }])),
        excludeDictionaryDefinitions: null, searchResolution: resolution, language: 'tr', useAllFrequencyDictionaries: false,
    });
}

for (const resolution of ['letter','word']) { const r=await run(resolution); console.log(JSON.stringify({resolution, originalTextLength:r.originalTextLength, entries:r.dictionaryEntries.map(e=>({term:e.headwords[0].term,sources:e.headwords[0].sources,reasons:e.inflectionRuleChainCandidates}))})); }
