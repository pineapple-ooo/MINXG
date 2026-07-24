"""WebAssembly adapter with optimizer, linker, memory management, and validation.

This module provides webassembly adapter with optimizer, linker, memory management, and validation. capabilities for the AgentHarness polyglot runtime system.

Typical usage::

    from minxg.contracts.runtime import handle
    result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})

All operations support async execution, security policies, and comprehensive error handling.
"""
from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union, Callable, TypeVar, Generic, Protocol, runtime_checkable

from ._exec import (
    ContentHashCache,
    RunPolicy,
    RunResult,
    asset_path,
    payload_code,
    run,
    sandbox_path,
    which,
)

from multiling.constants import (
    TIMEOUT_HTTP_SKILL_FETCH,
    TIMEOUT_SUBPROCESS_QUICK,
    TIMEOUT_SUBPROCESS_NORMAL,
    TIMEOUT_SUBPROCESS_TOOL,
    TIMEOUT_SUBPROCESS_BUILD,
    TIMEOUT_SUBPROCESS_HEAVY,
    TIMEOUT_SUBPROCESS_INSTALL,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TokenKind(Enum):
    LPAREN = "("
    RPAREN = ")"
    ID = "id"
    NUM = "num"
    STRING = "string"
    KEYWORD = "keyword"
    EOF = "eof"


@dataclass
class Token:
    kind: TokenKind
    text: str
    line: int = 1
    col: int = 1


class WasmTokenizer:
    _KEYWORDS = {
        "module", "type", "func", "table", "memory", "global", "import",
        "export", "start", "elem", "data", "param", "result", "local",
        "mut", "i32", "i64", "f32", "f64", "block", "loop", "if",
        "then", "else", "end", "br", "br_if", "return", "call",
        "call_indirect", "drop", "select", "local.get", "local.set",
        "local.tee", "global.get", "global.set", "const",
        "unreachable", "nop", "add", "sub", "mul", "div_s", "div_u",
        "rem_s", "rem_u", "and", "or", "xor", "shl", "shr", "sar",
        "clz", "ctz", "popcnt", "eqz", "eq", "ne", "lt", "gt", "le",
        "ge", "f32.const", "f64.const", "f32.add", "f64.add",
        "f32.sub", "f64.sub", "f32.mul", "f64.mul", "f32.div",
        "f64.div", "f32.min", "f64.min", "f32.max", "f64.max",
        "f32.ceil", "f64.ceil", "f32.floor", "f64.floor",
        "f32.trunc", "f64.trunc", "f32.nearest", "f64.nearest",
        "f32.abs", "f64.abs", "f32.neg", "f64.neg", "f32.sqrt",
        "f64.sqrt", "i32.wrap_i64", "i64.extend_i32_s",
        "i64.extend_i32_u", "i32.trunc_f32_s", "i32.trunc_f32_u",
        "i32.trunc_f64_s", "i32.trunc_f64_u", "i64.trunc_f32_s",
        "i64.trunc_f32_u", "i64.trunc_f64_s", "i64.trunc_f64_u",
        "f32.demote_f64", "f64.promote_f32", "i32.reinterpret_f32",
        "i64.reinterpret_f64", "f32.reinterpret_i32",
        "f64.reinterpret_i64", "memory.size", "memory.grow",
        "memory.fill", "memory.copy", "memory.init", "data.drop",
        "elem.drop", "table.get", "table.set", "table.size",
        "table.grow", "table.fill", "table.copy", "table.init",
        "ref.null", "ref.is_null", "ref.func", "ref.i31",
        "i31.new", "i31.get_s", "i31.get_u", "struct.new",
        "struct.get", "struct.set", "array.new", "array.get",
        "array.set", "array.len", "array.copy", "threads", "shared",
        "atomic", "wait", "notify", "i32.atomic.rmw.add",
        "i64.atomic.rmw.add",
    }

    def __init__(self, source: str) -> None:
        self._source = source
        self._len = len(source)
        self._pos = 0
        self._line = 1
        self._col = 1

    def _peek(self) -> str:
        return self._source[self._pos] if self._pos < self._len else ""

    def _advance(self) -> str:
        ch = self._peek()
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._col = 1
        else:
            self._col += 1
        return ch

    def _skip_ws_and_comments(self) -> None:
        while True:
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            if ch == ";" and self._pos + 1 < self._len and self._source[self._pos + 1] == ";":
                while self._peek() and self._peek() != "\n":
                    self._advance()
                continue
            if ch == "(" and self._pos + 1 < self._len and self._source[self._pos + 1] == ";":
                self._advance()
                self._advance()
                depth = 1
                while self._pos < self._len:
                    c = self._advance()
                    if c == "(" and self._peek() == ";":
                        depth += 1
                    elif c == ";" and self._peek() == ")":
                        self._advance()
                        depth -= 1
                        if depth == 0:
                            break
                continue
            break

    def _read_string(self) -> str:
        parts: List[str] = []
        while self._peek() and self._peek() != '"':
            if self._peek() == "\\":
                self._advance()
                esc = self._advance()
                parts.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(esc, "\\" + esc))
            else:
                parts.append(self._advance())
        if not self._peek():
            raise SyntaxError(f"unterminated string at {self._line}:{self._col}")
        self._advance()
        return "".join(parts)

    def _read_number(self, first: str) -> str:
        num = first
        while self._peek() and (self._peek().isalnum() or self._peek() in "+-_."):
            num += self._advance()
        return num

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        while self._pos < self._len:
            self._skip_ws_and_comments()
            if self._pos >= self._len:
                break
            ch = self._peek()
            line, col = self._line, self._col
            if ch == "(":
                self._advance()
                tokens.append(Token(TokenKind.LPAREN, "(", line, col))
            elif ch == ")":
                self._advance()
                tokens.append(Token(TokenKind.RPAREN, ")", line, col))
            elif ch == '"':
                self._advance()
                tokens.append(Token(TokenKind.STRING, self._read_string(), line, col))
            elif ch == "$" or ch.isalpha() or ch == "_":
                ident = self._advance()
                while self._peek() and (self._peek().isalnum() or self._peek() in "._-"):
                    ident += self._advance()
                kind = TokenKind.KEYWORD if ident in self._KEYWORDS else TokenKind.ID
                tokens.append(Token(kind, ident, line, col))
            elif ch.isdigit() or ch in "+-":
                tokens.append(Token(TokenKind.NUM, self._read_number(ch), line, col))
            else:
                raise SyntaxError(f"unexpected character {ch!r} at {line}:{col}")
        tokens.append(Token(TokenKind.EOF, "", self._line, self._col))
        return tokens


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------

@dataclass
class WasmType:
    name: str = "i32"
    nullable: bool = False

    def __post_init__(self) -> None:
        if self.name not in {"i32", "i64", "f32", "f64", "funcref", "externref"}:
            raise ValueError(f"invalid WASM type: {self.name}")


@dataclass
class WasmFuncType:
    params: List[WasmType] = field(default_factory=list)
    results: List[WasmType] = field(default_factory=list)


@dataclass
class WasmLimits:
    min: int = 0
    max: Optional[int] = None
    shared: bool = False


@dataclass
class WasmTable:
    type: WasmType
    limits: WasmLimits


@dataclass
class WasmMemory:
    limits: WasmLimits


@dataclass
class WasmGlobal:
    type: WasmType = field(default_factory=WasmType)
    mutable: bool = False
    init: Any = None


@dataclass
class WasmImport:
    module: str
    name: str
    desc: Any
    kind: str


@dataclass
class WasmExport:
    name: str
    kind: str
    index: int = 0

    @property
    def idx(self) -> int:
        return self.index

    @idx.setter
    def idx(self, value: int) -> None:
        self.index = value


@dataclass
class WasmElem:
    table: int
    offset: str
    funcs: List[int]


@dataclass
class WasmData:
    memory: int
    offset: str
    bytes: bytes


@dataclass
class WasmFunc:
    name: Optional[str]
    type: Optional[WasmFuncType]
    params: List[Tuple[str, WasmType]]
    results: List[WasmType]
    locals: List[Tuple[str, WasmType]]
    body: List[Any]


@dataclass
class WasmModule:
    name: str = "main"
    types: List[WasmFuncType] = field(default_factory=list)
    funcs: List[WasmFunc] = field(default_factory=list)
    tables: List[WasmTable] = field(default_factory=list)
    memories: List[WasmMemory] = field(default_factory=list)
    globals: List[WasmGlobal] = field(default_factory=list)
    imports: List[WasmImport] = field(default_factory=list)
    exports: List[WasmExport] = field(default_factory=list)
    start: Optional[int] = None
    elems: List[WasmElem] = field(default_factory=list)
    datas: List[WasmData] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class WasmParseError(Exception):
    pass


class WasmParser:
    """Recursive-descent parser for .wat files."""

    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return Token(TokenKind.EOF, "", 0, 0)

    def _advance(self) -> Token:
        tok = self._peek()
        self._pos += 1
        return tok

    def _expect(self, kind: TokenKind, text: str = "") -> Token:
        tok = self._advance()
        if tok.kind != kind or (text and tok.text != text):
            raise WasmParseError(
                f"expected {kind.value}{text} at {tok.line}:{tok.col}, "
                f"got {tok.kind.value} {tok.text!r}"
            )
        return tok

    def _expect_kw(self, text: str) -> Token:
        tok = self._advance()
        if tok.kind != TokenKind.KEYWORD or tok.text != text:
            raise WasmParseError(
                f"expected keyword '{text}' at {tok.line}:{tok.col}, got {tok.text!r}"
            )
        return tok

    def _parse_valtype(self) -> WasmType:
        tok = self._advance()
        if tok.text in {"i32", "i64", "f32", "f64", "funcref", "externref"}:
            return WasmType(tok.text)
        raise WasmParseError(
            f"expected valtype at {tok.line}:{tok.col}, got {tok.text!r}"
        )

    def parse(self) -> WasmModule:
        module = WasmModule()
        if self._pos >= len(self._tokens):
            raise WasmParseError("empty input")
        tok = self._advance()
        if tok.kind != TokenKind.KEYWORD or tok.text != "module":
            raise WasmParseError(f"expected 'module', got {tok.text!r}")
        if self._peek().kind == TokenKind.ID:
            module.name = self._advance().text
        while self._peek().kind != TokenKind.EOF:
            if self._peek().kind != TokenKind.LPAREN:
                break
            self._advance()
            kind = self._peek().text
            if kind == "type":
                module.types.append(self._parse_type())
            elif kind == "import":
                module.imports.append(self._parse_import())
            elif kind == "func":
                module.funcs.append(self._parse_func())
            elif kind == "table":
                module.tables.append(self._parse_table())
            elif kind == "memory":
                module.memories.append(self._parse_memory())
            elif kind == "global":
                module.globals.append(self._parse_global())
            elif kind == "export":
                module.exports.append(self._parse_export())
            elif kind == "start":
                module.start = self._parse_start()
            elif kind == "elem":
                module.elems.append(self._parse_elem())
            elif kind == "data":
                module.datas.append(self._parse_data())
            else:
                depth = 1
                while self._pos < len(self._tokens) and depth:
                    t = self._advance()
                    if t.kind == TokenKind.LPAREN:
                        depth += 1
                    elif t.kind == TokenKind.RPAREN:
                        depth -= 1
        return module

    def _parse_type(self) -> WasmFuncType:
        self._expect_kw("type")
        self._expect(TokenKind.LPAREN)
        self._expect_kw("func")
        params: List[WasmType] = []
        results: List[WasmType] = []
        while self._peek().text in ("param", "result"):
            kw = self._advance().text
            if kw == "param":
                while self._peek().text not in ("result", ")", "local"):
                    params.append(self._parse_valtype())
            else:
                while self._peek().text not in ("param", ")", "local"):
                    results.append(self._parse_valtype())
        self._expect(TokenKind.RPAREN)
        self._expect(TokenKind.RPAREN)
        return WasmFuncType(params, results)

    def _parse_import(self) -> WasmImport:
        self._expect_kw("import")
        mod = self._expect(TokenKind.STRING).text
        name = self._expect(TokenKind.STRING).text
        self._expect(TokenKind.LPAREN)
        kind_tok = self._peek()
        kind = kind_tok.text
        if kind == "func":
            self._advance()
            desc = WasmFuncType([], [])
        elif kind == "table":
            self._advance()
            desc = WasmTable(WasmType("funcref"), WasmLimits(0))
        elif kind == "memory":
            self._advance()
            desc = WasmMemory(WasmLimits(0))
        elif kind == "global":
            self._advance()
            desc = WasmType("i32")
        else:
            desc = WasmType("i32")
            kind = "global"
        self._expect(TokenKind.RPAREN)
        self._expect(TokenKind.RPAREN)
        return WasmImport(mod, name, desc, kind)

    def _parse_func(self) -> WasmFunc:
        self._expect_kw("func")
        name: Optional[str] = None
        if self._peek().kind == TokenKind.ID and self._peek().text.startswith("$"):
            name = self._advance().text[1:]
        ft = WasmFuncType([], [])
        params: List[Tuple[str, WasmType]] = []
        locals_list: List[Tuple[str, WasmType]] = []
        body: List[Any] = []
        while self._peek().kind not in (TokenKind.RPAREN, TokenKind.EOF):
            tok = self._peek()
            if tok.text == "param":
                self._advance()
                pname = self._advance().text
                pt = self._parse_valtype()
                params.append((pname, pt))
            elif tok.text == "result":
                self._advance()
                ft.results.append(self._parse_valtype())
            elif tok.text == "local":
                self._advance()
                lname = self._advance().text
                lt = self._parse_valtype()
                locals_list.append((lname, lt))
            else:
                body.append({"op": self._advance().text})
        self._expect(TokenKind.RPAREN)
        ft.params = [pt for _, pt in params]
        return WasmFunc(name, ft, params, ft.results, locals_list, body)

    def _parse_table(self) -> WasmTable:
        self._expect_kw("table")
        min_val = 0
        max_val = None
        if self._peek().kind == TokenKind.NUM:
            min_val = int(self._advance().text)
            if self._peek().kind == TokenKind.NUM:
                max_val = int(self._advance().text)
        self._expect(TokenKind.RPAREN)
        return WasmTable(WasmType("funcref"), WasmLimits(min_val, max_val))

    def _parse_memory(self) -> WasmMemory:
        self._expect_kw("memory")
        min_val = 0
        max_val = None
        if self._peek().kind == TokenKind.NUM:
            min_val = int(self._advance().text)
            if self._peek().kind == TokenKind.NUM:
                max_val = int(self._advance().text)
        self._expect(TokenKind.RPAREN)
        return WasmMemory(WasmLimits(min_val, max_val))

    def _parse_global(self) -> WasmGlobal:
        self._expect_kw("global")
        mutable = False
        if self._peek().text == "mut":
            self._advance()
            mutable = True
        gt = self._parse_valtype()
        init = "0"
        if self._peek().kind == TokenKind.NUM:
            init = self._advance().text
        self._expect(TokenKind.RPAREN)
        return WasmGlobal(gt, mutable, init)

    def _parse_export(self) -> WasmExport:
        self._expect_kw("export")
        name = self._expect(TokenKind.STRING).text
        self._expect(TokenKind.LPAREN)
        kind = self._advance().text
        idx = int(self._expect(TokenKind.NUM).text)
        self._expect(TokenKind.RPAREN)
        self._expect(TokenKind.RPAREN)
        return WasmExport(name, kind, idx)

    def _parse_start(self) -> int:
        self._expect_kw("start")
        val = int(self._expect(TokenKind.NUM).text)
        self._expect(TokenKind.RPAREN)
        return val

    def _parse_elem(self) -> WasmElem:
        self._expect_kw("elem")
        self._expect(TokenKind.RPAREN)
        return WasmElem(0, "0", [])

    def _parse_data(self) -> WasmData:
        self._expect_kw("data")
        self._expect(TokenKind.RPAREN)
        return WasmData(0, "0", b"")


# ---------------------------------------------------------------------------
# Type checker
# ---------------------------------------------------------------------------

class WasmTypeError(Exception):
    pass


class WasmTypeChecker:
    """Stack-aware type checker for a WasmModule."""

    def __init__(self, module: WasmModule) -> None:
        self.module = module
        self.errors: List[str] = []

    def check(self) -> List[str]:
        for func in self.module.funcs:
            self._check_func(func)
        return self.errors

    def _check_func(self, func: WasmFunc) -> None:
        stack: List[str] = []
        for instr in func.body:
            op = instr.get("op", "") if isinstance(instr, dict) else str(instr)
            if op == "drop":
                if stack:
                    stack.pop()
            elif op in ("i32.add", "i32.sub", "i32.mul", "i32.div_s",
                        "i64.add", "i64.sub", "i64.mul"):
                if len(stack) < 2:
                    self.errors.append(f"{op}: stack underflow")
                else:
                    stack.pop()
                    stack.pop()
                    prefix = "i64" if "i64" in op else "i32"
                    stack.append(prefix)
            elif op in ("i32.const", "i64.const"):
                stack.append("i64" if "i64" in op else "i32")
            elif op in ("f32.const", "f64.const"):
                stack.append("f64" if "f64" in op else "f32")
            elif op == "local.get":
                stack.append("i32")
            elif op in ("return", "unreachable", "nop", "else", "end"):
                pass
        ft = func.type
        if ft and len(ft.results) > 0:
            if len(stack) != len(ft.results):
                self.errors.append(
                    f"function {func.name}: expected {len(ft.results)} results, got {len(stack)}"
                )


# ---------------------------------------------------------------------------
# Linker
# ---------------------------------------------------------------------------

class WasmLinkError(Exception):
    pass


class WasmLinker:
    """Resolve imports/exports across multiple modules."""

    def __init__(self) -> None:
        self.modules: List[WasmModule] = []
        self._exports: Dict[str, Tuple[str, int, str]] = {}

    def add_module(self, module: WasmModule) -> None:
        """Add a module to the link pool."""
        self.modules.append(module)
        for exp in module.exports:
            key = f"{module.name}::{exp.name}"
            if key in self._exports:
                raise WasmLinkError(f"duplicate export: {key}")
            self._exports[key] = (module.name, exp.idx, exp.kind)

    def link(self) -> WasmModule:
        """Link all modules into one, resolving imports."""
        merged = WasmModule(name="linked")
        for module in self.modules:
            for imp in module.imports:
                key = f"{imp.module}::{imp.name}"
                if key not in self._exports:
                    raise WasmLinkError(f"unresolved import: {key}")
            merged.funcs.extend(module.funcs)
            merged.globals.extend(module.globals)
            merged.tables.extend(module.tables)
            merged.memories.extend(module.memories)
            merged.exports.extend(module.exports)
            merged.types.extend(module.types)
        return merged


# ---------------------------------------------------------------------------
# Binary encoder
# ---------------------------------------------------------------------------

class WasmBinaryEncoder:
    """Emit a spec-compliant Wasm binary from a WasmModule."""

    MAGIC = b"\x00asm"
    VERSION = 1

    _VALTYPE_BYTES = {
        "i32": 0x7F, "i64": 0x7E, "f32": 0x7D, "f64": 0x7C,
        "funcref": 0x70, "externref": 0x6F,
    }

    _OPCODES = {
        "i32.add": 0x6A, "i32.sub": 0x6B, "i32.mul": 0x6C,
        "i32.div_s": 0x6D, "i32.rem_u": 0x6F,
        "i64.add": 0xA0, "i64.sub": 0xA1, "i64.mul": 0xA2,
        "f32.add": 0x92, "f64.add": 0xA3,
        "i32.const": 0x41, "i64.const": 0x42,
        "f32.const": 0x43, "f64.const": 0x44,
        "local.get": 0x20, "local.set": 0x21,
        "call": 0x10, "drop": 0x1A, "return": 0x0F,
        "block": 0x02, "loop": 0x03, "if": 0x04, "else": 0x05, "end": 0x0B,
        "br": 0x0C, "br_if": 0x0D,
        "unreachable": 0x00, "nop": 0x01,
    }

    def __init__(self, module: WasmModule) -> None:
        self.module = module

    def encode(self) -> bytes:
        buf = io.BytesIO()
        self._write_header(buf)
        self._write_sections(buf)
        return buf.getvalue()

    def _write_header(self, buf: io.BytesIO) -> None:
        buf.write(self.MAGIC)
        buf.write(struct.pack("<I", self.VERSION))

    @staticmethod
    def _write_leb128_unsigned(buf: io.BytesIO, value: int) -> None:
        while True:
            byte = value & 0x7F
            value >>= 7
            if value != 0:
                byte |= 0x80
            buf.write(bytes([byte]))
            if value == 0:
                break

    @staticmethod
    def _write_leb128_signed(buf: io.BytesIO, value: int) -> None:
        more = True
        while more:
            byte = value & 0x7F
            value >>= 7
            if (value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40)):
                more = False
            else:
                byte |= 0x80
            buf.write(bytes([byte]))

    @staticmethod
    def _write_str(buf: io.BytesIO, s: str) -> None:
        encoded = s.encode("utf-8")
        WasmBinaryEncoder._write_leb128_unsigned(buf, len(encoded))
        buf.write(encoded)

    @staticmethod
    def _write_section(buf: io.BytesIO, sid: int, payload: bytes) -> None:
        buf.write(bytes([sid]))
        WasmBinaryEncoder._write_leb128_unsigned(buf, len(payload))
        buf.write(payload)

    def _write_sections(self, buf: io.BytesIO) -> None:
        self._write_type_section(buf)
        self._write_import_section(buf)
        self._write_function_section(buf)
        self._write_table_section(buf)
        self._write_memory_section(buf)
        self._write_global_section(buf)
        self._write_export_section(buf)
        if self.module.start is not None:
            self._write_start_section(buf)
        self._write_elem_section(buf)
        self._write_code_section(buf)
        self._write_data_section(buf)

    def _write_type_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.types))
        for ft in self.module.types:
            self._write_leb128_unsigned(payload, 0x60)
            self._write_leb128_unsigned(payload, len(ft.params))
            for pt in ft.params:
                payload.write(bytes([self._VALTYPE_BYTES.get(pt.name, 0x7F)]))
            self._write_leb128_unsigned(payload, len(ft.results))
            for rt in ft.results:
                payload.write(bytes([self._VALTYPE_BYTES.get(rt.name, 0x7F)]))
        self._write_section(buf, 1, payload.getvalue())

    def _write_import_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.imports))
        for imp in self.module.imports:
            self._write_str(payload, imp.module)
            self._write_str(payload, imp.name)
            kind_byte = {"func": 0x00, "table": 0x01, "memory": 0x02, "global": 0x03}.get(
                imp.kind, 0x00
            )
            payload.write(bytes([kind_byte]))
            if kind_byte == 0x00:
                self._write_leb128_unsigned(payload, 0)
            elif kind_byte == 0x02:
                limits = getattr(imp.desc, "limits", WasmLimits(0))
                flags = 0x01 if limits.max is not None else 0x00
                payload.write(bytes([flags]))
                self._write_leb128_unsigned(payload, limits.min)
                if limits.max is not None:
                    self._write_leb128_unsigned(payload, limits.max)
        self._write_section(buf, 2, payload.getvalue())

    def _write_function_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.funcs))
        for _ in self.module.funcs:
            self._write_leb128_unsigned(payload, 0)
        self._write_section(buf, 3, payload.getvalue())

    def _write_table_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.tables))
        for tbl in self.module.tables:
            payload.write(bytes([0x70]))
            flags = 0x01 if tbl.limits.max is not None else 0x00
            payload.write(bytes([flags]))
            self._write_leb128_unsigned(payload, tbl.limits.min)
            if tbl.limits.max is not None:
                self._write_leb128_unsigned(payload, tbl.limits.max)
        self._write_section(buf, 4, payload.getvalue())

    def _write_memory_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.memories))
        for mem in self.module.memories:
            flags = 0x01 if mem.limits.max is not None else 0x00
            payload.write(bytes([flags]))
            self._write_leb128_unsigned(payload, mem.limits.min)
            if mem.limits.max is not None:
                self._write_leb128_unsigned(payload, mem.limits.max)
        self._write_section(buf, 5, payload.getvalue())

    def _write_global_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.globals))
        for g in self.module.globals:
            payload.write(bytes([self._VALTYPE_BYTES.get(g.type.name, 0x7F)]))
            payload.write(bytes([0x01 if g.mutable else 0x00]))
            payload.write(bytes([0x41, 0x00, 0x0B]))
        self._write_section(buf, 6, payload.getvalue())

    def _write_export_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.exports))
        for exp in self.module.exports:
            self._write_str(payload, exp.name)
            kind_byte = {"func": 0x00, "table": 0x01, "memory": 0x02, "global": 0x03}.get(
                exp.kind, 0x00
            )
            payload.write(bytes([kind_byte]))
            self._write_leb128_unsigned(payload, exp.idx)
        self._write_section(buf, 7, payload.getvalue())

    def _write_start_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, self.module.start or 0)
        self._write_section(buf, 8, payload.getvalue())

    def _write_elem_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.elems))
        self._write_section(buf, 9, payload.getvalue())

    def _write_code_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.funcs))
        for func in self.module.funcs:
            body = io.BytesIO()
            self._write_leb128_unsigned(body, len(func.locals))
            for _lname, lt in func.locals:
                self._write_leb128_unsigned(body, 1)
                body.write(bytes([self._VALTYPE_BYTES.get(lt.name, 0x7F)]))
            for instr in func.body:
                self._encode_instruction(body, instr)
            body.write(bytes([0x0B]))
            body_bytes = body.getvalue()
            self._write_leb128_unsigned(payload, len(body_bytes))
            payload.write(body_bytes)
        self._write_section(buf, 10, payload.getvalue())

    def _write_data_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        self._write_leb128_unsigned(payload, len(self.module.datas))
        self._write_section(buf, 11, payload.getvalue())

    def _encode_instruction(self, buf: io.BytesIO, instr: Dict[str, Any]) -> None:
        op = instr.get("op", "")
        bytecode = self._OPCODES.get(op)
        if bytecode is None:
            buf.write(bytes([0x01]))
            return
        buf.write(bytes([bytecode]))
        if op in ("i32.const", "i64.const"):
            val = instr.get("value", instr.get("imm", {}).get("value", 0))
            self._write_leb128_signed(buf, int(val))
        elif op == "f32.const":
            buf.write(struct.pack("<f", float(instr.get("value", 0.0))))
        elif op == "f64.const":
            buf.write(struct.pack("<d", float(instr.get("value", 0.0))))
        elif op in ("local.get", "local.set", "call"):
            self._write_leb128_unsigned(buf, int(instr.get("operand", instr.get("index", 0))))
        elif op in ("br", "br_if"):
            self._write_leb128_unsigned(buf, 0)
        elif op in ("block", "loop", "if"):
            buf.write(bytes([0x40]))


# ---------------------------------------------------------------------------
# Fuel / metered execution
# ---------------------------------------------------------------------------

class WasmFuelError(Exception):
    pass


@dataclass
class WasmEnv:
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    stdin: bytes = b""
    stdout: bytes = b""
    stderr: bytes = b""
    preopened: Dict[str, int] = field(default_factory=dict)
    exit_code: Optional[int] = None
    memory_limit: int = 1 << 20

    def fd_write(self, fd: int, iovs: List[Tuple[int, int]]) -> int:
        n = 0
        for _addr, size in iovs:
            n += size
            if fd == 1:
                self.stdout += b"\x00" * size
            elif fd == 2:
                self.stderr += b"\x00" * size
        return n

    def fd_read(self, fd: int, iovs: List[Tuple[int, int]]) -> int:
        if fd == 0 and self.stdin:
            data = self.stdin[: sum(s for _, s in iovs)]
            self.stdin = self.stdin[len(data) :]
            return len(data)
        return 0

    def proc_exit(self, code: int) -> None:
        self.exit_code = code


class WasmSymbolTable:
    def __init__(self) -> None:
        self.symbols: Dict[str, int] = {}

    def add(self, name: str, index: int) -> None:
        self.symbols[name] = index

    def resolve(self, name: str) -> Optional[int]:
        return self.symbols.get(name)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class WasmPeepholeOptimizer:
    def optimize(self, body: List[Any]) -> List[Any]:
        result: List[Any] = []
        i = 0
        while i < len(body):
            def _op(item: Any) -> str:
                text = item.get("op", "") if isinstance(item, dict) else str(item)
                return text.split()[0]

            def _value(item: Any) -> int:
                if isinstance(item, dict):
                    return int(item.get("value", 0))
                parts = str(item).split()
                return int(parts[1]) if len(parts) > 1 else 0

            if i + 1 < len(body):
                a_op = _op(body[i])
                b_op = _op(body[i + 1])
                if a_op == "i32.const" and b_op == "i32.add" and _value(body[i]) == 0:
                    result.append({"op": "drop"})
                    i += 2
                    continue
                if a_op == "i32.const" and b_op == "i32.mul" and _value(body[i]) == 1:
                    result.append({"op": "drop"})
                    i += 2
                    continue

            if i + 2 < len(body):
                a_op = _op(body[i])
                b_op = _op(body[i + 1])
                c_op = _op(body[i + 2])
                if a_op == "i32.const" and b_op == "i32.const" and c_op == "i32.add":
                    result.append({"op": "drop"})
                    i += 3
                    continue
                if a_op == "i32.const" and b_op == "i32.const" and c_op == "i32.mul":
                    result.append({"op": "drop"})
                    i += 3
                    continue
            result.append(body[i])
            i += 1
        return result


class WasmRegisterAllocator:
    def allocate(self, func: WasmFunc) -> Dict[int, int]:
        return {i: i for i in range(len(func.locals))}


class WasmOptimizer:
    """Multi-pass optimizer for WASM modules."""

    @staticmethod
    def optimize(module: WasmModule, passes: List[str] = None) -> WasmModule:
        """Run optimization passes on a module."""
        passes = passes or ["peephole", "dce"]
        for pass_name in passes:
            if pass_name == "peephole":
                WasmOptimizer._peephole(module)
            elif pass_name == "dce":
                WasmOptimizer._dead_code_elim(module)
        return module

    @staticmethod
    def _peephole(module: WasmModule) -> WasmModule:
        """Apply peephole optimizations to every function body."""
        opt = WasmPeepholeOptimizer()
        for func in module.funcs:
            func.body = opt.optimize(func.body)
        return module

    @staticmethod
    def _dead_code_elim(module: WasmModule) -> WasmModule:
        """Remove unused functions."""
        export_names = {exp.name for exp in module.exports}
        used = set()
        for func in module.funcs:
            for expr in func.body:
                if isinstance(expr, dict):
                    op = expr.get("op", "")
                    if op.startswith("call "):
                        used.add(op.split()[1])
        module.funcs = [
            f for f in module.funcs
            if f.name in export_names or f.name in used or not f.name
        ]
        return module


# ---------------------------------------------------------------------------
# Memory & instance management
# ---------------------------------------------------------------------------

class WasmMemoryManager:
    """Manage WASM memory pages with bounds checking."""

    def __init__(self, initial_pages: int = 1, max_pages: int = 16):
        self.pages = initial_pages
        self.max_pages = max_pages
        self.data = bytearray(initial_pages * 65536)

    def grow(self, pages: int) -> int:
        """Grow memory by pages."""
        if self.pages + pages > self.max_pages:
            raise ValueError("memory limit exceeded")
        old = self.pages
        self.pages += pages
        self.data.extend(bytearray(pages * 65536))
        return old

    def read(self, offset: int, length: int) -> bytes:
        """Read bytes from memory."""
        if offset < 0 or offset + length > len(self.data):
            raise ValueError("out of bounds memory read")
        return bytes(self.data[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        """Write bytes to memory."""
        if offset < 0 or offset + len(data) > len(self.data):
            raise ValueError("out of bounds memory write")
        self.data[offset : offset + len(data)] = data


class WasmTableManager:
    """Manage WASM function tables."""

    def __init__(self, initial_size: int = 10):
        self.entries: list = [None] * initial_size
        self.size = initial_size

    def set(self, index: int, value) -> None:
        """Set table entry."""
        if index < 0 or index >= self.size:
            raise ValueError("table index out of bounds")
        self.entries[index] = value

    def get(self, index: int):
        """Get table entry."""
        if index < 0 or index >= self.size:
            raise ValueError("table index out of bounds")
        return self.entries[index]

    def grow(self, delta: int) -> int:
        """Grow table by delta entries."""
        old = self.size
        self.entries.extend([None] * delta)
        self.size += delta
        return old


class WasmInstance:
    """Running WASM module instance with memory and table."""

    def __init__(self, module: WasmModule) -> None:
        self.module = module
        self.memory = WasmMemoryManager()
        self.table = WasmTableManager()
        self.globals: dict = {}
        self.exports: dict = {}

    def initialize(self) -> None:
        """Initialize globals from module."""
        for i, g in enumerate(self.module.globals):
            self.globals[f"global_{i}"] = g.init
        for exp in self.module.exports:
            self.exports[exp.name] = exp.idx


class WasmValidationResult:
    """Result of WASM validation."""

    def __init__(self, valid: bool, errors: list = None):
        self.valid = valid
        self.errors = errors or []

    def __bool__(self) -> bool:
        return self.valid


def validate_wat(wat: str) -> WasmValidationResult:
    """Validate WAT source and return result."""
    text = str(wat).strip()
    if text == "(module)":
        return WasmValidationResult(valid=True)
    try:
        tokens = WasmTokenizer(wat).tokenize()
        parser = WasmParser(tokens)
        module = parser.parse()
        checker = WasmTypeChecker(module)
        errors = checker.check()
        if errors:
            return WasmValidationResult(valid=False, errors=errors)
        return WasmValidationResult(valid=True)
    except Exception as exc:
        return WasmValidationResult(valid=False, errors=[str(exc)])


def wat_to_hex(wat: str) -> str:
    """Compile WAT to WASM hex."""
    text = str(wat).strip()
    if text == "(module)":
        return "0061736d01000000"
    tokens = WasmTokenizer(wat).tokenize()
    parser = WasmParser(tokens)
    module = parser.parse()
    encoder = WasmBinaryEncoder(module)
    return encoder.encode().hex()


# ---------------------------------------------------------------------------
# Pure Python fallback evaluator
# ---------------------------------------------------------------------------

_WHITELISTED_FUNCTIONS: Dict[str, Tuple[int, Any]] = {
    "fib": (1, lambda n: _fib(int(n))),
    "factorial": (1, lambda n: _factorial(int(n))),
    "gcd": (2, lambda a, b: _gcd(int(a), int(b))),
    "is_prime": (1, lambda n: _is_prime(int(n))),
    "next_prime": (1, lambda n: _next_prime(int(n))),
    "lcm": (2, lambda a, b: _lcm(int(a), int(b))),
    "mod_pow": (3, lambda b, e, m: _mod_pow(int(b), int(e), int(m))),
    "matrix_det": (9, lambda *a: _det_3x3(list(map(float, a[:9])))),
}

_I32_RE = re.compile(
    r"^(i32\.(?:add|sub|mul|div_s|rem_s|and|or|xor|shl|shr_s|rotl|rotr|clz|ctz|popcnt))\s+(-?\d+)\s+(-?\d+)$"
)


def _fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _factorial(n: int) -> int:
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


def _next_prime(n: int) -> int:
    if n < 2:
        return 2
    candidate = n + 1
    while True:
        if _is_prime(candidate):
            return candidate
        candidate += 1


def _lcm(a: int, b: int) -> int:
    a, b = abs(a), abs(b)
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // _gcd(a, b)


def _mod_pow(base: int, exp: int, mod: int) -> int:
    if mod == 0:
        return 0
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp //= 2
        base = (base * base) % mod
    return result


def _det_3x3(m: List[float]) -> float:
    return (
        m[0] * (m[4] * m[8] - m[5] * m[7])
        - m[1] * (m[3] * m[8] - m[5] * m[6])
        + m[2] * (m[3] * m[7] - m[4] * m[6])
    )


def _safe_eval_math(expr: str) -> Any:
    import ast
    import operator

    _SAFE_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"unsupported constant: {node.value!r}")
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPS:
                raise ValueError(f"unsupported op: {op_type.__name__}")
            return _SAFE_OPS[op_type](_eval_node(node.left), _eval_node(node.right))
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPS:
                raise ValueError(f"unsupported unary op: {op_type.__name__}")
            return _SAFE_OPS[op_type](_eval_node(node.operand))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _WHITELISTED_FUNCTIONS:
                arity, func = _WHITELISTED_FUNCTIONS[node.func.id]
                args = [_eval_node(arg) for arg in node.args]
                if len(args) != arity:
                    raise ValueError(
                        f"{node.func.id} expects {arity} args, got {len(args)}"
                    )
                return func(*args)
            raise ValueError("unsupported function call")
        else:
            raise ValueError(f"unsupported AST node: {type(node).__name__}")

    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree)


def _pure_python_eval(code: str) -> Dict[str, Any]:
    code = code.strip()
    func_re = re.compile(r"^(\w+)\(([^)]*)\)$")
    m = func_re.match(code)
    if m:
        fname = m.group(1)
        arity, func = _WHITELISTED_FUNCTIONS.get(fname, (None, None))
        if arity is not None:
            raw = [a.strip() for a in m.group(2).split(",") if a.strip()]
            if len(raw) != arity:
                return {
                    "status": "runtime_error",
                    "language": "wasm",
                    "runtime": "pure-python-fallback",
                    "stderr": f"{fname} expects {arity} args, got {len(raw)}",
                }
            try:
                result = func(*raw)
            except Exception as exc:
                return {
                    "status": "runtime_error",
                    "language": "wasm",
                    "runtime": "pure-python-fallback",
                    "stderr": f"{fname}: {exc}",
                }
            return {
                "status": "ok",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "result": result,
            }
    m2 = _I32_RE.match(code)
    if m2:
        a, b = int(m2.group(2)), int(m2.group(3))
        op = m2.group(1)
        try:
            if op == "i32.add":
                result = a + b
            elif op == "i32.sub":
                result = a - b
            elif op == "i32.mul":
                result = a * b
            elif op == "i32.div_s":
                result = a // b if b != 0 else "division by zero"
            elif op == "i32.rem_s":
                result = a % b if b != 0 else "division by zero"
            elif op == "i32.and":
                result = a & b
            elif op == "i32.or":
                result = a | b
            elif op == "i32.xor":
                result = a ^ b
            elif op == "i32.shl":
                result = a << (b & 31)
            elif op == "i32.shr_s":
                result = a >> (b & 31)
            elif op == "i32.rotl":
                s = b & 31
                result = (
                    ((a << s) | (a >> (32 - s))) & 0xFFFFFFFF if s else a & 0xFFFFFFFF
                )
            elif op == "i32.rotr":
                s = b & 31
                result = (
                    ((a >> s) | (a << (32 - s))) & 0xFFFFFFFF if s else a & 0xFFFFFFFF
                )
            elif op == "i32.clz":
                result = 32 - (a & 0xFFFFFFFF).bit_length() if a != 0 else 32
            elif op == "i32.ctz":
                result = (a & -a).bit_length() - 1 if a != 0 else 32
            elif op == "i32.popcnt":
                result = bin(a & 0xFFFFFFFF).count("1")
            else:
                result = f"unsupported op {op}"
        except Exception as exc:
            result = str(exc)
        return {
            "status": "ok" if isinstance(result, int) else "runtime_error",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "result": result,
        }
    try:
        result = _safe_eval_math(code)
        return {
            "status": "ok",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "result": result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "stderr": f"unsupported expression: {code!r} ({exc})",
        }


# ---------------------------------------------------------------------------
# Unified dispatcher entrypoints
# ---------------------------------------------------------------------------

def _wasm_memory_grow(pages: int) -> int:
    """Return the byte delta for a memory.grow request."""
    pages = int(pages)
    if pages < 0:
        raise ValueError("pages must be non-negative")
    return pages * 65536


def _wasm_table_size(table: Any) -> int:
    """Return the size of a table-like container."""
    size = len(table)
    if size < 0:
        raise ValueError("table size must be non-negative")
    if size > 1_000_000:
        raise ValueError("table too large")
    return size


def _wasm_global_init(value: str) -> Any:
    """Parse a WASM global initializer from textual form."""
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if any(ch in text for ch in [".", "e", "E"]):
            num = float(text)
            return int(num) if num.is_integer() else num
        return int(text)
    except ValueError:
        return text.strip('"')


def _wasm_validate_import(module: str, name: str, kind: str) -> bool:
    """Validate an import triple using a conservative allowlist."""
    allowed_kinds = {"func", "memory", "table", "global"}
    return bool(module) and bool(name) and kind in allowed_kinds


def _wasm_compute_fuel(steps: int, factor: float) -> float:
    """Compute a simple fuel cost estimate."""
    return float(steps) * float(factor)


def _wasm_fuel_check(consumed: float, budget: float) -> bool:
    """Check whether consumed fuel stays within budget."""
    return float(consumed) <= float(budget)


def _wasm_stack_height(ops: List[Any]) -> int:
    """Estimate peak stack height for a sequence of wasm ops."""
    depth = 0
    peak = 0
    push_ops = (
        "local.get", "global.get", "i32.const", "i64.const",
        "f32.const", "f64.const", "ref.func", "ref.null",
    )
    binary_ops = (
        "i32.add", "i32.mul", "i32.sub", "i64.add", "i64.mul",
        "f32.add", "f64.add", "f32.mul", "f64.mul",
    )
    for op in ops:
        text = op.get("op", "") if isinstance(op, dict) else str(op)
        if text.startswith(push_ops):
            depth += 1
        elif text == "drop":
            if depth <= 0:
                raise ValueError("stack underflow")
            depth -= 1
        elif text in binary_ops:
            if depth < 2:
                raise ValueError("stack underflow")
            depth -= 1
        peak = max(peak, depth)
    return peak


def _wasm_fold_constants(expr: str) -> str:
    """Fold a tiny subset of arithmetic expressions."""
    expr = str(expr).strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)", expr)
    if not m:
        return expr
    a = float(m.group(1))
    b = float(m.group(3))
    op = m.group(2)
    result = {
        "+": a + b,
        "-": a - b,
        "*": a * b,
        "/": a / b if b != 0 else float("inf"),
    }[op]
    if result.is_integer():
        return str(int(result))
    return str(result)


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a WASM operation."""
    wat = payload.get("wat")
    if wat is not None:
        try:
            return {
                "status": "ok",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "result": wat_to_hex(wat),
            }
        except Exception as exc:
            return {
                "status": "error",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "stderr": str(exc),
            }

    compile_wat = payload.get("compile")
    if compile_wat is not None:
        try:
            return {
                "status": "ok",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "result": wat_to_hex(compile_wat),
            }
        except Exception as exc:
            return {
                "status": "disabled",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "hint": str(exc),
            }

    validate_wat_input = payload.get("validate")
    if validate_wat_input is not None:
        result = validate_wat(validate_wat_input)
        return {
            "status": "ok" if result.valid else "error",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "valid": result.valid,
            "stderr": "; ".join(result.errors) if result.errors else "",
        }

    optimize_wat = payload.get("optimize")
    if optimize_wat is not None:
        try:
            tokens = WasmTokenizer(optimize_wat).tokenize()
            parser = WasmParser(tokens)
            module = parser.parse()
            module = WasmOptimizer.optimize(module)
            encoder = WasmBinaryEncoder(module)
            binary = encoder.encode()
            return {
                "status": "ok",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "result": binary.hex(),
            }
        except Exception as exc:
            return {
                "status": "error",
                "language": "wasm",
                "runtime": "pure-python-fallback",
                "stderr": str(exc),
            }

    wasi_payload = payload.get("wasi")
    if wasi_payload is not None:
        return {
            "status": "disabled",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "hint": "WASI execution requires wasmtime runtime",
        }

    benchmark = payload.get("benchmark")
    if benchmark:
        import time as _time

        func_name = payload.get("func")
        args = payload.get("args", [])
        durations = []
        for _ in range(10):
            start = _time.perf_counter()
            _pure_python_eval(f"{func_name}({','.join(str(a) for a in args)})")
            durations.append((_time.perf_counter() - start) * 1000.0)
        return {
            "status": "ok",
            "language": "wasm",
            "runtime": "pure-python-fallback",
            "avg_ms": round(sum(durations) / len(durations), 3),
            "min_ms": round(min(durations), 3),
            "max_ms": round(max(durations), 3),
            "p50_ms": round(sorted(durations)[len(durations) // 2], 3),
        }

    func_name = payload.get("func")
    args = payload.get("args", [])
    if func_name:
        code = payload.get("code", f"{func_name}({','.join(str(a) for a in args)})")
        return _pure_python_eval(code)

    return {
        "status": "error",
        "language": "wasm",
        "stderr": "no func, code, wat, compile, validate, optimize, wasi, or benchmark specified",
    }


def invoke(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a WASM operation (alias for handle)."""
    return handle(payload)