#!/usr/bin/env python3
"""Corrige formatacao basica e valida os YAML do projeto."""

from pathlib import Path
import subprocess
import sys

try:
    from yamllint import linter
    from yamllint.config import YamlLintConfig
except ImportError:
    print("Instale a dependencia:")
    print(".venv/bin/python -m pip install yamllint==1.35.1")
    sys.exit(2)


def main():
    root = Path(__file__).resolve().parents[1]

    # Inclui arquivos versionados e novos, respeitando o .gitignore.
    result = subprocess.run(
        [
            "git", "ls-files", "-z",
            "--cached", "--others", "--exclude-standard",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )

    paths = sorted({
        name
        for name in result.stdout.decode().split("\0")
        if name.lower().endswith((".yaml", ".yml"))
    })

    config_path = root / ".yamllint.yaml"
    if not config_path.is_file():
        print("ERRO: .yamllint.yaml nao encontrado na raiz.")
        return 2

    config = YamlLintConfig(file=str(config_path))
    corrected_count = 0
    checked_count = 0
    error_count = 0
    warning_count = 0

    for name in paths:
        path = root / name
        if not path.is_file():
            continue

        try:
            original = path.read_bytes()
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            print(f"ERRO: {name}: arquivo nao esta em UTF-8.")
            error_count += 1
            continue

        # Normaliza finais de linha e remove espacos/tabs finais.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip(" \t") for line in text.split("\n")]
        corrected = "\n".join(lines).rstrip("\n") + "\n"

        if corrected.encode("utf-8") != original:
            path.write_bytes(corrected.encode("utf-8"))
            corrected_count += 1
            print(f"CORRIGIDO: {name}")

        checked_count += 1

        for problem in linter.run(corrected, config, filepath=name):
            print(
                f"{name}:{problem.line}:{problem.column}: "
                f"{problem.level}: {problem.desc} "
                f"({problem.rule})"
            )
            if problem.level == "error":
                error_count += 1
            else:
                warning_count += 1

    print(
        f"\nVerificados: {checked_count} | "
        f"Corrigidos: {corrected_count} | "
        f"Erros: {error_count} | "
        f"Avisos: {warning_count}"
    )

    if error_count:
        print("Corrija os erros indicados e execute novamente.")
        return 1

    print("YAML validado. Revise o git diff antes de adicionar ao commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())