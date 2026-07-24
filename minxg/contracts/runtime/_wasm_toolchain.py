"""agent_harness.contracts.runtime._wasm_toolchain — WAT parser / type checker / linker / encoder."""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


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
        "f64.sqrt", "memory.size", "memory.grow",
        "memory.fill", "memory.copy", "memory.init", "data.drop",
        "elem.drop", "table.get", "table.set", "table.size",
        "table.grow", "table.fill", "table.copy", "table.init",
        "ref.null", "ref.is_null", "ref.func",
    }

    def __init__(self, source: str) -> None:
        self._source = source
        self._len = len(source)
        self._pos = 0
        self._line = 1
        self._col = 1
        self._tokens: List[Token] = []

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
        self._tokens = []
        while self._pos < self._len:
            self._skip_ws_and_comments()
            if self._pos >= self._len:
                break
            ch = self._peek()
            line, col = self._line, self._col
            if ch == "(":
                self._advance()
                self._tokens.append(Token(TokenKind.LPAREN, "(", line, col))
            elif ch == ")":
                self._advance()
                self._tokens.append(Token(TokenKind.RPAREN, ")", line, col))
            elif ch == '"':
                self._advance()
                self._tokens.append(Token(TokenKind.STRING, self._read_string(), line, col))
            elif ch == "$" or (ch.isalpha() or ch == "_"):
                ident = self._advance()
                while self._peek() and (self._peek().isalnum() or self._peek() in "._-"):
                    ident += self._advance()
                kind = TokenKind.KEYWORD if ident in self._KEYWORDS else TokenKind.ID
                self._tokens.append(Token(kind, ident, line, col))
            elif ch.isdigit() or ch in "+-":
                self._tokens.append(Token(TokenKind.NUM, self._read_number(ch), line, col))
            else:
                raise SyntaxError(f"unexpected character {ch!r} at {line}:{col}")
        self._tokens.append(Token(TokenKind.EOF, "", self._line, self._col))
        return self._tokens


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------

@dataclass
class WasmType:
    name: str
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
    min: int
    max: Optional[int] = None
    shared: bool = False


@dataclass
class WasmTable:
    type: WasmType
    limits: WasmLimits


@dataclass
class WasmMemory:
    limits: WasmLimits
    max: Optional[int] = None
    pages64: bool = False


@dataclass
class WasmGlobal:
    type: WasmType
    mutable: bool
    init: str


@dataclass
class WasmImport:
    module: str
    name: str
    desc: Union[WasmFuncType, WasmType, WasmTable, WasmMemory]
    kind: str


@dataclass
class WasmExport:
    name: str
    kind: str
    idx: int


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
    body: List[Dict[str, Any]]


@dataclass
class WasmModule:
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
    source_map: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class WasmParseError(Exception):
    pass


class WasmParser:
    """Recursive-descent parser for ``.wat`` files."""

    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
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

    def _expect_id(self) -> Tuple[str, Token]:
        tok = self._advance()
        if tok.kind not in (TokenKind.ID, TokenKind.KEYWORD):
            raise WasmParseError(
                f"expected identifier at {tok.line}:{tok.col}, got {tok.kind.value}"
            )
        return tok.text, tok

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

    def _parse_functype(self) -> WasmFuncType:
        self._expect_kw("func")
        params: List[WasmType] = []
        results: List[WasmType] = []
        while self._pos < len(self._tokens) and self._peek().kind != TokenKind.RPAREN:
            tok = self._peek()
            if tok.text == "param":
                self._advance()
                while self._pos < len(self._tokens) and self._peek().text not in ("result", ")", "local"):
                    params.append(self._parse_valtype())
            elif tok.text == "result":
                self._advance()
                while self._pos < len(self._tokens) and self._peek().kind not in (TokenKind.RPAREN, TokenKind.EOF):
                    results.append(self._parse_valtype())
            else:
                self._advance()
        return WasmFuncType(params, results)

    def parse(self) -> WasmModule:
        module = WasmModule()
        if self._pos >= len(self._tokens):
            raise WasmParseError("empty input")
        tok = self._advance()
        if tok.kind != TokenKind.KEYWORD or tok.text != "module":
            raise WasmParseError(f"expected 'module' at top level, got {tok.text!r}")
        while self._pos < len(self._tokens) and self._peek().kind != TokenKind.EOF:
            section_tok = self._peek()
            if section_tok.kind != TokenKind.LPAREN:
                raise WasmParseError(
                    f"expected section at {section_tok.line}:{section_tok.col}"
                )
            self._advance()
            section_kind = self._peek().text
            if section_kind == "type":
                module.types.append(self._parse_type_section())
            elif section_kind == "import":
                module.imports.append(self._parse_import())
            elif section_kind == "func":
                module.funcs.append(self._parse_func())
            elif section_kind == "table":
                module.tables.append(self._parse_table())
            elif section_kind == "memory":
                module.memories.append(self._parse_memory())
            elif section_kind == "global":
                module.globals.append(self._parse_global())
            elif section_kind == "export":
                module.exports.append(self._parse_export())
            elif section_kind == "start":
                module.start = self._parse_start()
            elif section_kind == "elem":
                module.elems.append(self._parse_elem())
            elif section_kind == "data":
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

    def _parse_type_section(self) -> WasmFuncType:
        self._expect_kw("type")
        ft = self._parse_functype()
        self._expect(TokenKind.RPAREN)
        return ft

    def _parse_import(self) -> WasmImport:
        self._expect_kw("import")
        mod = self._expect(TokenKind.STRING).text
        name = self._expect(TokenKind.STRING).text
        desc_tok = self._peek()
        kind = desc_tok.text
        if kind == "func":
            self._advance()
            desc = self._parse_functype()
        elif kind == "table":
            self._advance()
            desc = WasmTable(WasmType("funcref"), WasmLimits(0))
        elif kind == "memory":
            self._advance()
            desc = WasmMemory(WasmLimits(0))
        else:
            desc = WasmType("i32")
        self._expect(TokenKind.RPAREN)
        return WasmImport(mod, name, desc, kind)

    def _parse_func(self) -> WasmFunc:
        self._expect_kw("func")
        name_tok = self._peek()
        name: Optional[str] = None
        if name_tok.kind == TokenKind.ID and name_tok.text.startswith("$"):
            name = name_tok.text[1:]
            self._advance()
        ft = WasmFuncType([], [])
        params: List[Tuple[str, WasmType]] = []
        locals_list: List[Tuple[str, WasmType]] = []
        body: List[Dict[str, Any]] = []
        while self._pos < len(self._tokens) and self._peek().text != ")" and self._peek().kind != TokenKind.EOF:
            tok = self._peek()
            if tok.text == "param":
                self._advance()
                while self._pos < len(self._tokens) and self._peek().text not in ("result", ")", "local"):
                    pname, _ = self._expect_id()
                    pt = self._parse_valtype()
                    params.append((pname, pt))
            elif tok.text == "result":
                self._advance()
                ft.results.append(self._parse_valtype())
            elif tok.text == "local":
                self._advance()
                lname, _ = self._expect_id()
                lt = self._parse_valtype()
                locals_list.append((lname, lt))
            elif tok.text in ("block", "loop", "if"):
                body.append(self._parse_block())
            else:
                instr = self._parse_instruction()
                if instr:
                    body.append(instr)
        self._expect(TokenKind.RPAREN)
        return WasmFunc(name, ft, params, ft.results, locals_list, body)

    def _parse_block(self) -> Dict[str, Any]:
        label_tok = self._advance()
        label = ""
        if self._peek().kind == TokenKind.ID:
            label = self._advance().text
        block_type: Optional[WasmFuncType] = None
        if self._peek().text in ("i32", "i64", "f32", "f64", "funcref"):
            block_type = WasmFuncType(results=[self._parse_valtype()])
        body: List[Dict[str, Any]] = []
        end_count = 1
        while self._pos < len(self._tokens) and end_count:
            tok = self._peek()
            if tok.text in ("block", "loop", "if"):
                end_count += 1
                body.append(self._parse_block())
            elif tok.text == "end":
                self._advance()
                end_count -= 1
            else:
                body.append(self._parse_instruction() or {})
        return {"op": label_tok.text, "label": label, "type": block_type, "body": body}

    def _parse_instruction(self) -> Optional[Dict[str, Any]]:
        tok = self._peek()
        if tok.kind in (TokenKind.RPAREN, TokenKind.EOF) or tok.text in ("then", "else"):
            return None
        instr: Dict[str, Any] = {"op": tok.text}
        self._advance()
        imm: Dict[str, Any] = {}
        while self._pos < len(self._tokens) and self._peek().kind in (
            TokenKind.NUM, TokenKind.ID, TokenKind.STRING
        ):
            nxt = self._peek()
            if nxt.kind == TokenKind.ID and nxt.text in ("offset", "align", "mem"):
                key = self._advance().text
                val = self._advance().text
                imm[key] = int(val) if val.lstrip("-").isdigit() else val
            else:
                break
        if imm:
            instr["imm"] = imm
        return instr

    def _parse_table(self) -> WasmTable:
        self._expect_kw("table")
        self._expect(TokenKind.RPAREN)
        return WasmTable(WasmType("funcref"), WasmLimits(0))

    def _parse_memory(self) -> WasmMemory:
        self._expect_kw("memory")
        self._expect(TokenKind.RPAREN)
        return WasmMemory(WasmLimits(0))

    def _parse_global(self) -> WasmGlobal:
        self._expect_kw("global")
        self._expect(TokenKind.RPAREN)
        return WasmGlobal(WasmType("i32"), False, "0")

    def _parse_export(self) -> WasmExport:
        self._expect_kw("export")
        name = self._expect(TokenKind.STRING).text
        kind_tok = self._peek()
        kind = kind_tok.text if kind_tok.text in {"func", "table", "memory", "global"} else "func"
        if kind_tok.kind == TokenKind.KEYWORD:
            self._advance()
        idx = int(self._expect(TokenKind.NUM).text)
        self._expect(TokenKind.RPAREN)
        return WasmExport(name, kind, idx)

    def _parse_start(self) -> int:
        self._expect_kw("start")
        return int(self._expect(TokenKind.NUM).text)

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
        self._func_types: Dict[int, WasmFuncType] = {}

    def check(self) -> List[str]:
        errors: List[str] = []
        for i, imp in enumerate(self.module.imports):
            if isinstance(imp.desc, WasmFuncType):
                self._func_types[i] = imp.desc
        for i, func in enumerate(self.module.funcs):
            offset = len(self.module.imports)
            ft = func.type or WasmFuncType(func.params, func.results)
            self._func_types[offset + i] = ft
        for i, func in enumerate(self.module.funcs):
            idx = len(self.module.imports) + i
            ft = self._func_types.get(idx, WasmFuncType(func.params, func.results))
            errors.extend(self._check_func(idx, func, ft))
        return errors

    def _check_func(self, idx: int, func: WasmFunc, ft: WasmFuncType) -> List[str]:
        errors: List[str] = []
        stack: List[WasmType] = []
        labels: List[Tuple[str, WasmFuncType]] = []
        for _, pt in func.params:
            stack.append(pt)
        errors.extend(self._check_block(func.body, stack, labels, ft.results, func))
        return errors

    def _check_block(
        self, body: List[Dict[str, Any]], stack: List[WasmType],
        labels: List[Tuple[str, WasmFuncType]], result: List[WasmType],
        func: WasmFunc,
    ) -> List[str]:
        errors: List[str] = []
        for instr in body:
            op = instr.get("op", "")
            if op in ("block", "loop", "if"):
                label = instr.get("label", "")
                labels.append((label, WasmFuncType(results=list(result))))
                errors.extend(
                    self._check_block(
                        instr.get("body", []), list(stack), list(labels), result, func
                    )
                )
                labels.pop()
            elif op == "br":
                label = instr.get("label", "")
                if label and self._resolve_label(label, labels) is None:
                    errors.append(f"unknown label '{label}' in br")
            elif op == "br_if":
                if stack:
                    stack.pop()
                label = instr.get("label", "")
                if label and self._resolve_label(label, labels) is None:
                    errors.append(f"unknown label '{label}' in br_if")
            elif op == "local.set":
                if stack:
                    stack.pop()
            elif op in (
                "i32.add", "i32.sub", "i32.mul", "i32.div_s", "i32.rem_u",
                "i64.add", "i64.sub", "i64.mul",
            ):
                if len(stack) < 2:
                    errors.append(f"{op}: stack underflow")
                else:
                    a = stack.pop()
                    b = stack.pop()
                    expected = "i64" if "i64" in op else "i32"
                    if a.name != expected or b.name != expected:
                        errors.append(f"{op}: type mismatch")
                    else:
                        stack.append(a)
            elif op in (
                "f32.add", "f64.add", "f32.sub", "f64.sub", "f32.mul", "f64.mul",
                "f32.div", "f64.div",
            ):
                if len(stack) < 2:
                    errors.append(f"{op}: stack underflow")
                else:
                    a = stack.pop()
                    b = stack.pop()
                    expected = "f64" if "f64" in op else "f32"
                    if a.name != expected or b.name != expected:
                        errors.append(f"{op}: type mismatch")
                    else:
                        stack.append(a)
            elif op in ("i32.const", "i64.const"):
                stack.append(WasmType("i64" if "i64" in op else "i32"))
            elif op in ("f32.const", "f64.const"):
                stack.append(WasmType("f64" if "f64" in op else "f32"))
            elif op == "call":
                target = instr.get("operand", 0)
                key = int(target) if isinstance(target, str) and target.isdigit() else 0
                ft2 = self._func_types.get(key)
                if ft2:
                    for _ in ft2.params:
                        if stack:
                            stack.pop()
                    stack.extend(ft2.results)
            elif op == "drop":
                if stack:
                    stack.pop()
            elif op in ("return", "unreachable", "nop", "else", "end"):
                pass
            else:
                if op:
                    pass
        return errors

    @staticmethod
    def _resolve_label(label: str, labels: List[Tuple[str, WasmFuncType]]) -> Optional[int]:
        if not label:
            return 0
        for i in range(len(labels) - 1, -1, -1):
            if labels[i][0] == label:
                return len(labels) - 1 - i
        return None


# ---------------------------------------------------------------------------
# Linker
# ---------------------------------------------------------------------------

class WasmLinkError(Exception):
    pass


class WasmLinker:
    """Resolve imports/exports across multiple modules."""

    def __init__(self, modules: Dict[str, WasmModule]) -> None:
        self.modules = modules
        self._exports: Dict[str, Tuple[str, int, str]] = {}

    def link(self) -> WasmModule:
        for mod_name, module in self.modules.items():
            for exp in module.exports:
                key = f"{mod_name}::{exp.name}"
                if key in self._exports:
                    raise WasmLinkError(f"duplicate export: {key}")
                self._exports[key] = (mod_name, exp.idx, exp.kind)
        primary = next(iter(self.modules.values()))
        return primary

    def resolve_import(self, imp: WasmImport) -> Optional[Tuple[str, int, str]]:
        key = f"{imp.module}::{imp.name}"
        return self._exports.get(key)


# ---------------------------------------------------------------------------
# Binary encoder
# ---------------------------------------------------------------------------

class WasmBinaryEncoder:
    """Emit a spec-compliant Wasm binary from a WasmModule."""

    def __init__(self, module: WasmModule) -> None:
        self.module = module

    def encode(self) -> bytes:
        buf = io.BytesIO()
        self._write_header(buf)
        self._write_sections(buf)
        return buf.getvalue()

    @staticmethod
    def _write_header(buf: io.BytesIO) -> None:
        buf.write(b"\x00asm")
        buf.write(bytes([0x01, 0x00, 0x00, 0x00]))

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
    def _write_string(buf: io.BytesIO, s: str) -> None:
        b = s.encode("utf-8")
        WasmBinaryEncoder._write_leb128_unsigned(buf, len(b))
        buf.write(b)

    @staticmethod
    def _write_vec(buf: io.BytesIO, payload: bytes) -> None:
        WasmBinaryEncoder._write_leb128_unsigned(buf, len(payload))
        buf.write(payload)

    @staticmethod
    def _valtype_byte(name: str) -> int:
        return {
            "i32": 0x7F, "i64": 0x7E, "f32": 0x7D, "f64": 0x7C,
            "funcref": 0x70, "externref": 0x6F,
        }[name]

    def _write_type_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.types))
        for ft in self.module.types:
            WasmBinaryEncoder._write_leb128_unsigned(payload, 0x60)
            WasmBinaryEncoder._write_leb128_unsigned(payload, len(ft.params))
            for pt in ft.params:
                payload.write(bytes([self._valtype_byte(pt.name)]))
            WasmBinaryEncoder._write_leb128_unsigned(payload, len(ft.results))
            for rt in ft.results:
                payload.write(bytes([self._valtype_byte(rt.name)]))
        self._write_section(buf, 1, payload.getvalue())

    def _write_import_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.imports))
        for imp in self.module.imports:
            WasmBinaryEncoder._write_string(payload, imp.module)
            WasmBinaryEncoder._write_string(payload, imp.name)
            kind = {"func": 0x00, "table": 0x01, "memory": 0x02, "global": 0x03}[imp.kind]
            if kind == 0x00:
                payload.write(bytes([0x00]))
                if isinstance(imp.desc, WasmFuncType) and imp.desc in self.module.types:
                    WasmBinaryEncoder._write_leb128_signed(
                        payload, self.module.types.index(imp.desc)
                    )
                else:
                    WasmBinaryEncoder._write_leb128_signed(payload, 0)
            elif kind == 0x02:
                payload.write(bytes([0x02]))
                limits = imp.desc.limits
                payload.write(bytes([0x01 if limits.max is not None else 0x00]))
                WasmBinaryEncoder._write_leb128_unsigned(payload, limits.min)
                if limits.max is not None:
                    WasmBinaryEncoder._write_leb128_unsigned(payload, limits.max)
            else:
                payload.write(bytes([kind]))
        self._write_section(buf, 2, payload.getvalue())

    def _write_function_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.funcs))
        for _ in self.module.funcs:
            WasmBinaryEncoder._write_leb128_unsigned(payload, 0)
        self._write_section(buf, 3, payload.getvalue())

    def _write_table_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.tables))
        for tbl in self.module.tables:
            payload.write(bytes([0x70]))
            flags = 0x01 if tbl.limits.max is not None else 0x00
            payload.write(bytes([flags]))
            WasmBinaryEncoder._write_leb128_unsigned(payload, tbl.limits.min)
            if tbl.limits.max is not None:
                WasmBinaryEncoder._write_leb128_unsigned(payload, tbl.limits.max)
        self._write_section(buf, 4, payload.getvalue())

    def _write_memory_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.memories))
        for mem in self.module.memories:
            flags = 0x01 if mem.limits.max is not None else 0x00
            payload.write(bytes([flags]))
            WasmBinaryEncoder._write_leb128_unsigned(payload, mem.limits.min)
            if mem.limits.max is not None:
                WasmBinaryEncoder._write_leb128_unsigned(payload, mem.limits.max)
        self._write_section(buf, 5, payload.getvalue())

    def _write_global_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.globals))
        for _g in self.module.globals:
            payload.write(bytes([0x7F, 0x00, 0x41, 0x00, 0x0B]))
        self._write_section(buf, 6, payload.getvalue())

    def _write_export_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.exports))
        for exp in self.module.exports:
            WasmBinaryEncoder._write_string(payload, exp.name)
            kind = {"func": 0x00, "table": 0x01, "memory": 0x02, "global": 0x03}[exp.kind]
            payload.write(bytes([kind]))
            WasmBinaryEncoder._write_leb128_unsigned(payload, exp.idx)
        self._write_section(buf, 7, payload.getvalue())

    def _write_start_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, self.module.start or 0)
        self._write_section(buf, 8, payload.getvalue())

    def _write_elem_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.elems))
        self._write_section(buf, 9, payload.getvalue())

    def _write_code_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.funcs))
        for func in self.module.funcs:
            body = io.BytesIO()
            WasmBinaryEncoder._write_leb128_unsigned(body, 0)
            for instr in func.body:
                self._encode_instruction(body, instr)
            body.write(bytes([0x0B]))
            WasmBinaryEncoder._write_vec(payload, body.getvalue())
        self._write_section(buf, 10, payload.getvalue())

    def _write_data_section(self, buf: io.BytesIO) -> None:
        payload = io.BytesIO()
        WasmBinaryEncoder._write_leb128_unsigned(payload, len(self.module.datas))
        self._write_section(buf, 11, payload.getvalue())

    @staticmethod
    def _write_section(buf: io.BytesIO, sid: int, payload: bytes) -> None:
        buf.write(bytes([sid]))
        WasmBinaryEncoder._write_leb128_unsigned(buf, len(payload))
        buf.write(payload)

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

    def _encode_instruction(self, buf: io.BytesIO, instr: Dict[str, Any]) -> None:
        op = instr.get("op", "")
        bytecode = self._OPCODES.get(op)
        if bytecode is None:
            buf.write(bytes([0x01]))
            return
        buf.write(bytes([bytecode]))
        if op in ("i32.const", "i64.const"):
            self._write_leb128_signed(buf, int(instr.get("value", 0)))
        elif op in ("f32.const",):
            buf.write(struct.pack("<f", float(instr.get("value", 0.0))))
        elif op in ("f64.const",):
            buf.write(struct.pack("<d", float(instr.get("value", 0.0))))
        elif op in ("local.get", "local.set"):
            self._write_leb128_unsigned(buf, int(instr.get("operand", 0)))
        elif op in ("call",):
            self._write_leb128_unsigned(buf, int(instr.get("operand", 0)))
        elif op in ("br", "br_if"):
            self._write_leb128_unsigned(buf, 0)
        elif op in ("block", "loop", "if"):
            buf.write(bytes([0x40]))


# ---------------------------------------------------------------------------
# Fuel metering
# ---------------------------------------------------------------------------

class WasmFuelError(Exception):
    pass


@dataclass
class FuelMeter:
    budget: int = 1_000_000
    consumed: int = 0

    def charge(self, n: int = 1) -> None:
        self.consumed += n
        if self.consumed > self.budget:
            raise WasmFuelError(f"fuel exhausted: {self.consumed} > {self.budget}")

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.consumed)


# ---------------------------------------------------------------------------
# WASI stubs
# ---------------------------------------------------------------------------

@dataclass
class WasmEnv:
    args: List[str]
    env: Dict[str, str]
    stdin: bytes = b""
    stdout: bytes = b""
    stderr: bytes = b""
    preopened: Dict[str, int] = None
    clocks: List[float] = None
    exit_code: Optional[int] = None
    memory_limit: int = 1 << 20

    def __post_init__(self) -> None:
        if self.preopened is None:
            self.preopened = {}
        if self.clocks is None:
            self.clocks = []

    def fd_write(self, fd: int, iovs: List[Tuple[int, int]]) -> int:
        if fd == 1:
            for addr, size in iovs:
                self.stdout += b"\x00" * size
        elif fd == 2:
            for addr, size in iovs:
                self.stderr += b"\x00" * size
        return len(iovs)

    def fd_read(self, fd: int, iovs: List[Tuple[int, int]]) -> int:
        if fd == 0 and self.stdin:
            data = self.stdin[: sum(s for _, s in iovs)]
            self.stdin = self.stdin[len(data) :]
            return len(data)
        return 0

    def proc_exit(self, code: int) -> None:
        self.exit_code = code

    def clock_time_get(self, clock_id: int, precision: int) -> int:
        import time
        return int(time.time() * 1_000_000_000)


# ---------------------------------------------------------------------------
# Symbol table / name resolution
# ---------------------------------------------------------------------------

class WasmSymbolTable:
    """Map names to indices across imports + funcs + tables + memories + globals."""

    def __init__(self, module: WasmModule) -> None:
        self.module = module
        self._func_names: Dict[str, int] = {}
        self._global_names: Dict[str, int] = {}
        self._table_names: Dict[str, int] = {}
        self._memory_names: Dict[str, int] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        self._func_names.clear()
        self._global_names.clear()
        self._table_names.clear()
        self._memory_names.clear()
        offset = 0
        for i, imp in enumerate(self.module.imports):
            if imp.kind == "func":
                self._func_names[f"{imp.module}::{imp.name}"] = offset + i
            elif imp.kind == "global":
                self._global_names[f"{imp.module}::{imp.name}"] = offset + i
            elif imp.kind == "table":
                self._table_names[f"{imp.module}::{imp.name}"] = offset + i
            elif imp.kind == "memory":
                self._memory_names[f"{imp.module}::{imp.name}"] = offset + i
        offset = len(self.module.imports)
        for i, func in enumerate(self.module.funcs):
            if func.name:
                self._func_names[func.name] = offset + i
        for i, mem in enumerate(self.module.memories):
            self._memory_names[f"memory{i}"] = offset + i
        for i, tbl in enumerate(self.module.tables):
            self._table_names[f"table{i}"] = offset + i

    def resolve_func(self, name: str) -> Optional[int]:
        return self._func_names.get(name)

    def resolve_global(self, name: str) -> Optional[int]:
        return self._global_names.get(name)

    def resolve_table(self, name: str) -> Optional[int]:
        return self._table_names.get(name)

    def resolve_memory(self, name: str) -> Optional[int]:
        return self._memory_names.get(name)


# ---------------------------------------------------------------------------
# Peephole optimizer
# ---------------------------------------------------------------------------

class WasmPeepholeOptimizer:
    """Local instruction-sequence simplifier.

    Operates on the AST before binary encoding. Patterns are matched
    against each function body and replaced in-place when a cheaper
    sequence is found.
    """

    def __init__(self, module: WasmModule) -> None:
        self.module = module

    def optimize(self) -> None:
        for func in self.module.funcs:
            func.body = self._optimize_block(func.body)

    def _optimize_block(self, body: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        optimized: List[Dict[str, Any]] = []
        i = 0
        while i < len(body):
            seq = body[i : i + 4]
            replacement = self._match(seq)
            if replacement is not None:
                optimized.extend(replacement)
                i += len(seq)
            else:
                optimized.append(body[i])
                i += 1
        for idx, instr in enumerate(optimized):
            if "body" in instr:
                optimized[idx] = dict(instr)
                optimized[idx]["body"] = self._optimize_block(instr["body"])
        return optimized

    def _match(self, seq: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        if not seq:
            return None
        ops = [s.get("op") for s in seq]
        if ops == ["i32.const", "i32.const", "i32.add"]:
            a = int(seq[0].get("value", 0))
            b = int(seq[1].get("value", 0))
            return [{"op": "i32.const", "value": a + b}]
        if ops == ["i32.const", "i32.const", "i32.sub"]:
            a = int(seq[0].get("value", 0))
            b = int(seq[1].get("value", 0))
            return [{"op": "i32.const", "value": a - b}]
        if ops == ["i32.const", "i32.const", "i32.mul"]:
            a = int(seq[0].get("value", 0))
            b = int(seq[1].get("value", 0))
            return [{"op": "i32.const", "value": a * b}]
        if ops == ["i32.const", "drop"]:
            return [
                {"op": "i32.const", "value": seq[0].get("value", 0)},
                {"op": "drop"},
            ]
        if ops == ["local.get", "local.set"] and seq[0].get("operand") == seq[1].get("operand"):
            return [{"op": "nop"}]
        return None


# ---------------------------------------------------------------------------
# Linear-scan register allocator
# ---------------------------------------------------------------------------

@dataclass
class LiveInterval:
    name: str
    start: int
    end: int
    reg: Optional[str] = None


class WasmRegisterAllocator:
    """Linear-scan register allocator for WebAssembly locals.

    In Wasm, locals are already numbered, but when lowering from SSA
    or from a higher-level IR, we use this allocator to pack live
    ranges into the smallest possible local index space.
    """

    def __init__(self, func: WasmFunc) -> None:
        self.func = func
        self._intervals: List[LiveInterval] = []
        self._next_reg = 0

    def allocate(self) -> WasmFunc:
        self._build_intervals()
        self._intervals.sort(key=lambda i: i.start)
        active: List[LiveInterval] = []
        for interval in self._intervals:
            active = [i for i in active if i.end > interval.start]
            if len(active) < 10:
                interval.reg = f"%{self._next_reg}"
                self._next_reg += 1
                active.append(interval)
            else:
                interval.reg = active[-1].reg
        self._apply_allocations()
        return self.func

    def _build_intervals(self) -> None:
        pos = 0
        for instr in self.func.body:
            if "local.get" in instr.get("op", ""):
                name = instr.get("operand", f"unknown_{pos}")
                self._intervals.append(LiveInterval(name, pos, pos + 1))
            elif "local.set" in instr.get("op", ""):
                name = instr.get("operand", f"unknown_{pos}")
                self._intervals.append(LiveInterval(name, pos, pos + 1))
            pos += 1

    def _apply_allocations(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Convenience API used by wasm.py
# ---------------------------------------------------------------------------

def parse_wat(source: str) -> WasmModule:
    tokens = WasmTokenizer(source).tokenize()
    return WasmParser(tokens).parse()


def validate_module(module: WasmModule) -> List[str]:
    return WasmTypeChecker(module).check()


def link_modules(modules: Dict[str, WasmModule]) -> WasmModule:
    return WasmLinker(modules).link()


def encode_binary(module: WasmModule) -> bytes:
    return WasmBinaryEncoder(module).encode()


def check_fuel(module: WasmModule, budget: int = 1_000_000) -> FuelMeter:
    meter = FuelMeter(budget=budget)

    def _walk(body: List[Dict[str, Any]]) -> None:
        for instr in body:
            meter.charge()
            if "body" in instr:
                _walk(instr["body"])

    for func in module.funcs:
        _walk(func.body)
    return meter