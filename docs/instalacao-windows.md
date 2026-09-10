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

1. Clique com o botão direito em `Baixador.bat` → **Mostrar mais opções** (no
   Windows 11) → **Enviar para** → **Área de trabalho (criar atalho)**.
2. O atalho pode ser renomeado à vontade; ele guarda o caminho do `.bat`.

Arrastar com o **botão direito** para a área de trabalho e escolher *Criar
atalhos aqui* dá no mesmo. Não arraste com o botão esquerdo: isso **move** o
arquivo para fora do repositório.

### Trocar o ícone

Botão direito no atalho → **Propriedades** → aba **Atalho** → **Alterar
ícone** → **Procurar**.

- O Windows aceita `.ico` (ou `.exe`/`.dll` que contenham ícones). **PNG e JPG
  não servem** — converta antes.
- Sem nenhum arquivo à mão, `%SystemRoot%\System32\imageres.dll` e
  `shell32.dll` trazem centenas de ícones prontos.
- Guarde o `.ico` fora do repositório, ou o atalho quebra se a pasta mudar.

Na mesma aba, **Executar: Minimizada** deixa até o piscar inicial da janela
fora da tela.

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
