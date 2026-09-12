"""Deterministic, fictional industrial customer fixtures; no I/O or credentials."""

from leads.industrial import BUSINESS_LINES


MARKER = 'SYNTHETIC DEMO / DO NOT CONTACT / 虚构演示数据'
SOURCES = (
    ('manual', 'manual_create'),
    ('research', 'file_import'),
    ('website_form', 'automatic_receive'),
    ('meta_native', 'automatic_receive'),
    ('other', 'file_import'),
)
COUNTRIES = ('China', 'Ghana', 'Kenya', 'Egypt', 'Saudi Arabia',
             'United Arab Emirates', 'Morocco', 'South Africa')


def customer_fixtures():
    """Return 200 rows, not actual platform receipts or verified contact consent."""
    lines = list(BUSINESS_LINES.items())
    rows = []
    for index in range(200):
        number = index + 1
        business, industry = lines[index % len(lines)]
        source, intake = SOURCES[index % len(SOURCES)]
        rows.append({
            'number': number,
            'name': f'Vorntek Synthetic Company {number:03d}',
            'website': f'https://company-{number:03d}.example.invalid',
            'email': f'contact{number:03d}@example.invalid',
            'industry': industry, 'business_line': business,
            'country': COUNTRIES[index % len(COUNTRIES)],
            'source_type': source, 'intake_method': intake,
            'state': 'available' if index < 150 else 'review' if index < 190 else 'archived',
        })
    return rows
