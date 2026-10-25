"""Definition packs shared by exporters and installation packaging."""

SOURCE_PACKS = [
    dict(kind='tdk', title='TDK', language='tr', source='tdk'),
    dict(kind='wiktionary', title='Wiktionary', language='en', source='wty'),
    dict(kind='tureng', title='TR-EN Tureng', language='en', source='tureng'),
    dict(kind='kenet', title='KeNet', language='tr', source='kenet'),
]

ENGLISH_TURENG = dict(kind='tureng-en-tr', basename='english-tureng', title='EN-TR Tureng',
                     source_language='en', language='tr', source='tureng')


def source_language(pack):
    return pack.get('source_language', 'tr')


def pack_basename(pack):
    return pack.get('basename', 'turkish-' + pack['kind'])


def installation_basename(pack):
    labels = {'tdk': 'TDK', 'wiktionary': 'Wiktionary', 'tureng': 'Tureng',
              'tureng-en-tr': 'Tureng', 'kenet': 'KeNet', 'etymology': 'Etymology',
              'mono': 'Monolingual', 'bilingual': 'Bilingual',
              'forms': 'Forms', 'frequency': 'Frequency', 'morphology': 'Morphology'}
    kind = pack['kind']
    language = source_language(pack).upper()
    direction = language if kind in ('forms', 'frequency', 'morphology') else language + '-' + pack['language'].upper()
    if kind in ('frequency', 'etymology'):
        return '[' + kind.upper() + '] [' + direction + ']'
    recommended = '[RECOMMENDED] ' if kind == 'forms' else ''
    return recommended + '[' + direction + '] ' + labels.get(kind, pack.get('title', kind))


def definition_packs(report):
    return report.get('definition_packs') or [
        dict(kind='mono', title='Turkish Monolingual', language='tr'),
        dict(kind='bilingual', title='Turkish Bilingual', language='en'),
    ]
