"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .collect import collect_markdown
from .render import render_files


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="md2gdoc",
        description="Convert Markdown file(s) into a single Google Doc.",
    )
    p.add_argument("inputs", nargs="+", type=Path, help="Markdown files and/or directories (order is preserved)")
    p.add_argument("-t", "--title", help="Document title (default: first file's title / directory name)")
    p.add_argument("--folder-id", help="Drive folder ID to create the doc in (usually needs --scope full)")
    p.add_argument("--update", metavar="DOC_ID", help="Overwrite an existing Google Doc instead of creating a new one")
    p.add_argument("--no-recursive", action="store_true", help="Do not descend into subdirectories")
    p.add_argument(
        "--separator",
        choices=["pagebreak", "hr", "none"],
        default="pagebreak",
        help="What to put between files when merging (default: pagebreak)",
    )
    p.add_argument(
        "--file-titles",
        action="store_true",
        help="Start each file with an H1 of its title and shift its own headings one level down",
    )
    p.add_argument("--html-out", type=Path, help="Also save the generated HTML to this path")
    p.add_argument("--dry-run", action="store_true", help="Do not upload; just render (use with --html-out)")
    p.add_argument("--credentials", type=Path, help="Path to OAuth client JSON (default: ~/.config/md2gdoc/credentials.json)")
    p.add_argument("--scope", choices=["file", "full"], default="file", help="Drive access scope (default: file)")
    p.add_argument("--no-browser", action="store_true", help="Print the auth URL instead of opening a browser")
    p.add_argument("--open", action="store_true", help="Open the resulting document in the browser")
    return p


def default_title(inputs: list[Path]) -> str | None:
    if len(inputs) == 1 and inputs[0].is_dir():
        return inputs[0].resolve().name
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.update and args.folder_id:
        print("error: --update and --folder-id cannot be used together", file=sys.stderr)
        return 2

    try:
        files = collect_markdown(args.inputs, recursive=not args.no_recursive)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not files:
        print("error: no Markdown files found", file=sys.stderr)
        return 2

    result = render_files(
        files,
        title=args.title or default_title(args.inputs),
        file_titles=args.file_titles,
        separator=args.separator,
    )

    print(f"Files ({len(files)}):", file=sys.stderr)
    for f in files:
        print(f"  {f}", file=sys.stderr)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.html_out:
        args.html_out.write_text(result.html, encoding="utf-8")
        print(f"HTML written to {args.html_out}", file=sys.stderr)

    if args.dry_run:
        print(f"Dry run: '{result.title}', {len(result.html.encode()):,} bytes of HTML, not uploaded.", file=sys.stderr)
        return 0

    from .gdrive import AuthError, get_credentials, upload_html

    try:
        creds = get_credentials(args.scope, args.credentials, open_browser=not args.no_browser)
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    from googleapiclient.errors import HttpError

    try:
        doc = upload_html(creds, result.html, result.title, folder_id=args.folder_id, update_id=args.update)
    except HttpError as e:
        print(f"error: Google API: {e}", file=sys.stderr)
        if e.resp.status in (403, 404) and (args.folder_id or args.update) and args.scope == "file":
            print("hint: with --scope file the app can only touch files/folders it created; try --scope full", file=sys.stderr)
        return 1

    link = doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit"
    print(link)
    if args.open:
        webbrowser.open(link)
    return 0
