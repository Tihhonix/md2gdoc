"""Markdown -> HTML tailored for Google Drive's HTML-to-Docs importer.

Google's importer ignores most CSS from <style> blocks, so every visual
property we care about is set as an inline style attribute.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .collect import MD_EXTENSIONS

MONO = "font-family: 'Courier New', Courier, monospace"
PRE_STYLE = f"{MONO}; font-size: 9pt; background-color: #f6f8fa; padding: 8px"
CODE_INLINE_STYLE = f"{MONO}; background-color: #f2f2f2"
TABLE_STYLE = "border-collapse: collapse"
CELL_STYLE = "border: 1px solid #999999; padding: 4px 8px"
TH_EXTRA = "background-color: #f2f2f2; font-weight: bold"
BLOCKQUOTE_STYLE = "border-left: 3px solid #cccccc; padding-left: 12px; color: #555555"

# Google Docs' own HTML export emits exactly this for a page break,
# so the importer recognises it.
PAGE_BREAK = '<hr style="page-break-before: always; display: none;">'

# Formats Google Docs can display. Anything else is reported, not embedded.
SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}
MAX_IMAGE_BYTES = 50 * 1024 * 1024  # Google Docs per-image limit
MAX_DOC_CHARS = 1_020_000  # Google Docs document size limit (characters)

_FORMATTER = HtmlFormatter(noclasses=True, nowrap=True, style="default")


@dataclass
class SourceDoc:
    path: Path
    text: str
    title: str
    index: int  # position in the merged document

    @property
    def anchor(self) -> str:
        return f"md2gdoc-{self.index}"


@dataclass
class RenderResult:
    html: str
    title: str
    warnings: list[str] = field(default_factory=list)
    char_estimate: int = 0


# ---------------------------------------------------------------- helpers

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
_FM_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.M)
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)


def detect_title(path: Path, text: str) -> str:
    """Front-matter `title:` > first `# H1` > file name."""
    fm = _FRONT_MATTER_RE.match(text)
    if fm:
        m = _FM_TITLE_RE.search(fm.group(1))
        if m:
            return m.group(1).strip().strip("'\"")
        text = text[fm.end():]
    m = _H1_RE.search(text)
    if m:
        return m.group(1).strip()
    return path.stem


def read_markdown(path: Path) -> str:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")  # also strips a BOM
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    return text.replace("\r\n", "\n")


def _highlight(code: str, lang: str) -> str:
    if lang:
        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            lexer = None
        if lexer is not None:
            return pygments_highlight(code, lexer, _FORMATTER).rstrip("\n")
    return html.escape(code.rstrip("\n"))


def _render_fence(self, tokens, idx, options, env):
    token = tokens[idx]
    lang = token.info.strip().split()[0] if token.info.strip() else ""
    return f'<pre style="{PRE_STYLE}"><code>{_highlight(token.content, lang)}</code></pre>\n'


def _render_code_inline(self, tokens, idx, options, env):
    return f'<code style="{CODE_INLINE_STYLE}">{html.escape(tokens[idx].content)}</code>'


def _add_style(token: Token, style: str) -> None:
    existing = token.attrGet("style")
    token.attrSet("style", f"{existing}; {style}" if existing else style)


def build_parser() -> MarkdownIt:
    md = (
        MarkdownIt("commonmark", {"html": True, "typographer": False})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin)
    )
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_fence)
    md.add_render_rule("code_inline", _render_code_inline)
    return md


# ---------------------------------------------------------------- renderer


class Renderer:
    def __init__(
        self,
        docs: list[SourceDoc],
        *,
        file_titles: bool = False,
        separator: str = "pagebreak",
    ) -> None:
        self.docs = docs
        self.file_titles = file_titles
        self.separator = separator
        self.md = build_parser()
        self.warnings: list[str] = []
        self.merged = len(docs) > 1
        self._by_path = {d.path.resolve(): d for d in docs}

    def warn(self, doc: SourceDoc, msg: str) -> None:
        self.warnings.append(f"{doc.path}: {msg}")

    # -- token post-processing ------------------------------------------

    def _process(self, doc: SourceDoc, tokens: list[Token]) -> list[Token]:
        shift = 1 if self.file_titles else 0
        for token in tokens:
            if token.type in ("heading_open", "heading_close") and shift:
                level = min(int(token.tag[1:]) + shift, 6)
                token.tag = f"h{level}"
            elif token.type == "table_open":
                _add_style(token, TABLE_STYLE)
            elif token.type in ("th_open", "td_open"):
                _add_style(token, CELL_STYLE)
                if token.type == "th_open":
                    _add_style(token, TH_EXTRA)
            elif token.type == "blockquote_open":
                _add_style(token, BLOCKQUOTE_STYLE)
            elif token.type == "fence":
                lang = token.info.strip().split()[0].lower() if token.info.strip() else ""
                if lang in ("mermaid", "plantuml", "dot", "graphviz"):
                    self.warn(doc, f"'{lang}' diagram is not rendered, it will appear as code")
                elif lang in ("math", "latex", "katex"):
                    self.warn(doc, "math block is not rendered, it will appear as code")
            elif token.type == "inline" and token.children:
                token.children = self._process_inline(doc, token.children)
        return tokens

    def _process_inline(self, doc: SourceDoc, children: list[Token]) -> list[Token]:
        out: list[Token] = []
        for child in children:
            if child.type == "image":
                out.append(self._process_image(doc, child))
            elif child.type == "link_open":
                self._process_link(doc, child)
                out.append(child)
            else:
                out.append(child)
        return out

    def _process_image(self, doc: SourceDoc, token: Token) -> Token:
        src = str(token.attrGet("src") or "")
        scheme = urlsplit(src).scheme.lower()
        alt = token.content or ""

        if scheme in ("http", "https"):
            # Google fetches it during import - only works for public URLs.
            return token
        if scheme == "data":
            return token

        local = (doc.path.parent / unquote(urlsplit(src).path)).resolve()
        problem = None
        if not local.is_file():
            problem = "not found"
        else:
            mime = mimetypes.guess_type(local.name)[0] or ""
            size = local.stat().st_size
            if mime == "image/svg+xml":
                problem = "SVG is not supported by Google Docs (convert to PNG)"
            elif mime not in SUPPORTED_IMAGE_TYPES:
                problem = f"unsupported image type '{mime or local.suffix}'"
            elif size > MAX_IMAGE_BYTES:
                problem = f"too large ({size // 1024 // 1024} MB > 50 MB)"

        if problem:
            self.warn(doc, f"image '{src}' skipped: {problem}")
            placeholder = Token("text", "", 0)
            placeholder.content = f"[image: {alt or src}]"
            return placeholder

        data = base64.b64encode(local.read_bytes()).decode("ascii")
        token.attrSet("src", f"data:{mime};base64,{data}")
        return token

    def _process_link(self, doc: SourceDoc, token: Token) -> None:
        href = str(token.attrGet("href") or "")
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or not parts.path:
            return  # external link or pure #fragment
        if Path(parts.path).suffix.lower() not in MD_EXTENSIONS:
            return
        target = (doc.path.parent / unquote(parts.path)).resolve()
        linked = self._by_path.get(target)
        if self.merged and linked is not None:
            token.attrSet("href", f"#{linked.anchor}")
        else:
            self.warn(doc, f"link to '{href}' will be broken in Google Docs")

    # -- assembly ---------------------------------------------------------

    def render_doc(self, doc: SourceDoc) -> str:
        env = {"docId": f"f{doc.index}"}  # keeps footnote ids unique when merged
        tokens = self._process(doc, self.md.parse(doc.text, env))
        body = self.md.renderer.render(tokens, self.md.options, env)
        parts = []
        if self.merged:
            parts.append(f'<a id="{doc.anchor}" name="{doc.anchor}"></a>')
        if self.file_titles:
            parts.append(f"<h1>{html.escape(doc.title)}</h1>")
        parts.append(body)
        return "\n".join(parts)

    def render(self, title: str) -> RenderResult:
        sep = {"pagebreak": PAGE_BREAK, "hr": "<hr>", "none": ""}[self.separator]
        sections = [self.render_doc(d) for d in self.docs]
        body = f"\n{sep}\n".join(sections)
        page = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '<meta charset="utf-8">\n'
            f"<title>{html.escape(title)}</title>\n"
            f"</head>\n<body>\n{body}\n</body>\n</html>\n"
        )
        chars = sum(len(d.text) for d in self.docs)
        if chars > MAX_DOC_CHARS:
            self.warnings.append(
                f"source is ~{chars:,} characters; Google Docs limit is ~{MAX_DOC_CHARS:,}. "
                "Import will likely fail - split the input."
            )
        return RenderResult(html=page, title=title, warnings=self.warnings, char_estimate=chars)


def render_files(
    paths: list[Path],
    *,
    title: str | None = None,
    file_titles: bool = False,
    separator: str = "pagebreak",
) -> RenderResult:
    docs = []
    for i, p in enumerate(paths):
        text = read_markdown(p)
        docs.append(SourceDoc(path=p, text=text, title=detect_title(p, text), index=i))
    final_title = title or docs[0].title
    return Renderer(docs, file_titles=file_titles, separator=separator).render(final_title)
