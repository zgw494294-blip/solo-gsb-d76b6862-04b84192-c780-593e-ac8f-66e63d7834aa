"""算式的词法与语法分析。

只允许：数字、变量、括号、一元正负号以及二元 + - * /。
禁止：函数调用、隐式乘法、其余任何符号。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .errors import EvalError

_TOKEN_RE = re.compile(
    r"(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<op>[+\-*/()])"
    r"|(?P<space>\s+)"
    r"|(?P<bad>.)"
)


@dataclass
class Token:
    kind: str  # number | ident | op | eof
    text: str
    pos: int


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Num:
    value: float
    pos: int


@dataclass
class Var:
    name: str
    pos: int


@dataclass
class Unary:
    op: str  # '+' | '-'
    operand: object
    pos: int


@dataclass
class Bin:
    op: str  # '+' | '-' | '*' | '/'
    left: object
    right: object
    pos: int  # 运算符位置，用于报错定位


def tokenize(expression: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(expression):
        kind, text, pos = m.lastgroup, m.group(), m.start()
        if kind == "space":
            continue
        if kind == "bad":
            raise EvalError.at_expression(
                "syntax_error", pos, f"算式在位置 {pos} 处有非法字符 {text!r}"
            )
        tokens.append(Token(kind, text, pos))
    tokens.append(Token("eof", "", len(expression)))
    return tokens


class _Parser:
    """expr := term (('+'|'-') term)* ; term := factor (('*'|'/') factor)* ;
    factor := ('+'|'-') factor | primary ;
    primary := 数字 | 变量 | '(' expr ')'"""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._i = 0

    def _peek(self) -> Token:
        return self._tokens[self._i]

    def _next(self) -> Token:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def parse(self):
        node = self._expr()
        tok = self._peek()
        if tok.kind != "eof":
            if tok.kind == "op" and tok.text == ")":
                raise EvalError.at_expression(
                    "syntax_error", tok.pos, f"算式在位置 {tok.pos} 处有多余的右括号"
                )
            raise EvalError.at_expression(
                "syntax_error",
                tok.pos,
                f"算式在位置 {tok.pos} 处缺少运算符；不允许隐式乘法或并列的运算数",
            )
        return node

    def _expr(self):
        node = self._term()
        while (tok := self._peek()).kind == "op" and tok.text in "+-":
            self._next()
            right = self._term()
            node = Bin(tok.text, node, right, tok.pos)
        return node

    def _term(self):
        node = self._factor()
        while (tok := self._peek()).kind == "op" and tok.text in "*/":
            self._next()
            right = self._factor()
            node = Bin(tok.text, node, right, tok.pos)
        return node

    def _factor(self):
        tok = self._peek()
        if tok.kind == "op" and tok.text in "+-":
            self._next()
            return Unary(tok.text, self._factor(), tok.pos)
        return self._primary()

    def _primary(self):
        tok = self._peek()
        if tok.kind == "number":
            self._next()
            value = float(tok.text)
            if not math.isfinite(value):
                raise EvalError.at_expression(
                    "invalid_number", tok.pos, f"位置 {tok.pos} 处的数字 {tok.text!r} 不是有限数值"
                )
            return Num(value, tok.pos)
        if tok.kind == "ident":
            self._next()
            nxt = self._peek()
            if nxt.kind == "op" and nxt.text == "(":
                raise EvalError.at_expression(
                    "syntax_error",
                    nxt.pos,
                    f"位置 {nxt.pos}：不允许函数调用（{tok.text}(...)），算式只支持 + - * /",
                )
            return Var(tok.text, tok.pos)
        if tok.kind == "op" and tok.text == "(":
            self._next()
            node = self._expr()
            closing = self._peek()
            if closing.kind == "op" and closing.text == ")":
                self._next()
                return node
            raise EvalError.at_expression(
                "syntax_error", closing.pos, f"位置 {closing.pos}：缺少右括号"
            )
        if tok.kind == "eof":
            raise EvalError.at_expression(
                "syntax_error", tok.pos, f"位置 {tok.pos}：算式在此处意外结束，缺少运算数"
            )
        raise EvalError.at_expression(
            "syntax_error", tok.pos, f"位置 {tok.pos}：此处不允许出现 {tok.text!r}，缺少运算数"
        )


def parse_expression(expression: str):
    """把算式文本解析为 AST；非法算式抛出带位置的 EvalError。"""
    tokens = tokenize(expression)
    if tokens[0].kind == "eof":
        raise EvalError.at_expression("syntax_error", 0, "算式为空")
    return _Parser(tokens).parse()


def variable_names(node) -> dict[str, int]:
    """收集 AST 中出现的变量名 -> 首次出现位置（0 起字符偏移）。"""
    names: dict[str, int] = {}
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, Var):
            names.setdefault(n.name, n.pos)
        elif isinstance(n, Unary):
            stack.append(n.operand)
        elif isinstance(n, Bin):
            stack.append(n.right)
            stack.append(n.left)
    return names
