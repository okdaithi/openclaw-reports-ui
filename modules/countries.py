"""The countries the agent reports on: database code, name and report series.

Country reports are files named mika-<slug>-YYYY-MM-DD.md (series
"mika-<slug>"), while the database stores ISO 3166 alpha-3 codes.
"""
from typing import Dict, Optional

# code: (name, slug)
COUNTRIES = {
    'ARE': ('UAE', 'uae'),
    'AUS': ('Australia', 'australia'),
    'BRA': ('Brazil', 'brazil'),
    'CHN': ('China', 'china'),
    'DEU': ('Germany', 'germany'),
    'GBR': ('UK', 'uk'),
    'IDN': ('Indonesia', 'indonesia'),
    'IND': ('India', 'india'),
    'IRL': ('Ireland', 'ireland'),
    'JPN': ('Japan', 'japan'),
    'KOR': ('South Korea', 'south-korea'),
    'RUS': ('Russia', 'russia'),
    'SAU': ('Saudi Arabia', 'saudi-arabia'),
    'TUR': ('Turkey', 'turkey'),
    'USA': ('USA', 'usa'),
    'ZAF': ('South Africa', 'south-africa'),
}
COUNTRY_NAMES = {code: name for code, (name, _) in COUNTRIES.items()}
_BY_SLUG = {slug: code for code, (_, slug) in COUNTRIES.items()}


def by_slug(slug: str) -> Optional[Dict]:
    """{'code', 'name', 'slug', 'series'} for a URL slug like 'south-korea'"""
    code = _BY_SLUG.get(slug)
    if code is None:
        return None
    return describe(code)


def describe(code: str) -> Dict:
    name, slug = COUNTRIES[code]
    return {'code': code, 'name': name, 'slug': slug, 'series': f'mika-{slug}'}


def all_countries():
    """Every country, by name"""
    return sorted((describe(code) for code in COUNTRIES), key=lambda c: c['name'])
