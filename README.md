# Turkish reading dictionaries

Turkish-to-Turkish, Turkish-to-English, and English-to-Turkish dictionaries in StarDict and Yomitan formats. Turkish lookup includes inflections and frequency ranks.

Each ZIP contains one dictionary or lookup resource. [Browse all releases](https://github.com/namidanokisetsu/turkdict/releases).

| Dictionary | Yomitan | StarDict for KOReader |
| --- | --- | --- |
| [TR-TR] TDK | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-tdk-yomitan.zip) | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-tdk-stardict.zip) |
| [TR-TR] KeNet | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-kenet-yomitan.zip) | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-kenet-stardict.zip) |
| [TR-EN] Tureng | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-tureng-yomitan.zip) | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-tureng-stardict.zip) |
| [EN-TR] Tureng | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/english-tureng-yomitan.zip) | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/english-tureng-stardict.zip) |
| [TR] Forms | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-forms-yomitan.zip) | Included in Turkish-source packs |
| [TR] Frequency | [Download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-frequency-yomitan.zip) | Included in Turkish-source packs |
| [TR] Morphology |  | [Optional download](https://github.com/namidanokisetsu/turkdict/releases/latest/download/turkish-morphology-stardict.zip) |

[Sources and how word forms work](SOURCES.md).

## KOReader (StarDict)

Extract each dictionary archive and keep its `.ifo`, `.idx`, and `.dict.dz` files together. In KOReader, copy the extracted folder into `data/dict/`, restart, and enable the dictionary in **Dictionary settings**.

Turkish inflected-word lookup is included. In KOReader, enable **fuzzy search** for English inflections. Tureng’s **More** link uses the reader’s `bword://` dictionary links.

## Yomitan

Import each dictionary ZIP directly. For Turkish lookup, add **Turkish Forms** and your chosen dictionaries. **Frequency** is optional.

Select **Turkish**, enable **Use deinflections** for **Turkish Forms**, and set search resolution to **Word**.

For English lookup, use an English profile with **EN-TR Tureng** only. In that dictionary's settings, turn off **Part of speech filtering** so inflected words can match their base forms.
