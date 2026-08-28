"""The docs' mermaid diagrams must render on GitHub.

A node id that is also a mermaid statement keyword breaks the parse —
`parse --> class{item class}` had GitHub show "Expecting 'AMP', 'COLON',
… got 'CLASS'" in place of the pricing flowchart — and nothing local
says so, the block being a fenced string to every other tool. This is the
keyword check; a real parse needs Node and mermaid, which the test suite
does not carry.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))

# Mermaid's flowchart grammar lexes these as statements or keywords wherever
# they stand, so a node cannot be called one.
KEYWORDS = {
    "class", "classDef", "click", "default", "direction", "end", "flowchart",
    "graph", "interpolate", "linkStyle", "style", "subgraph", "call", "href",
}

_FENCE = re.compile(r"```mermaid\n(.*?)```", re.S)
# A node id: a bare word at the start of a statement, or one straight after
# a link arrow. Labels in brackets/quotes are stripped first so their words
# are not read as ids.
_LABEL = re.compile(r'"[^"]*"|\[[^\]]*\]|\{[^}]*\}|\([^)]*\)')
_LINK = re.compile(r"(?:-->|---|-\.->|==>|--\s*[^-]+\s*-->|--\s*[^-]+\s*---)")


def mermaid_blocks(markdown: str) -> list[str]:
    return _FENCE.findall(markdown)


def node_ids(block: str) -> set[str]:
    ids: set[str] = set()
    for line in block.splitlines()[1:]:  # first line: flowchart TD
        bare = _LABEL.sub(" ", line)
        for chunk in _LINK.split(bare):
            head = chunk.strip().split()
            if head:
                ids.add(head[0])
    return ids


def reserved_ids(markdown: str) -> set[str]:
    return {i for b in mermaid_blocks(markdown) for i in node_ids(b) if i in KEYWORDS}


def test_the_check_catches_the_node_that_broke_github():
    bad = "```mermaid\nflowchart TD\n    parse --> class{item class}\n    class -- gear --> score\n```"
    assert reserved_ids(bad) == {"class"}


def test_the_check_reads_past_labels_and_edge_text():
    ok = ('```mermaid\nflowchart TD\n    a["end of the line"] -- "class" --> kind{item class}\n'
          '    kind -- yes --> b([default])\n```')
    assert reserved_ids(ok) == set()


@pytest.mark.parametrize("doc", DOCS, ids=[d.name for d in DOCS])
def test_no_mermaid_node_is_named_after_a_keyword(doc):
    assert reserved_ids(doc.read_text()) == set(), f"{doc.name}: rename the node"
