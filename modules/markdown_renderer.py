"""
Markdown Renderer - Converts markdown to HTML with syntax highlighting
"""
import html as html_lib
import re

import markdown
import nh3


# Raw HTML embedded in reports passes through Python-Markdown untouched, so the
# rendered output is sanitized against an allowlist before it is marked safe.
ALLOWED_TAGS = {
    'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'dd', 'del', 'div', 'dl',
    'dt', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins',
    'li', 'ol', 'p', 'pre', 'span', 'strong', 'sub', 'sup', 'table', 'tbody',
    'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
}
ALLOWED_ATTRIBUTES = {
    '*': {'class', 'id'},
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title'},
    'th': {'align'},
    'td': {'align'},
}
ALLOWED_URL_SCHEMES = {'http', 'https', 'mailto'}


def sanitize_html(html: str) -> str:
    """Strip scripts, event handlers and unsafe URL schemes from HTML"""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel='noopener noreferrer',
    )


_LEADING_H1 = re.compile(r'^\s*<h1\b[^>]*>.*?</h1>\s*', re.DOTALL)


class MarkdownRenderer:
    @staticmethod
    def _new_markdown() -> markdown.Markdown:
        # A fresh converter per report: Flask serves requests on several
        # threads, and one shared converter mixes their output and headings.
        return markdown.Markdown(
            extensions=[
                'extra',
                'codehilite',
                'fenced_code',
                'tables',
                'toc',
                'nl2br',
                'sane_lists'
            ],
            extension_configs={
                'codehilite': {
                    'css_class': 'highlight',
                    'linenums': False
                }
            }
        )

    def render(self, content: str, strip_title: bool = False) -> str:
        """Render markdown to sanitized HTML.

        strip_title drops a leading H1 so a page that already shows the
        report title in its own header doesn't repeat it.
        """
        return self.render_with_contents(content, strip_title)[0]

    def render_with_contents(self, content: str, strip_title: bool = False,
                             max_level: int = 3) -> tuple:
        """Sanitized HTML and the report's headings as (level, id, text)
        for a table of contents; a stripped title is left out."""
        md = self._new_markdown()
        html = sanitize_html(md.convert(content))
        if strip_title:
            html = _LEADING_H1.sub('', html, count=1)
        return html, _contents(getattr(md, 'toc_tokens', []), strip_title, max_level)

    def extract_title(self, content: str) -> str:
        """Extract title from markdown (first H1)"""
        lines = content.split('\n')
        for line in lines:
            if line.startswith('# '):
                return line[2:].strip()
        return "Untitled Report"


def _contents(tokens: list, strip_title: bool, max_level: int) -> list:
    found = []

    def walk(level_tokens):
        for token in level_tokens:
            if token['level'] <= max_level:
                found.append((token['level'], token['id'], html_lib.unescape(token['name'])))
            walk(token['children'])

    if strip_title and tokens and tokens[0]['level'] == 1:
        tokens = tokens[0]['children'] + tokens[1:]
    walk(tokens)
    if found:
        top = min(level for level, _, _ in found)
        found = [(level - top + 1, anchor, text) for level, anchor, text in found]
    return found
