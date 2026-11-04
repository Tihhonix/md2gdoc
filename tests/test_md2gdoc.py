import base64
from pathlib import Path

import pytest

from md2gdoc.cli import main
from md2gdoc.collect import collect_markdown
from md2gdoc.render import PAGE_BREAK, detect_title, render_files

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------- collect


def test_natural_order_and_hidden_skipped(tmp_path):
    for name in ["10-end.md", "2-middle.md", "1-start.md", "notes.txt", ".hidden.md", ".obsidian/x.md"]:
        write(tmp_path / name, "x")
    write(tmp_path / "sub" / "a.md", "x")
    files = [p.relative_to(tmp_path).as_posix() for p in collect_markdown([tmp_path])]
    assert files == ["1-start.md", "2-middle.md", "10-end.md", "sub/a.md"]


def test_no_recursive(tmp_path):
    write(tmp_path / "a.md", "x")
    write(tmp_path / "sub" / "b.md", "x")
    assert [p.name for p in collect_markdown([tmp_path], recursive=False)] == ["a.md"]


def test_explicit_order_kept_and_deduplicated(tmp_path):
    b = write(tmp_path / "b.md", "x")
    a = write(tmp_path / "a.md", "x")
    assert collect_markdown([b, a, b]) == [b, a]


def test_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_markdown([tmp_path / "nope.md"])


# ------------------------------------------------------------- titles


def test_title_detection(tmp_path):
    p = tmp_path / "file.md"
    assert detect_title(p, "---\ntitle: 'From FM'\n---\n# H1\n") == "From FM"
    assert detect_title(p, "---\nauthor: x\n---\n# Заголовок\n") == "Заголовок"
    assert detect_title(p, "no heading here") == "file"


# ------------------------------------------------------------- render


def test_basic_render(tmp_path):
    f = write(
        tmp_path / "doc.md",
        "---\ntitle: T\n---\n# Привет\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\n"
        "```python\nprint('hi')\n```\n\n- [x] done\n\n`inline`\n\n> quote\n",
    )
    r = render_files([f])
    assert r.title == "T"
    assert "title: T" not in r.html  # front matter stripped
    assert "<h1>Привет</h1>" in r.html
    assert 'style="text-align:left; border: 1px solid' in r.html
    assert "Courier New" in r.html and "print" in r.html
    assert 'type="checkbox"' in r.html
    assert "border-left" in r.html
    assert PAGE_BREAK not in r.html  # single file: no separator
    assert not r.warnings


def test_merge_with_page_breaks_titles_and_links(tmp_path):
    a = write(tmp_path / "a.md", "# A\n\nSee [b](b.md) and [x](missing.md).\n\n## Sub\n")
    b = write(tmp_path / "b.md", "Body of b[^1]\n\n[^1]: note\n")
    r = render_files([a, b], title="Merged", file_titles=True)
    assert r.title == "Merged"
    assert r.html.count(PAGE_BREAK) == 1
    assert '<a href="#md2gdoc-1">b</a>' in r.html
    assert 'id="md2gdoc-1"' in r.html
    # headings shifted under the per-file H1
    assert "<h2>A</h2>" in r.html and "<h3>Sub</h3>" in r.html
    assert "<h1>b</h1>" in r.html
    assert any("missing.md" in w for w in r.warnings)


def test_footnote_ids_unique_across_files(tmp_path):
    a = write(tmp_path / "a.md", "x[^1]\n\n[^1]: a\n")
    b = write(tmp_path / "b.md", "y[^1]\n\n[^1]: b\n")
    r = render_files([a, b])
    assert 'id="fn-f0-1"' in r.html and 'id="fn-f1-1"' in r.html


def test_images(tmp_path):
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "my pic.png").write_bytes(PNG_1PX)
    (tmp_path / "logo.svg").write_text("<svg/>")
    f = write(
        tmp_path / "doc.md",
        "![ok](img/my%20pic.png) ![svg](logo.svg) ![gone](nope.png) ![web](https://example.com/a.png)\n",
    )
    r = render_files([f])
    assert 'src="data:image/png;base64,' in r.html
    assert "[image: svg]" in r.html and "[image: gone]" in r.html
    assert 'src="https://example.com/a.png"' in r.html
    assert len(r.warnings) == 2


def test_diagram_warning(tmp_path):
    f = write(tmp_path / "d.md", "```mermaid\ngraph TD; A-->B\n```\n")
    assert any("mermaid" in w for w in render_files([f]).warnings)


def test_html_is_escaped_in_code(tmp_path):
    f = write(tmp_path / "c.md", "`<b>`\n\n```\n<script>\n```\n")
    r = render_files([f])
    assert "&lt;b&gt;" in r.html and "&lt;script&gt;" in r.html


# ------------------------------------------------------------- cli


def test_cli_dry_run(tmp_path, capsys):
    d = tmp_path / "book"
    write(d / "1.md", "# One\n")
    write(d / "2.md", "# Two\n")
    out = tmp_path / "out.html"
    assert main([str(d), "--dry-run", "--html-out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "<title>book</title>" in html
    assert html.index("One") < html.index("Two")


def test_cli_no_files(tmp_path):
    assert main([str(tmp_path), "--dry-run"]) == 2
