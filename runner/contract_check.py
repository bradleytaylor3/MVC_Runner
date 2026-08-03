"""Generic, declarative replacement for bench's old per-fixture check.py
files: each fixture task carries a `contract` dict (a UML-flavored
operation signature -- stereotype/annotation, name, params, return type --
plus, for executable languages, behavioral test cases) instead of a
hand-written Python function. One interpreter per language reads the
contract and decides whether a generated fragment satisfies it.

The point of doing this declaratively rather than per-task code: it lets
the checker ignore spelling on names that don't matter (parameter names,
locals -- checked by type/role, or not checked at all) while still holding
exact spelling to account where it's load-bearing (the operation name, a
constant other code might reference, a SQL table/column) -- without
needing an abbreviation/synonym table, which can't be made exhaustive.
"""

import math
import re
import subprocess
import sys
import textwrap

TIMEOUT_SECONDS = 5


def _run_subprocess(script: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SECONDS}s"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:300]
    return True, proc.stdout.strip()


def _check_python(fragment: str, contract: dict) -> tuple[bool, str]:
    """contract: {kind: "function"|"constant", name, ...}
    function: {cases: [{args: [...], expect: number, rel_tol?: float}]}
    constant: {expect: number, rel_tol?: float}
    Runs in a subprocess (not exec() in-process) -- the fragment is
    untrusted model output; a timeout at least bounds a hang, though this
    doesn't sandbox filesystem/network access."""
    kind = contract.get("kind")
    name = contract["name"]

    if kind == "constant":
        expect = contract["expect"]
        rel_tol = contract.get("rel_tol", 1e-4)
        script = fragment + "\n\n" + textwrap.dedent(f"""
            import math
            assert isinstance({name}, (int, float)), "{name} is not a number"
            assert math.isclose({name}, {expect!r}, rel_tol={rel_tol!r}), f"{name} = {{{name}}}, expected ~{expect!r}"
            print(f"{name} = {{{name}}}")
        """)
        return _run_subprocess(script)

    if kind == "function":
        cases = contract.get("cases", [])
        if not cases:
            return False, "contract has no test cases to run"
        checks = []
        for case in cases:
            args_src = ", ".join(repr(a) for a in case["args"])
            expect = case["expect"]
            rel_tol = case.get("rel_tol", 1e-4)
            checks.append(textwrap.dedent(f"""
                result = {name}({args_src})
                assert math.isclose(result, {expect!r}, rel_tol={rel_tol!r}), \\
                    f"{name}({args_src}) = {{result}}, expected ~{expect!r}"
                print(f"{name}({args_src}) = {{result}}")
            """))
        script = fragment + "\n\nimport math\n" + "\n".join(checks)
        return _run_subprocess(script)

    return False, f"unknown contract kind: {kind!r}"


def _check_kotlin(fragment: str, contract: dict) -> tuple[bool, str]:
    """contract: {stereotype: "Insert"|"Delete"|"Update"|"Query", name,
    params: [{type}], return_type, suspend?: bool,
    sql?: {verb, table?, aggregate?, where_param?}} (sql only for Query)."""
    stereotype = contract["stereotype"]

    if stereotype == "Query":
        m = re.search(r'@Query\("([^"]+)"\)', fragment)
        if not m:
            return False, "missing @Query annotation"
        sql = m.group(1).upper()
        sql_contract = contract.get("sql", {})

        verb = sql_contract.get("verb")
        if verb and not sql.strip().startswith(verb.upper()):
            return False, f"SQL doesn't start with {verb}: {sql}"

        table = sql_contract.get("table")
        if table and table.upper() not in sql.replace("`", ""):
            return False, f"SQL doesn't reference table {table}: {sql}"

        aggregate = sql_contract.get("aggregate")
        if aggregate and f"{aggregate.upper()}(" not in sql:
            return False, f"SQL doesn't aggregate with {aggregate}: {sql}"

        where_param = sql_contract.get("where_param")
        if where_param and f":{where_param.upper()}" not in sql:
            return False, f"SQL doesn't filter by :{where_param}: {sql}"
    else:
        if f"@{stereotype}" not in fragment:
            return False, f"missing @{stereotype} annotation"

    name = contract["name"]
    fun_prefix = r"suspend\s+fun" if contract.get("suspend") else r"fun"
    params = contract.get("params", [])
    param_pattern = r"\s*,\s*".join(r"\w+\s*:\s*" + re.escape(p["type"]) for p in params)
    return_type = contract.get("return_type")
    return_pattern = r"\s*:\s*" + re.escape(return_type) if return_type else ""
    sig_re = rf"{fun_prefix}\s+{re.escape(name)}\s*\(\s*{param_pattern}\s*\){return_pattern}"
    if not re.search(sig_re, fragment):
        return False, f"signature doesn't match expected shape: /{sig_re}/"

    return True, "contract satisfied"


CHECKERS_BY_LANGUAGE = {
    "python": _check_python,
    "kotlin": _check_kotlin,
}


def check_contract(language: str, fragment: str, contract: dict) -> tuple[bool, str]:
    checker = CHECKERS_BY_LANGUAGE.get(language)
    if checker is None:
        return False, f"no generic checker registered for language {language!r}"
    return checker(fragment, contract)
