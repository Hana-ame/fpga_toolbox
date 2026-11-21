"""Verilog-2001 / SystemVerilog lexer & recursive-descent module header parser."""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SVToken:
    kind: str
    value: str
    line: int


@dataclass(frozen=True)
class SVPortAST:
    name: str
    direction: str
    width_expr: str
    width: int
    line: int


@dataclass(frozen=True)
class SVModuleAST:
    name: str
    file_path: str
    parameters: Dict[str, int]
    ports: Tuple[SVPortAST, ...]


_TOKEN_RE = re.compile(
    r"""
    (?P<WS>[ \t\r]+)
  | (?P<NL>\n)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<NUMBER>[0-9]+'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+|[0-9][0-9_]*)
  | (?P<SYM>[#()\[\],;=:\+\-\*/<>\{\}\.\?@!~&|\^])
    """,
    re.VERBOSE,
)

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Div: operator.floordiv,
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"(?m)^[ \t]*`[^\n]*", "", src)
    return src


def sv_tokenize(src: str) -> List[SVToken]:
    cleaned = strip_comments(src)
    tokens: List[SVToken] = []
    line, pos, n = 1, 0, len(cleaned)
    while pos < n:
        m = _TOKEN_RE.match(cleaned, pos)
        if not m:
            pos += 1
            continue
        kind, val, pos = m.lastgroup, m.group(0), m.end()
        if kind == "NL":
            line += 1
        elif kind != "WS":
            tokens.append(SVToken(kind=str(kind), value=val, line=line))
    return tokens


def _parse_sv_literal(val: str) -> Optional[int]:
    val = val.replace("_", "")
    if "'" in val:
        radix_part = val.split("'", 1)[1].lstrip("sS")
        base_map = {"b": 2, "d": 10, "h": 16, "o": 8}
        if radix_part and radix_part[0].lower() in base_map and not re.search(r"[xXzZ?]", radix_part[1:]):
            return int(radix_part[1:], base_map[radix_part[0].lower()])
        return None
    return int(val, 10) if val.isdigit() else None


def eval_int_expr(expr: str, params: Dict[str, int]) -> Optional[int]:
    expr = expr.strip()
    if not expr:
        return None
    expr = re.sub(
        r"\$clog2\s*\(([^()]+)\)",
        lambda m: str(max(1, ((eval_int_expr(m.group(1), params) or 1) - 1).bit_length())),
        expr,
    )
    expr = re.sub(
        r"[0-9]+'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+",
        lambda m: str(_parse_sv_literal(m.group(0)) or 0),
        expr,
    )
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None

    def _ev(nd: ast.AST) -> int:
        if isinstance(nd, ast.Constant) and isinstance(nd.value, int):
            return int(nd.value)
        if isinstance(nd, ast.Name) and nd.id in params:
            return params[nd.id]
        if isinstance(nd, ast.UnaryOp) and isinstance(nd.op, ast.USub):
            return -_ev(nd.operand)
        if isinstance(nd, ast.BinOp) and type(nd.op) in _SAFE_BINOPS:
            return _SAFE_BINOPS[type(nd.op)](_ev(nd.left), _ev(nd.right))
        raise ValueError("unsupported")

    try:
        return _ev(node)
    except Exception:
        return None


class SVModuleParser:
    _TYPES = {"wire", "reg", "logic", "bit", "signed", "unsigned", "int", "integer", "var", "tri"}
    _DIRS = {"input", "output", "inout"}

    def __init__(self, tokens: List[SVToken], file_path: str):
        self.tokens, self.file_path, self.i, self.n = tokens, file_path, 0, len(tokens)

    def _peek(self, off: int = 0) -> Optional[SVToken]:
        idx = self.i + off
        return self.tokens[idx] if 0 <= idx < self.n else None

    def _adv(self) -> SVToken:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _match(self, val: str) -> bool:
        if self._peek() and self._peek().value == val:
            self.i += 1
            return True
        return False

    def _read_balanced(self, open_s: str, close_s: str) -> List[SVToken]:
        out, depth = [], 0
        while self.i < self.n:
            tok = self._adv()
            if tok.value == open_s:
                depth += 1
                if depth > 1:
                    out.append(tok)
            elif tok.value == close_s:
                depth -= 1
                if depth == 0:
                    break
                out.append(tok)
            else:
                out.append(tok)
        return out

    def _split_top(self, toks: List[SVToken], sep: str = ",") -> List[List[SVToken]]:
        groups, cur, depth = [], [], 0
        for t in toks:
            if t.value in ("(", "[", "{"):
                depth += 1
            elif t.value in (")", "]", "}"):
                depth = max(0, depth - 1)
            if t.value == sep and depth == 0:
                if cur:
                    groups.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            groups.append(cur)
        return groups

    def _parse_params(self, toks: List[SVToken]) -> Dict[str, int]:
        params: Dict[str, int] = {}
        for item in self._split_top(toks):
            eq = next((i for i, t in enumerate(item) if t.value == "="), None)
            if eq and eq > 0:
                val = eval_int_expr(" ".join(t.value for t in item[eq + 1:]), params)
                if val is not None:
                    params[item[eq - 1].value] = val
        return params

    def _parse_range(self, toks: List[SVToken], start: int, params: Dict[str, int]) -> Tuple[str, int, int]:
        depth, end, inner = 0, start, []
        while end < len(toks):
            t = toks[end]
            if t.value == "[":
                depth += 1
                if depth > 1:
                    inner.append(t)
            elif t.value == "]":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
                inner.append(t)
            else:
                inner.append(t)
            end += 1
        colon = next((i for i, t in enumerate(inner) if t.value == ":"), None)
        expr = "[" + "".join(t.value for t in inner) + "]"
        if colon is not None:
            msb = eval_int_expr(" ".join(t.value for t in inner[:colon]), params)
            lsb = eval_int_expr(" ".join(t.value for t in inner[colon + 1:]), params)
            w = abs(msb - lsb) + 1 if (msb is not None and lsb is not None) else 1
        else:
            w = 1
        return expr, w, end

    def _parse_ansi(self, toks: List[SVToken], params: Dict[str, int]) -> List[SVPortAST]:
        ports, cur_dir, cur_wexpr, cur_w = [], "input", "1", 1
        for item in self._split_top(toks):
            idx = 0
            while idx < len(item):
                v = item[idx].value
                if v in self._DIRS:
                    cur_dir, cur_wexpr, cur_w = v, "1", 1
                    idx += 1
                elif v in self._TYPES:
                    idx += 1
                elif v == "[":
                    cur_wexpr, cur_w, idx = self._parse_range(item, idx, params)
                else:
                    break
            name_tok = None
            for t in item[idx:]:
                if t.value in ("=", "["):
                    break
                if t.kind == "IDENT":
                    name_tok = t
            if name_tok:
                ports.append(SVPortAST(name_tok.value, cur_dir, cur_wexpr, cur_w, name_tok.line))
        return ports

    def _parse_non_ansi(self, names: List[str], params: Dict[str, int]) -> List[SVPortAST]:
        declared: Dict[str, SVPortAST] = {}
        while self.i < self.n and self._peek().value != "endmodule":
            val = self._peek().value
            if val in ("parameter", "localparam"):
                self._adv()
                stmt = []
                while self.i < self.n and self._peek().value != ";":
                    stmt.append(self._adv())
                self._match(";")
                params.update(self._parse_params(stmt))
            elif val in self._DIRS:
                direction = self._adv().value
                stmt = []
                while self.i < self.n and self._peek().value != ";":
                    stmt.append(self._adv())
                self._match(";")
                idx, wexpr, w = 0, "1", 1
                while idx < len(stmt):
                    if stmt[idx].value in self._TYPES:
                        idx += 1
                    elif stmt[idx].value == "[":
                        wexpr, w, idx = self._parse_range(stmt, idx, params)
                    else:
                        break
                for grp in self._split_top(stmt[idx:]):
                    ident = next((t for t in grp if t.kind == "IDENT"), None)
                    if ident:
                        declared[ident.value] = SVPortAST(ident.value, direction, wexpr, w, ident.line)
            else:
                self._adv()
        return [declared[n] for n in names if n in declared]

    def parse_all(self) -> List[SVModuleAST]:
        mods: List[SVModuleAST] = []
        while self.i < self.n:
            if self._adv().value != "module":
                continue
            nt = self._peek()
            if not nt or nt.kind != "IDENT":
                continue
            mod_name = self._adv().value
            params: Dict[str, int] = {}
            if self._match("#") and self._peek() and self._peek().value == "(":
                params = self._parse_params(self._read_balanced("(", ")"))
            ports: List[SVPortAST] = []
            if self._peek() and self._peek().value == "(":
                ptoks = self._read_balanced("(", ")")
                self._match(";")
                if any(t.value in self._DIRS for t in ptoks):
                    ports = self._parse_ansi(ptoks, params)
                else:
                    names = [g[0].value for g in self._split_top(ptoks) if g and g[0].kind == "IDENT"]
                    ports = self._parse_non_ansi(names, params)
            else:
                self._match(";")
            while self.i < self.n and self._peek().value != "endmodule":
                self._adv()
            self._match("endmodule")
            mods.append(SVModuleAST(mod_name, self.file_path, params, tuple(ports)))
        return mods


def parse_sv_file(rtl_path: Path) -> List[SVModuleAST]:
    return SVModuleParser(sv_tokenize(rtl_path.read_text(encoding="utf-8")), str(rtl_path)).parse_all()
