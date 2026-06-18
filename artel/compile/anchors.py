import ast
import hashlib
from dataclasses import dataclass, field

PYTHON = "python"
KIND_MODULE = "module"
KIND_FUNCTION = "function"
KIND_CLASS = "class"
KIND_FILE = "file"

_LANG_BY_EXT = {
    ".py": PYTHON,
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".sql": "sql",
}


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Dep:
    kind: str
    name: str


@dataclass
class Unit:
    path: str
    symbol: str
    lang: str
    kind: str
    start_line: int
    end_line: int
    sha: str
    description: str
    deps: list[Dep] = field(default_factory=list)


def lang_of(path: str) -> str:
    for ext, lang in _LANG_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return ""


def compile_source(path: str, source: str) -> list[Unit]:
    if path.endswith(".py"):
        try:
            return _compile_python(path, source)
        except SyntaxError:
            return [_generic_unit(path, source)]
    return [_generic_unit(path, source)]


def _generic_unit(path: str, source: str) -> Unit:
    lines = source.splitlines()
    desc = (
        f"`{path}` — {len(lines)} lines. No compiler frontend for this language, "
        f"so the shape is opaque: trust this node only for existence/size; read the source for behaviour."
    )
    return Unit(
        path=path,
        symbol="",
        lang=lang_of(path),
        kind=KIND_FILE,
        start_line=1,
        end_line=max(1, len(lines)),
        sha=sha_of(source),
        description=desc,
        deps=[],
    )


def _imports(tree: ast.Module) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return sorted(names)


def _top_symbols(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
    return names


def _referenced_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            base = n
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
    return names


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        args = ast.unparse(node.args)
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({args}){ret}"
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    return ""


def _class_members(node: ast.ClassDef) -> tuple[list[str], list[str]]:
    methods: list[str] = []
    attrs: list[str] = []
    for n in node.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    attrs.append(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            attrs.append(n.target.id)
    return methods, attrs


def _decorators(node: ast.AST) -> list[str]:
    decs = getattr(node, "decorator_list", [])
    return [ast.unparse(d) for d in decs]


def _module_description(path: str, imports: list[str], top: list[str]) -> str:
    line = f"`{path}` — Python module. "
    if top:
        line += f"Defines {len(top)} top-level symbol(s): {', '.join(top[:12])}"
        if len(top) > 12:
            line += f", +{len(top) - 12} more"
        line += ". "
    line += (
        f"Relies on {len(imports)} import(s): {', '.join(imports[:12])}."
        if imports
        else "No imports."
    )
    line += " This node carries the module's SHAPE (imports + symbol set); it stays fresh across body-only edits."
    return line


def _symbol_description(path: str, node: ast.AST, sig: str, span_deps: list[Dep]) -> str:
    head = f"`{sig}` at {path}:{node.lineno}"
    decs = _decorators(node)
    parts = [head]
    if decs:
        parts.append("decorated by " + ", ".join("@" + d for d in decs))
    if isinstance(node, ast.ClassDef):
        methods, attrs = _class_members(node)
        if methods:
            parts.append(f"methods: {', '.join(methods[:14])}")
        if attrs:
            parts.append(f"fields: {', '.join(attrs[:14])}")
    imports = [d.name for d in span_deps if d.kind == "import"]
    siblings = [d.name for d in span_deps if d.kind == "symbol"]
    if imports:
        parts.append("uses imports " + ", ".join(imports))
    if siblings:
        parts.append("relies on siblings " + ", ".join(siblings))
    parts.append("grounded to source; trust for shape, check the body for fiddly behaviour")
    return ". ".join(parts) + "."


def _compile_python(path: str, source: str) -> list[Unit]:
    tree = ast.parse(source)
    lines = source.splitlines()
    imports = _imports(tree)
    top = _top_symbols(tree)
    top_set = set(top)
    import_set = set(imports)

    shape = "\n".join(imports) + "\n--\n" + "\n".join(sorted(top))
    units: list[Unit] = [
        Unit(
            path=path,
            symbol="",
            lang=PYTHON,
            kind=KIND_MODULE,
            start_line=1,
            end_line=max(1, len(lines)),
            sha=sha_of(shape),
            description=_module_description(path, imports, top),
            deps=[Dep("import", m) for m in imports],
        )
    ]

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        span = "\n".join(lines[start - 1 : end])
        refs = _referenced_names(node) - {node.name}
        deps = [Dep("import", n) for n in sorted(refs & import_set)]
        deps += [Dep("symbol", n) for n in sorted(refs & top_set - {node.name})]
        sig = _signature(node)
        units.append(
            Unit(
                path=path,
                symbol=node.name,
                lang=PYTHON,
                kind=KIND_CLASS if isinstance(node, ast.ClassDef) else KIND_FUNCTION,
                start_line=start,
                end_line=end,
                sha=sha_of(span),
                description=_symbol_description(path, node, sig, deps),
                deps=deps,
            )
        )
    return units
