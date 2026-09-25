"""Flat frontmatter parsing and rendering, Markdown sections, and checkboxes."""

from __future__ import annotations

import re

from .common import die


def parse(text: str) -> tuple[dict[str, str], str]:
    """Split flat YAML-ish frontmatter from the body.

    A comment starts at a `#` that begins a line or follows whitespace, as in `sh`,
    so `echo a#b`, a URL fragment, or `$#` in a verify command stays whole. A byte
    order mark an editor added is not part of the document.
    """
    text = text.removeprefix("\ufeff")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if line.strip().startswith("#"):
            line = ""
        else:
            # strip inline comments only when # is outside quotes
            in_single = False
            in_double = False
            cut = None
            for i, ch in enumerate(line):
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif ch == "#" and not in_single and not in_double \
                        and (i == 0 or line[i - 1] in " \t"):
                    cut = i
                    break
            if cut is not None:
                line = line[:cut].rstrip()
        if not line.strip() or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body


def render(meta: dict[str, str], body: str) -> str:
    for key, value in meta.items():
        if "\n" in str(value) or "\r" in str(value):
            die(f"frontmatter {key!r} cannot contain a line break, since each line is one key: "
                f"{str(value)!r}")
    lines = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return f"---\n{lines}\n---\n\n{body.lstrip()}"


CODE_FENCE = re.compile(r"^\s*(```|~~~)")


def heading_lines(body: str) -> list[tuple[int, str]]:
    """(line index, name) of every `## ` heading outside a code fence.

    Pasted output often carries Markdown of its own; a heading inside a fence is
    content, never a new section of the document around it.
    """
    out: list[tuple[int, str]] = []
    fence = ""
    for index, line in enumerate(body.splitlines()):
        marker = CODE_FENCE.match(line)
        if marker:
            if not fence:
                fence = marker.group(1)
            elif marker.group(1) == fence:
                fence = ""
            continue
        if fence:
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            out.append((index, heading.group(1)))
    return out


def sections(body: str) -> dict[str, str]:
    """Map '## Heading' -> its content. A repeated heading keeps its first section,
    the same one `set_section` edits; `duplicate_sections` names the repeat."""
    lines = body.splitlines()
    heads = heading_lines(body)
    out: dict[str, str] = {}
    for n, (index, name) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        out.setdefault(name, "\n".join(lines[index + 1:end]).strip())
    return out


def duplicate_sections(body: str, names: list[str]) -> list[str]:
    """Required headings that appear more than once, which make a document ambiguous."""
    found = [name for _, name in heading_lines(body)]
    return [name for name in names if found.count(name) > 1]


def set_section(body: str, name: str, content: str) -> str:
    """Replace one '## name' section's content, appending the section when absent."""
    lines = body.splitlines(keepends=True)
    heads = heading_lines(body)
    target = next((n for n, (_, heading) in enumerate(heads) if heading == name), None)
    if target is None:
        return body.rstrip("\n") + f"\n\n## {name}\n\n{content}\n"
    index = heads[target][0]
    end = heads[target + 1][0] if target + 1 < len(heads) else len(lines)
    head = "".join(lines[:index + 1]).rstrip("\n")
    return (head + f"\n\n{content}\n\n" + "".join(lines[end:])).rstrip("\n") + "\n"


def checkbox_items(text: str) -> list[str]:
    """Normalized checkbox labels, without their checked state."""
    out: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*-\s*\[[ xX]\]\s*(.+?)\s*$", line)
        if match:
            out.append(re.sub(r"\s+", " ", match.group(1)).strip())
    return out


def is_empty(text: str) -> bool:
    """Content that is only comments or whitespace counts as empty."""
    stripped = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    return not stripped


def stated(text: str) -> str:
    """A plan section's text without template placeholders, or '' when only those remain."""
    return re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()


def unwrap_markdown(text: str) -> str:
    """Join hard-wrapped prose so each paragraph and list item is one line.

    Reference files are wrapped for human editors; a model reads the wrap as
    noise. Fenced code, table rows, headings, and blank lines are kept byte for
    byte, so literal commands, fences, and tables survive unchanged.
    """
    out: list[str] = []
    fenced = False
    joinable = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            out.append(line)
            joinable = False
            continue
        if fenced or not stripped or stripped.startswith(("|", "#", "<!--")):
            out.append(line)
            joinable = False
            continue
        starts_item = re.match(r"^(\s*)([-*+]|\d+[.)])\s+\S", line) is not None
        if joinable and not starts_item:
            out[-1] = out[-1].rstrip() + " " + stripped
            continue
        out.append(line.rstrip())
        joinable = True
    return "\n".join(out)
