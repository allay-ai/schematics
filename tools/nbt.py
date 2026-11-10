"""Named Binary Tag, as Minecraft writes it: read and write gzip'd files without dependencies.

Tags come back as plain Python values with their type kept where Python can't tell: `Tag(type, value)` for the
numeric kinds and arrays, so a file written back keeps its exact types. Compounds are dicts, lists are `List`.
"""

from __future__ import annotations

import gzip
import io
import struct
from dataclasses import dataclass, field

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


@dataclass
class Tag:
    """A value whose NBT type Python's own types don't say: a byte, a short, a long, an array."""

    type: int
    value: object


@dataclass
class List:
    """A list tag: its items' [type] stays known even when it's empty."""

    type: int
    items: list = field(default_factory=list)


def _reader(data: bytes):
    stream = io.BytesIO(data)

    def take(n: int) -> bytes:
        chunk = stream.read(n)
        if len(chunk) != n:
            raise ValueError("NBT ends midway")
        return chunk

    def number(fmt: str):
        return struct.unpack(">" + fmt, take(struct.calcsize(">" + fmt)))[0]

    def string() -> str:
        return take(number("H")).decode("utf-8", "surrogatepass")

    def payload(kind: int, depth: int):
        if depth > 512:
            raise ValueError("NBT nests too deep")
        if kind == BYTE:
            return Tag(BYTE, number("b"))
        if kind == SHORT:
            return Tag(SHORT, number("h"))
        if kind == INT:
            return number("i")
        if kind == LONG:
            return Tag(LONG, number("q"))
        if kind == FLOAT:
            return Tag(FLOAT, number("f"))
        if kind == DOUBLE:
            return number("d")
        if kind == BYTE_ARRAY:
            return Tag(BYTE_ARRAY, take(number("i")))
        if kind == STRING:
            return string()
        if kind == LIST:
            inner, size = number("b"), number("i")
            return List(inner, [payload(inner, depth + 1) for _ in range(size)])
        if kind == COMPOUND:
            out = {}
            while True:
                tag = number("b")
                if tag == END:
                    return out
                name = string()
                out[name] = payload(tag, depth + 1)
        if kind == INT_ARRAY:
            n = number("i")
            return Tag(INT_ARRAY, list(struct.unpack(f">{n}i", take(4 * n))))
        if kind == LONG_ARRAY:
            n = number("i")
            return Tag(LONG_ARRAY, list(struct.unpack(f">{n}q", take(8 * n))))
        raise ValueError(f"unknown NBT tag {kind}")

    return number, string, payload


def loads(data: bytes) -> tuple[str, dict]:
    """The root compound of [data], gzip'd or not, and its name."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    number, string, payload = _reader(data)
    if number("b") != COMPOUND:
        raise ValueError("NBT root isn't a compound")
    name = string()
    return name, payload(COMPOUND, 0)


def load(path: str) -> tuple[str, dict]:
    with open(path, "rb") as file:
        return loads(file.read())


def _kind(value) -> int:
    if isinstance(value, Tag):
        return value.type
    if isinstance(value, List):
        return LIST
    if isinstance(value, dict):
        return COMPOUND
    if isinstance(value, bool):
        return BYTE
    if isinstance(value, int):
        return INT
    if isinstance(value, float):
        return DOUBLE
    if isinstance(value, str):
        return STRING
    raise TypeError(f"no NBT type for {type(value).__name__}")


def _write(out: io.BytesIO, kind: int, value) -> None:
    raw = value.value if isinstance(value, Tag) else value
    if kind == BYTE:
        out.write(struct.pack(">b", int(raw)))
    elif kind == SHORT:
        out.write(struct.pack(">h", raw))
    elif kind == INT:
        out.write(struct.pack(">i", raw))
    elif kind == LONG:
        out.write(struct.pack(">q", raw))
    elif kind == FLOAT:
        out.write(struct.pack(">f", raw))
    elif kind == DOUBLE:
        out.write(struct.pack(">d", raw))
    elif kind == BYTE_ARRAY:
        out.write(struct.pack(">i", len(raw)))
        out.write(bytes(raw))
    elif kind == STRING:
        encoded = raw.encode("utf-8", "surrogatepass")
        out.write(struct.pack(">H", len(encoded)))
        out.write(encoded)
    elif kind == LIST:
        out.write(struct.pack(">bi", value.type, len(value.items)))
        for item in value.items:
            _write(out, value.type, item)
    elif kind == COMPOUND:
        for name, item in raw.items():
            tag = _kind(item)
            out.write(struct.pack(">b", tag))
            _write(out, STRING, name)
            _write(out, tag, item)
        out.write(b"\x00")
    elif kind == INT_ARRAY:
        out.write(struct.pack(f">i{len(raw)}i", len(raw), *raw))
    elif kind == LONG_ARRAY:
        out.write(struct.pack(f">i{len(raw)}q", len(raw), *raw))
    else:
        raise ValueError(f"unknown NBT tag {kind}")


def dumps(root: dict, name: str = "") -> bytes:
    """[root] as a gzip'd NBT file, as the game and WorldEdit write them."""
    out = io.BytesIO()
    out.write(struct.pack(">b", COMPOUND))
    _write(out, STRING, name)
    _write(out, COMPOUND, root)
    # mtime 0 keeps the bytes the same for the same content, so CI commits only what changed.
    return gzip.compress(out.getvalue(), compresslevel=9, mtime=0)


def value(tag):
    """A tag's plain value: a byte's int, an array's list."""
    return tag.value if isinstance(tag, Tag) else tag
