"""Pure HTML -> markdown/text conversion and turn validation.

Nothing here touches a browser or a database: the adapter hands in HTML,
these functions hand back strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal

Role = Literal["user", "assistant"]

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "figure",
    "figcaption",
    "main",
    "ul",
    "ol",
    "li",
    "table",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class ParseDriftError(RuntimeError):
    """The page did not look like a ChatGPT conversation."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def html_to_markdown(html: str) -> str:
    """Convert a message's HTML into readable markdown."""
    text = _Renderer(markdown=True).render_children(parse_html(html).children)
    return _tidy(text)


def html_to_text(html: str) -> str:
    """Flatten a message's HTML into plain text for search and previews."""
    text = _Renderer(markdown=False).render_children(parse_html(html).children)
    return _tidy(text)


def _tidy(text: str) -> str:
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _Renderer:
    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown

    # -- dispatch ---------------------------------------------------
    def render_children(self, children: list[Node | str]) -> str:
        return "".join(self.render(child) for child in children)

    def render(self, node: Node | str) -> str:
        if isinstance(node, str):
            return node
        tag = node.tag.lower()
        if tag in SKIP_TAGS:
            return ""
        handler = getattr(self, f"_tag_{tag}", None)
        if handler is not None:
            return str(handler(node))
        if tag in BLOCK_TAGS:
            return self._block(self.render_children(node.children))
        return self.render_children(node.children)

    # -- blocks -----------------------------------------------------
    def _block(self, inner: str) -> str:
        inner = inner.strip("\n")
        return f"\n\n{inner}\n\n" if inner else "\n\n"

    def _tag_p(self, node: Node) -> str:
        return self._block(self.render_children(node.children))

    _tag_section = _tag_article = _tag_main = _tag_figure = _tag_p
    _tag_header = _tag_footer = _tag_figcaption = _tag_p

    def _tag_div(self, node: Node) -> str:
        if "data-code-block" in node.attrs:
            return self._code_block(node)
        return self._block(self.render_children(node.children))

    def _code_block(self, node: Node) -> str:
        """A ChatGPT code block: header holds the language label, body holds the code."""
        pre = next(_iter_tag(node, "pre"), None)
        if pre is None:
            return self._block(self.render_children(node.children))
        code = _raw_text(pre).strip("\n")
        language = _code_language(node, pre)
        if not self.markdown:
            return self._block(code)
        fence = "```"
        while fence in code:
            fence += "`"
        return self._block(f"{fence}{language}\n{code}\n{fence}")

    def _tag_br(self, node: Node) -> str:
        return "\n"

    def _tag_hr(self, node: Node) -> str:
        return self._block("---" if self.markdown else "")

    def _tag_blockquote(self, node: Node) -> str:
        inner = self._tidy_children(node)
        if not inner:
            return ""
        if not self.markdown:
            return self._block(inner)
        quoted = "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
        return self._block(quoted)

    def _tag_pre(self, node: Node) -> str:
        code = _raw_text(node).strip("\n")
        if not self.markdown:
            return self._block(code)
        fence = "```"
        while fence in code:
            fence += "`"
        return self._block(f"{fence}\n{code}\n{fence}")

    def _tag_code(self, node: Node) -> str:
        text = _raw_text(node)
        if not self.markdown or "\n" in text:
            return text
        if not text.strip():
            return text
        ticks = "`"
        while ticks in text:
            ticks += "`"
        padding = " " if text.startswith("`") or text.endswith("`") else ""
        return f"{ticks}{padding}{text}{padding}{ticks}"

    def _tag_a(self, node: Node) -> str:
        inner = self.render_children(node.children).strip()
        href = node.attrs.get("href", "")
        if not self.markdown or not href or href.startswith("javascript:"):
            return inner
        if inner == href:
            return f"<{href}>"
        return f"[{inner}]({href})"

    def _tag_img(self, node: Node) -> str:
        alt = node.attrs.get("alt", "").strip()
        label = f"image: {alt}" if alt else "image"
        return f"[{label}]" if self.markdown else f"[{label}]"

    def _tag_strong(self, node: Node) -> str:
        return self._wrap("**", node, skip_if_empty=True)

    _tag_b = _tag_strong

    def _tag_em(self, node: Node) -> str:
        return self._wrap("*", node, skip_if_empty=True)

    _tag_i = _tag_em

    def _tag_del(self, node: Node) -> str:
        return self._wrap("~~", node, skip_if_empty=True)

    _tag_s = _tag_del

    def _tag_sup(self, node: Node) -> str:
        return self._wrap("^", node, skip_if_empty=True)

    def _tag_sub(self, node: Node) -> str:
        return self._wrap("_", node, skip_if_empty=True)

    def _wrap(self, marker: str, node: Node, skip_if_empty: bool = False) -> str:
        inner = self.render_children(node.children)
        if not self.markdown or (skip_if_empty and not inner.strip()):
            return inner
        return f"{marker}{inner}{marker}"

    # -- headings ---------------------------------------------------
    def _heading(self, node: Node, level: int) -> str:
        inner = self.render_children(node.children).strip()
        if not inner:
            return ""
        return self._block(f"{'#' * level} {inner}" if self.markdown else inner)

    def _tag_h1(self, node: Node) -> str:
        return self._heading(node, 1)

    def _tag_h2(self, node: Node) -> str:
        return self._heading(node, 2)

    def _tag_h3(self, node: Node) -> str:
        return self._heading(node, 3)

    def _tag_h4(self, node: Node) -> str:
        return self._heading(node, 4)

    def _tag_h5(self, node: Node) -> str:
        return self._heading(node, 5)

    def _tag_h6(self, node: Node) -> str:
        return self._heading(node, 6)

    # -- lists ------------------------------------------------------
    def _tag_ul(self, node: Node) -> str:
        return self._list(node, ordered=False)

    def _tag_ol(self, node: Node) -> str:
        return self._list(node, ordered=True)

    def _list(self, node: Node, ordered: bool) -> str:
        lines: list[str] = []
        index = 1
        for child in node.children:
            if isinstance(child, Node) and child.tag.lower() == "li":
                body = self._list_item(child)
                if not body:
                    continue
                marker = (f"{index}. " if ordered else "- ") if self.markdown else ""
                lines.extend(_indent_item(marker, body))
                index += 1
            elif isinstance(child, str) and child.strip():
                lines.append(child.strip())
        return self._block("\n".join(lines))

    def _list_item(self, node: Node) -> str:
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, Node) and child.tag.lower() in {"ul", "ol"}:
                nested = self.render(child).strip("\n")
                if nested:
                    parts.append("\n" + nested)
            else:
                parts.append(self.render(child))
        return _tidy("".join(parts))

    # -- tables -----------------------------------------------------
    def _tag_table(self, node: Node) -> str:
        rows = _collect_rows(node)
        if not rows:
            return ""
        if not self.markdown:
            return self._block("\n".join(" | ".join(cells) for cells in rows))
        width = max(len(cells) for cells in rows)
        padded = [cells + [""] * (width - len(cells)) for cells in rows]
        lines = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines.extend("| " + " | ".join(cells) + " |" for cells in padded[1:])
        return self._block("\n".join(lines))

    def _tidy_children(self, node: Node) -> str:
        return _tidy(self.render_children(node.children))


def _code_language(node: Node, pre: Node) -> str:
    """Read the language label from a code block header, if present."""
    for element in _iter_descendants(node):
        if element is pre:
            break
        classes = element.attrs.get("class", "")
        if "truncate" in classes:
            candidate = _raw_text(element).strip()
            if _is_language_label(candidate):
                return candidate.lower()
            break
    return ""


def _is_language_label(text: str) -> bool:
    return bool(text) and len(text) <= 20 and re.fullmatch(r"[A-Za-z0-9+#._-]+", text) is not None


def _iter_descendants(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from _iter_descendants(child)


def _collect_rows(table: Node) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in _iter_tag(table, "tr"):
        cells = [
            _tidy(_Renderer(markdown=True).render_children(cell.children)).replace("\n", " ")
            for cell in tr.children
            if isinstance(cell, Node) and cell.tag.lower() in {"td", "th"}
        ]
        if cells:
            rows.append(cells)
    return rows


def _iter_tag(node: Node, tag: str) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            if child.tag.lower() == tag:
                yield child
            else:
                yield from _iter_tag(child, tag)


def _raw_text(node: Node) -> str:
    out: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        elif child.tag.lower() == "br":
            out.append("\n")
        elif child.tag.lower() == "img":
            alt = child.attrs.get("alt", "").strip()
            out.append(f"[image: {alt}]" if alt else "[image]")
        else:
            out.append(_raw_text(child))
    return "".join(out)


def _indent_item(marker: str, body: str) -> list[str]:
    lines = body.split("\n")
    first = f"{marker}{lines[0]}"
    rest = [_indent_block(line, len(marker)) for line in lines[1:]]
    return [first, *rest]


def _indent_block(text: str, width: int) -> str:
    pad = " " * width
    return "\n".join(pad + line if line else line for line in text.split("\n"))


# ------------------------------------------------------------------ turns


@dataclass(frozen=True)
class RawTurn:
    """A turn as extracted from the page: role plus message HTML."""

    role: Role
    html: str
    alternatives: int = 1


@dataclass(frozen=True)
class ParsedTurn:
    role: Role
    markdown: str
    text: str
    alternatives: int = 1


def build_turns(raw_turns: list[RawTurn]) -> list[ParsedTurn]:
    """Convert raw page turns into markdown/text turns and validate the result."""
    turns: list[ParsedTurn] = []
    for raw in raw_turns:
        markdown = html_to_markdown(raw.html)
        text = html_to_text(raw.html)
        turns.append(
            ParsedTurn(
                role=raw.role,
                markdown=markdown,
                text=text,
                alternatives=max(1, raw.alternatives),
            )
        )
    validate_turns(turns)
    return turns


def validate_turns(turns: list[ParsedTurn]) -> None:
    """Reject a conversation that does not alternate user/assistant."""
    if not turns:
        raise ParseDriftError("no messages found; the page layout may have changed")
    if turns[0].role != "user":
        raise ParseDriftError(f"conversation starts with {turns[0].role!r}, expected 'user'")
    for index, turn in enumerate(turns):
        expected = "user" if index % 2 == 0 else "assistant"
        if turn.role != expected:
            raise ParseDriftError(f"message {index} has role {turn.role!r}, expected {expected!r}")
        if not turn.text.strip():
            raise ParseDriftError(f"message {index} ({turn.role}) is empty")
