# Linha de comando

Movido do README em 10/09/2026. A CLI e a interface web usam o mesmo `pipeline.py`.


```bash
python -m src.cli --perfil edicao_1080 --projeto cliente_x URL [URL...]
```

A CLI e a interface web usam o mesmo `pipeline.py` — nenhuma regra é escrita
duas vezes. Outras opções:

| Comando | Para quê |
|---|---|
| `--dry-run` | Mostra o que seria baixado e **para onde**, sem baixar. É como conferir a nomenclatura antes de comprometer disco |
| `--perfis` | Lista os perfis do YAML, com o teto de qualidade e a extensão de cada um |
| `--projetos` | Lista os projetos, a pasta de destino e o motivo de um inválido |
| `--historico [TERMO]` | Consulta o histórico; `TERMO` busca no título ignorando acento |
| `--forcar` | Baixa de novo um vídeo já concluído naquele perfil |

Ao final de um download a CLI imprime quantos foram baixados, quantos **já
existiam** no destino e quantas falhas houve, com o caminho de cada arquivo.
Sai com código 0 se tudo deu certo e 1 se qualquer coisa falhou.
