"""Seletor NATIVO de pasta. Roda como processo separado, nunca no servidor.

    python -m src.seletor_pasta

Imprime o caminho escolhido em stdout, ou nada se o usuário cancelar.

Por que um processo só para isto, medido nesta máquina:

- o Tk **não é thread-safe**, e os handlers do FastAPI rodam num threadpool;
  criar a janela ali funciona no Windows e é exatamente o tipo de coisa que
  quebra depois;
- um diálogo esquecido aberto penduraria a requisição para sempre — em
  processo separado, o `timeout` do subprocess resolve;
- um erro do Tk derruba este processo, não o servidor.

Custo medido: ~190 ms para subir o processo, contra 654 ms para criar um Tk
dentro de uma thread do servidor.

Ticket: gerenciamento de projetos pela interface.
"""

import sys


def main() -> int:
    # O caminho pode ter acento; o console do Windows é cp1252 (RESEARCH 7.4).
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as erro:  # python sem tk
        print(f"tkinter indisponível: {erro}", file=sys.stderr)
        return 2

    raiz = tkinter.Tk()
    raiz.withdraw()  # só o diálogo aparece
    # Sem isto o diálogo abre ATRÁS do navegador, e parece que nada aconteceu.
    raiz.attributes("-topmost", True)
    try:
        escolhido = filedialog.askdirectory(
            title="Escolha a pasta de destino do footage", mustexist=True, parent=raiz
        )
    finally:
        raiz.destroy()

    if escolhido:
        print(escolhido)
    return 0


if __name__ == "__main__":
    sys.exit(main())
