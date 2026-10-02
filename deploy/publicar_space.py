"""
Publica o app no Hugging Face Spaces.

Por que um script, e não um "git push" direto deste repositório?
O Space exige um README com um cabeçalho de configuração (sdk, porta...)
que ficaria estranho no GitHub. Então mantemos uma cópia separada (um clone
do repositório do Space) e este script copia para lá SÓ o que o app precisa,
sem testes, sem .env e sem nada pessoal.

Uso (uma vez):
    git clone https://huggingface.co/spaces/<usuario>/<space> ../achadinhos-space
Depois, a cada atualização:
    python deploy/publicar_space.py ../achadinhos-space
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Exatamente o que o Dockerfile usa, e nada mais.
ARQUIVOS = ["Dockerfile", "requirements.txt", "pipeline.py", "servidor.py", "main.py"]
PASTAS = ["services", "web"]
README_SPACE = RAIZ / "deploy" / "huggingface" / "README.md"


def git(destino: Path, *argumentos: str) -> None:
    subprocess.run(["git", "-C", str(destino), *argumentos], check=True)


def copiar(destino: Path) -> None:
    """Deixa o clone do Space idêntico ao que o app precisa."""
    # Apaga o conteúdo antigo (menos o .git), para arquivos removidos aqui
    # também sumirem de lá.
    for item in destino.iterdir():
        if item.name in (".git", ".gitattributes"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    for nome in ARQUIVOS:
        shutil.copy2(RAIZ / nome, destino / nome)
    for nome in PASTAS:
        shutil.copytree(RAIZ / nome, destino / nome,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(README_SPACE, destino / "README.md")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    destino = Path(sys.argv[1]).resolve()
    if not (destino / ".git").is_dir():
        print(f"[ERRO] {destino} não é um clone do Space (não tem .git).")
        return 1

    copiar(destino)
    git(destino, "add", "-A")
    pendente = subprocess.run(["git", "-C", str(destino), "diff", "--cached", "--quiet"])
    if pendente.returncode == 0:
        print("Nada mudou desde a última publicação.")
        return 0

    commit = subprocess.run(["git", "-C", str(RAIZ), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    git(destino, "commit", "-m", f"Publica a versão {commit} do GitHub")
    git(destino, "push")
    print("✓ Publicado. Acompanhe o build na aba 'Logs' do Space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
