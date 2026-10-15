"""Deterministic fictional customer examples, independent of industry enums.

This legacy optional customer-only fixture is not the complete product example
bundle or its import/reset lifecycle. Values live in a separate data file.
"""

import json
from pathlib import Path


EXAMPLES = json.loads((Path(__file__).parent / 'data/customer_examples.json').read_text(encoding='utf-8'))
MARKER = EXAMPLES['notice']
SOURCES = tuple(tuple(source) for source in EXAMPLES['sources'])
COUNTRIES = tuple(EXAMPLES['countries'])


def customer_fixtures():
    """Return 200 rows, not actual platform receipts or verified contact consent."""
    contexts = EXAMPLES['contexts']
    rows = []
    for index in range(EXAMPLES['count']):
        number = index + 1
        context = contexts[index % len(contexts)]
        source, intake = SOURCES[index % len(SOURCES)]
        rows.append({
            'number': number,
            'name': f'Vorntek Synthetic Company {number:03d}',
            'website': f'https://company-{number:03d}.example.invalid',
            'email': f'contact{number:03d}@example.invalid',
            'industry': context['label'], 'business_line': context['id'],
            'country': COUNTRIES[index % len(COUNTRIES)],
            'source_type': source, 'intake_method': intake,
            'state': next(item['state'] for item in EXAMPLES['states'] if number <= item['until']),
        })
    return rows
