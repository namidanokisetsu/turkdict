# Sources and word forms

## Dictionaries

- **TDK:** Turkish definitions from the [Güncel Türkçe Sözlük SQLite snapshot](https://github.com/ogun/guncel-turkce-sozluk).
- **Tureng:** Turkish–English and English–Turkish entries from the [Tureng database export](https://github.com/helallao/tureng).
- **Wiktionary:** Turkish entries from the [WTY export](https://huggingface.co/datasets/daxida/wty-release), including definitions, etymology, and links between word forms.
- **KeNet:** Turkish definitions from [Turkish WordNet](https://github.com/StarlangSoftware/TurkishWordNet-Py).

The [source list](config/sources.lock.json) records the snapshots, download URLs, and checksums used.

## Turkish Forms

Forms links inflected spellings to dictionary headwords. The build collects spellings from the [MaCoCu Turkish web corpus](https://www.clarin.si/repository/xmlui/handle/11356/1802) and [OpenSubtitles frequency list](https://github.com/hermitdave/FrequencyWords/tree/master/content/2018/tr), then analyzes them with [Zemberek](https://github.com/ahmetaa/zemberek-nlp). It keeps links whose headwords have definitions in the exported dictionaries.

Wiktionary supplies additional direct links, including forms not found in those corpora.

## Frequency

Ranks combine MaCoCu and OpenSubtitles rankings with equal weight, using reciprocal rank fusion. Lower numbers mean more frequent spellings. Inflected spellings are ranked separately, not added to their headword's total. Headwords absent from the inventory have no rank. These are corpus estimates, not vocabulary levels.

See [BUILD.md](BUILD.md) for build instructions.
