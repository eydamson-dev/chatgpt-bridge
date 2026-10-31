from __future__ import annotations

import pytest

from chatgpt_bridge import parse
from chatgpt_bridge.parse import ParseDriftError, ParsedTurn, RawTurn


def md(html: str) -> str:
    return parse.html_to_markdown(html)


def txt(html: str) -> str:
    return parse.html_to_text(html)


def test_paragraphs_and_blank_lines() -> None:
    assert md("<p>one</p><p>two</p>") == "one\n\ntwo"


def test_bold_italic_and_strikethrough() -> None:
    assert (
        md("<p><strong>bold</strong> <em>it</em> <del>gone</del></p>") == "**bold** *it* ~~gone~~"
    )


def test_headings() -> None:
    assert md("<h1>Title</h1><h2>Sub</h2><h3>Small</h3>") == "# Title\n\n## Sub\n\n### Small"


def test_unordered_list() -> None:
    assert md("<ul><li>a</li><li>b</li></ul>") == "- a\n- b"


def test_ordered_list_numbers() -> None:
    assert md("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"


def test_nested_list_indents() -> None:
    out = md("<ul><li>a<ul><li>b</li></ul></li></ul>")
    assert out.splitlines()[0] == "- a"
    assert out.splitlines()[1] == "  - b"


def test_list_item_with_paragraph_keeps_one_line() -> None:
    assert md("<ul><li><p>a</p></li></ul>") == "- a"


def test_inline_code() -> None:
    assert md("<p>use <code>uv sync</code> now</p>") == "use `uv sync` now"


def test_inline_code_with_backticks_uses_longer_fence() -> None:
    assert md("<p><code>a ` b</code></p>") == "``a ` b``"


def test_code_block_is_fenced_and_raw() -> None:
    html = '<pre><code><span class="x">a &lt; b</span><span>\n</span><span>c</span></code></pre>'
    assert md(html) == "```\na < b\nc\n```"


def test_code_block_keeps_markdown_characters_verbatim() -> None:
    assert md("<pre><code>**not bold**</code></pre>") == "```\n**not bold**\n```"


def test_links() -> None:
    assert md('<p><a href="https://x.test">site</a></p>') == "[site](https://x.test)"


def test_bare_url_link_collapses() -> None:
    assert md('<p><a href="https://x.test">https://x.test</a></p>') == "<https://x.test>"


def test_javascript_links_are_dropped() -> None:
    assert md('<p><a href="javascript:void(0)">x</a></p>') == "x"


def test_images_become_placeholders() -> None:
    assert md('<p><img alt="a chart" src="blob:x"></p>') == "[image: a chart]"
    assert md('<p><img src="blob:x"></p>') == "[image]"


def test_table_becomes_gfm() -> None:
    html = (
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></tbody></table>"
    )
    assert md(html) == "| Name | Value |\n| --- | --- |\n| a | 1 |\n| b | 2 |"


def test_table_pads_short_rows() -> None:
    html = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td></tr></table>"
    assert md(html).splitlines()[-1] == "| 1 |  |"


def test_blockquote_prefixes_lines() -> None:
    assert md("<blockquote><p>quote</p></blockquote>") == "> quote"


def test_horizontal_rule() -> None:
    assert md("<p>a</p><hr><p>b</p>") == "a\n\n---\n\nb"


def test_br_becomes_newline() -> None:
    assert md("<p>a<br>b</p>") == "a\nb"


def test_script_and_style_are_skipped() -> None:
    assert md("<p>a</p><script>alert(1)</script><style>x{}</style>") == "a"


def test_plain_text_strips_markdown_markers() -> None:
    html = "<h2>Title</h2><p><strong>bold</strong></p><ul><li>a</li></ul>"
    assert txt(html) == "Title\n\nbold\n\na"


def test_plain_text_table_rows() -> None:
    html = "<table><tr><td>a</td><td>b</td></tr></table>"
    assert txt(html) == "a | b"


def test_whitespace_is_tidied() -> None:
    assert md("<p>  a  </p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_zero_width_and_nbsp_removed() -> None:
    assert txt("<p>a\u200b b&nbsp;c</p>") == "a b c"


def test_empty_html_gives_empty_string() -> None:
    assert md("") == ""
    assert txt("") == ""


def test_build_turns_converts_and_validates() -> None:
    raw = [
        RawTurn(role="user", html="<p>hi</p>"),
        RawTurn(role="assistant", html="<p>hello <strong>there</strong></p>", alternatives=2),
    ]
    turns = parse.build_turns(raw)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[1].markdown == "hello **there**"
    assert turns[1].alternatives == 2


def test_build_turns_rejects_empty_conversation() -> None:
    with pytest.raises(ParseDriftError, match="no messages"):
        parse.build_turns([])


def test_build_turns_rejects_assistant_first() -> None:
    with pytest.raises(ParseDriftError, match="starts with"):
        parse.build_turns([RawTurn(role="assistant", html="<p>x</p>")])


def test_validate_rejects_broken_alternation() -> None:
    turns = [
        ParsedTurn(role="user", markdown="a", text="a"),
        ParsedTurn(role="user", markdown="b", text="b"),
    ]
    with pytest.raises(ParseDriftError, match="message 1"):
        parse.validate_turns(turns)


def test_validate_rejects_empty_message() -> None:
    with pytest.raises(ParseDriftError, match="is empty"):
        parse.validate_turns([ParsedTurn(role="user", markdown="", text="  ")])


def test_alternatives_minimum_is_one() -> None:
    turns = parse.build_turns([RawTurn(role="user", html="<p>x</p>", alternatives=0)])
    assert turns[0].alternatives == 1


def _code_block_html(language: str | None) -> str:
    label = f'<div class="min-w-0 flex-1 truncate">{language}</div>' if language else ""
    return (
        '<div data-code-block="true" class="Root">'
        f'<div class="ActionBar" data-markdown-copy="true">{label}<button>Copy code</button></div>'
        '<div class="Scrollport"><pre><code><span>uv sync</span></code></pre></div>'
        "</div>"
    )


def test_code_block_language_label_becomes_fence_info() -> None:
    assert md(_code_block_html("Bash")) == "```bash\nuv sync\n```"


def test_code_block_without_label_has_no_info_string() -> None:
    assert md(_code_block_html(None)) == "```\nuv sync\n```"


def test_code_block_ignores_non_language_label() -> None:
    out = md(_code_block_html("Copy this into your terminal"))
    assert out.startswith("```\n") or out.startswith("``` ")
    assert "Copy code" not in out.split("```")[1]


def test_language_label_is_lowercased() -> None:
    assert md(_code_block_html("Python")) == "```python\nuv sync\n```"
