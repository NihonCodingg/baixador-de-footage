# Instalação e uso no Windows

Movido do README em 10/09/2026. O README mantém só os três comandos para rodar;
o que é específico do Windows — ffmpeg, o `.bat`, atalho e ícone — vive aqui.

## Ambiente virtual

Recomendado, para as dependências não se misturarem com as do sistema:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Sobre o ffmpeg


O ffmpeg **não** é instalado pelo `pip` — é um binário externo que precisa estar
no `PATH`. Ele é obrigatório para todo perfil de edição, porque sites modernos
servem vídeo e áudio como streams separados e juntá-los é trabalho do ffmpeg.

Sem ele, o operador `+` do seletor de formato não funciona e o download fica
limitado aos formatos pré-combinados, tipicamente 720p ou menos. Como o sintoma
é "pedi 1080p e veio 720p", que é difícil de diagnosticar, a aplicação verifica
o ffmpeg na inicialização e mostra um aviso claro na interface — nunca um stack
trace.

No Windows, a instalação mais direta é:

```bash
winget install Gyan.FFmpeg
```


## Abrir sem terminal


`Baixador.bat`, na raiz do projeto, faz o mesmo com um duplo clique:

- entra na pasta do projeto, venha o clique de onde vier;
- usa o `.venv` se existir, senão o `python` do PATH;
- **se já estiver rodando, só abre o navegador** — a pergunta é feita a
  `/api/config`, não à porta, então outro programa ocupando a 8000 não passa
  por Baixador;
- reabre a própria janela **minimizada**, para não ficar na frente do
  navegador. Ela continua na barra de tarefas: é por ela que se para o
  servidor;
- **se der erro, a janela fica aberta** com a mensagem. Nada de console
  piscando e sumindo.

### Atalho na área de trabalho

Um comando cria o atalho **Baixador** na área de trabalho, já com o ícone
próprio e abrindo minimizado:

```bash
python scripts/criar_atalho.py
```

O ícone é a seta de download no verde da tela, desenhado pelo próprio script
(sem dependência nova) e gravado em `data/baixador.ico`, que o Git ignora.
Rodar de novo regenera tudo. Um atalho antigo feito à mão, como o "Baixador -
Atalho", não é apagado — pode removê-lo você mesmo.

Se o atalho mudar de lugar ou o projeto mudar de pasta, rode o script de novo.

### Abre como programa, não como aba

O Baixador abre numa **janela própria**, sem barra de endereço e sem abas,
com o ícone dele na barra de tarefas: é o modo app do Edge (`--app`), que vem
com todo Windows 10 e 11. Funciona tanto ao subir quanto no segundo clique,
quando ele já estava rodando. Sem o Edge, abre numa aba do navegador padrão.

Fechar a janela **não** desliga o servidor; ele continua na barra de tarefas,
minimizado. Para desligar, feche aquela janela do console.

> O `.bat` chama a si mesmo com `--rodando` para reabrir minimizado. É um
> detalhe interno; não use esse argumento à mão.

Linha de comando:

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
