"""`docs/troubleshooting.md` must quote every user-facing failure message.

The catalogue is built by hand from the same call sites
`docs/documentation-plan.md`'s troubleshooting prompt greps for:
``ConfigError(``, ``RenderError(``, ``HomeAssistantError(``, ``RuntimeError(``,
``DeliveryResult.failure(`` and ``log.warning(``/``log.error(``. Nothing stops
someone changing or removing a message except this test, so it re-parses the
source with :mod:`ast`, reconstructs each message's literal text — including
its ``{placeholder}`` and ``%s`` markers, unevaluated — and asserts the first
thirty characters of every one appear verbatim in the page.

A call site is skipped only if its first argument is not a reconstructable
string literal (for example ``log.warning(outcome.describe())``, where there
is nothing static to check), or if it is named in ``ALLOW_LIST`` because the
message is purely internal and never reaches a user.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "troubleshooting.md"

#: Every file the documentation-plan grep covers.
SOURCE_FILES = [
    "src/maverick/cli.py",
    "src/maverick/config.py",
    "src/maverick/ha/client.py",
    "src/maverick/render/browser.py",
    "src/maverick/render/dashboard.py",
    "src/maverick/scheduling/scheduler.py",
    "src/maverick/transports/opendisplay.py",
    "src/maverick/transports/mqtt.py",
    "src/maverick/transports/pull.py",
    "src/maverick/engine.py",
    "src/maverick/app.py",
]

#: Names that raise/return a user-facing message when called.
_ERROR_CLASS_NAMES = {"ConfigError", "RenderError", "HomeAssistantError", "RuntimeError"}

#: (relative file path, line number of the call) -> reason it is exempt.
#: Keep this list short: it is for messages that cannot reach a user through
#: any current call path, not for messages that are merely inconvenient to
#: document.
ALLOW_LIST: dict[tuple[str, int], str] = {
    (
        "src/maverick/transports/mqtt.py",
        119,
    ): (
        "guards MqttPublisher.subscribe() being called before start() — an "
        "internal ordering invariant the engine always satisfies, not "
        "something reachable through configuration or normal use."
    ),
}

_CONVERSIONS = {-1: "", 115: "!s", 114: "!r", 97: "!a"}


def render_string_expr(node: ast.AST) -> str | None:
    """Reconstruct the literal, unevaluated text of a string-producing expression.

    A plain string constant is returned as-is. An f-string (``JoinedStr``) is
    rebuilt with each interpolation shown as ``{expr}`` — the expression's own
    source, not its runtime value — exactly as the source spells it. A ``+``
    concatenation (``"a " + fn() + "b"``) returns its left-most literal, which
    is always where these messages' static, human-authored text lives.

    Returns ``None`` when the node has no reconstructable literal text at all
    (a bare call, a name, ...).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                expr_src = ast.unparse(value.value)
                conv = _CONVERSIONS[value.conversion]
                spec = ""
                if value.format_spec is not None:
                    rendered_spec = render_string_expr(value.format_spec)
                    spec = ":" + (rendered_spec or "")
                parts.append("{" + expr_src + conv + spec + "}")
            else:  # pragma: no cover - JoinedStr only ever holds the two above
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return render_string_expr(node.left)
    return None


def _is_target_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _ERROR_CLASS_NAMES
    if isinstance(func, ast.Attribute):
        if func.attr == "failure" and isinstance(func.value, ast.Name):
            return func.value.id == "DeliveryResult"
        if func.attr in ("warning", "error") and isinstance(func.value, ast.Name):
            return func.value.id == "log"
    return False


def extract_messages() -> list[tuple[str, int, str]]:
    """Every (file, line, message) triple from a matching call site's first argument.

    Sites whose first argument is not a reconstructable literal are omitted
    entirely, matching the acceptance criteria's "extracts the string
    literals at those call sites" — there is nothing to extract from
    ``log.warning(outcome.describe())``.
    """
    found: list[tuple[str, int, str]] = []
    for relative in SOURCE_FILES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_target_call(node)):
                continue
            if not node.args:
                continue
            message = render_string_expr(node.args[0])
            if message is None:
                continue
            found.append((relative, node.lineno, message))
    return found


def test_extractor_finds_the_expected_call_sites() -> None:
    """Sanity check on the extractor itself, so a refactor that silently stops
    matching real call sites fails loudly here rather than passing by having
    nothing left to check."""
    messages = extract_messages()
    assert len(messages) >= 55, (
        f"only found {len(messages)} message call sites; expected roughly 58 "
        "(13 ConfigError, 6 HomeAssistantError, 5 RenderError, 3 RuntimeError, "
        "18 DeliveryResult.failure, 13 log.warning/log.error). If a file moved "
        "or a call site's shape changed, update SOURCE_FILES or the extractor."
    )
    by_class: dict[str, int] = {}
    for relative, _lineno, _message in messages:
        by_class[relative] = by_class.get(relative, 0) + 1
    assert by_class, "no source files produced any messages"


@pytest.mark.parametrize(
    "relative,lineno,message",
    extract_messages(),
    ids=lambda v: f"{v}" if isinstance(v, str) else str(v),
)
def test_every_message_is_documented(relative: str, lineno: int, message: str) -> None:
    if (relative, lineno) in ALLOW_LIST:
        pytest.skip(ALLOW_LIST[(relative, lineno)])
    document = DOC.read_text(encoding="utf-8")
    needle = message[:30]
    assert needle in document, (
        f"{relative}:{lineno} raises/logs a message not quoted in "
        f"docs/troubleshooting.md (first 30 chars): {needle!r}\n"
        f"full message: {message!r}\n"
        "Either add it to the catalogue, or — if it is purely internal and "
        "never reaches a user — add (relative_path, lineno) to ALLOW_LIST "
        "in this test with a reason."
    )


def test_allow_list_entries_still_exist() -> None:
    """An allow-list entry for a call site that moved or was deleted is stale."""
    found = {(relative, lineno) for relative, lineno, _message in extract_messages()}
    stale = sorted(set(ALLOW_LIST) - found)
    assert not stale, (
        f"ALLOW_LIST names call sites that no longer exist: {stale}. "
        "Update the (file, line) pair or remove the entry."
    )
