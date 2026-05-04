"""Auto-diagnóstico e fix das dependências do módulo Text-to-SQL Deep Agent.

Uso:
    python scripts/fix_text2sql.py              # só diagnóstico
    python scripts/fix_text2sql.py --fix        # diagnóstico + reinstalação forçada

Garante que o ambiente tem as versões corretas de:
    deepagents, langchain-openai, langchain-community,
    langgraph, langgraph-prebuilt, sqlalchemy

E que estão na MESMA venv que está rodando este script.
"""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version


# (pacote_pip, módulo_importável, versão_mínima)
DEPS = [
    ("deepagents",          "deepagents",                         "0.4.0"),
    ("langchain-openai",    "langchain_openai",                   "1.0.0"),
    ("langchain-community", "langchain_community.utilities",      "0.4.0"),
    ("langchain-community", "langchain_community.agent_toolkits", "0.4.0"),
    ("langgraph",           "langgraph",                          "1.0.0"),
    ("langgraph-prebuilt",  "langgraph.prebuilt.tool_node",       "1.0.0"),
    ("sqlalchemy",          "sqlalchemy",                         "2.0.0"),
]


def _v(pkg: str) -> str | None:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def _can_import(module: str) -> tuple[bool, str | None]:
    try:
        __import__(module, fromlist=["_"])
        return True, None
    except ImportError as e:
        return False, str(e)


def diagnose() -> bool:
    """Imprime relatório de cada dep. Retorna True se tudo OK."""
    print("=" * 78)
    print("DIAGNÓSTICO — Text-to-SQL Deep Agent (Vértice)")
    print("=" * 78)
    print(f"Python:    {sys.executable}")
    print(f"venv:      {sys.prefix}")
    print(f"cwd:       {os.getcwd()}")
    print()
    print(f"{'PACOTE':<22} {'INSTALADO':<14} {'MÍNIMA':<10} {'IMPORT':<8} DETALHE")
    print("-" * 78)

    all_ok = True
    seen: dict[str, tuple[str | None, bool]] = {}
    for pkg, mod, min_ver in DEPS:
        if pkg not in seen:
            seen[pkg] = (_v(pkg), True)
        installed, _ = seen[pkg]
        ok, err = _can_import(mod)
        status = "OK" if ok else "FAIL"
        ver_disp = installed or "AUSENTE"
        detail = "" if ok else (err or "")[:30]
        print(f"{pkg:<22} {ver_disp:<14} >={min_ver:<8} {status:<8} {detail}")
        if not ok or installed is None:
            all_ok = False

    print()
    if all_ok:
        print("✓ Todas as dependências OK. O módulo SQL deve funcionar.")
    else:
        print("✗ Dependências inválidas. Rode com --fix para reinstalar.")
    print("=" * 78)
    return all_ok


def fix() -> int:
    """Reinstala forçadamente as deps no python ATUAL."""
    pkgs = sorted({pkg for pkg, _, _ in DEPS})
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--upgrade", "--force-reinstall", "--no-cache-dir",
    ] + pkgs
    print(f"\n→ Executando: {' '.join(cmd)}\n")
    return subprocess.call(cmd)


def main():
    args = sys.argv[1:]
    if "--fix" in args:
        rc = fix()
        if rc != 0:
            print(f"\n✗ pip install retornou código {rc}")
            sys.exit(rc)
        print("\n→ Re-validando após reinstalação...\n")

    ok = diagnose()
    if not ok and "--fix" not in args:
        print("\nDica: rode `python scripts/fix_text2sql.py --fix` para reinstalar.")
        print("Após reinstalar, REINICIE o uvicorn (Ctrl+C e suba de novo).")
        sys.exit(1)
    if not ok:
        print("\n✗ Mesmo após reinstalação algo não importa.")
        print("Verifique se está na venv correta — o caminho mostrado em")
        print("'venv:' acima precisa ser o mesmo que serve o uvicorn.")
        sys.exit(2)


if __name__ == "__main__":
    main()
