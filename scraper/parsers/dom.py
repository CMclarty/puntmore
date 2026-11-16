"""A small DOM over a page's HTML, from the standard library's html.parser.

For parsers that read a page's own markup (`page.content()`, which costs no
request) in Python rather than in the page, so their tests need no browser.
The HTML Chrome serialises closes every element it opens, so a plain stack
is enough: a void element never takes children, and an end tag closes back to
the nearest open element of its name (one with none open is ignored). Script
and style bodies are raw text, as in a browser: markup inside a script string
is never an element.
"""
from html.parser import HTMLParser

VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []          # Node or str
        self.parent = parent

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def iter(self):
        """This node and every element under it, in document order."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, Node))

    def find_all(self, pred) -> list:
        return [n for n in self.iter() if pred(n)]

    def find(self, pred):
        return next((n for n in self.iter() if pred(n)), None)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root
