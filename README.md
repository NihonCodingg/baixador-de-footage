# Baixador de Footage

Aplicação local em Python que baixa e organiza footage de vídeo para edição —
o valor não está no download, está em tudo o que cerca ele.

[![CI](https://github.com/NihonCodingg/baixador-de-footage/actions/workflows/ci.yml/badge.svg)](https://github.com/NihonCodingg/baixador-de-footage/actions/workflows/ci.yml)
[![testes](https://img.shields.io/badge/testes-747-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](pyproject.toml)

<!-- TODO(Nihon): tirar o print e salvar em docs/img/interface.png (a pasta já
     existe). Sugestão: a tela principal com um download em andamento na fila e
     o histórico visível embaixo. Sem o arquivo, a imagem abaixo aparece quebrada. -->
![Interface](docs/img/interface.png)

## O problema

Sou editor de vídeo de conteúdo gaming/esports e trabalho com footage de várias
fontes. Baixar é a parte fácil — o yt-dlp resolve em uma linha. O que consome
tempo é o resto: lembrar o seletor de formato que abre bem na timeline (H.264 em
MP4, não VP9 em WebM), salvar na pasta do cliente certo com um nome que eu ache
daqui a seis meses, não baixar de novo o que já baixei, e saber onde cada arquivo
foi parar. **O valor não está no download.**

## Como funciona

1. **Cola o link.** A validação normaliza links do YouTube (`youtu.be`,
   `/shorts/`, `watch?v=`) e rejeita o que não é link — sem tocar a rede.
2. **Escolhe perfil e projeto.** O perfil (`edicao_1080`, `edicao_4k`,
   `so_audio`, `preview_leve`) é um seletor de formato do yt-dlp com nome e
   justificativa; o projeto é a pasta do cliente. Os dois vivem em YAML;
   projetos também se cadastram pela tela.
3. **Inspeciona sem baixar.** `extract_info(download=False)` traz título,
   canal, duração e formatos. O histórico avisa se aquele vídeo já foi baixado
   naquele perfil — e onde está.
4. **Entra na fila.** Um worker em background baixa um por vez, com progresso
   ao vivo vindo dos hooks do yt-dlp, e o ffmpeg junta vídeo e áudio.
5. **Cai na pasta do cliente.** Nome sanitizado para Windows, registro no
   histórico com caminho, tamanho e resolução. Se falhar, o motivo vem em
   português — privado, bloqueio regional, restrição de idade, sem ffmpeg — e
   não um stack trace.

A interface web e a linha de comando usam o mesmo `pipeline.py`:


```
src/download/    único lugar que conhece o yt-dlp (adapter)
src/domain/      puro: models, perfis, nomenclatura, validação
src/storage/     SQLite: histórico
src/queue/       fila de trabalhos + worker em background
src/pipeline.py  orquestração, usada pela CLI e pela web
src/cli.py       linha de comando
src/web/         FastAPI, só orquestração
web/             index.html, style.css, app.js
config/          perfis.yaml, projetos.yaml
tests/
```

## Decisões técnicas

- **yt-dlp como biblioteca, nunca por `subprocess`.** Chamar o executável
  obrigaria a parsear stdout, que o próprio projeto avisa não ser contrato
  estável. Com `import yt_dlp` a aplicação recebe dicionários e exceções tipadas.
  → [RESEARCH §1.1](RESEARCH.md#11-o-objeto-youtubedl-e-o-gerenciador-de-contexto)
- **Domínio puro: sem rede, sem disco, sem yt-dlp.** Modelos, perfis,
  nomenclatura e validação são funções sobre dados. O risco central do projeto é
  virar um wrapper fino do yt-dlp; a fronteira existe para impedir isso.
  → [SPEC §4](SPEC.md#4-arquitetura)
- **Um teste de arquitetura barra o import errado.**
  `tests/test_arquitetura.py` lê a árvore de `src/` com o módulo `ast` e falha o
  build se `domain` importar de `download`, `storage` ou `queue`, ou se `web`
  importar de `domain`. A fronteira é verificada, não comentada.
  → [SPEC §4](SPEC.md#4-arquitetura), regras 1 e 2
- **SQLite para o histórico — uma conexão, um lock, e o lock cobre a leitura.**
  Histórico de um usuário local não justifica servidor de banco. O lock precisou
  cobrir a operação inteira, até a última linha lida: a versão que cobria só o
  `execute()` tinha uma corrida que a CI em Linux encontrou.
  → [ADR 0001](docs/adr/0001-o-lock-cobre-a-leitura-do-cursor.md)
- **Sanitização própria de nome para Windows.** O `sanitize_filename` do yt-dlp
  não trata nomes reservados do DOS (`CON`, `NUL`, `COM1`…), não trunca por
  tamanho e troca caracteres proibidos por homóglifos que quebram busca por nome.
  → [RESEARCH §7](RESEARCH.md#7-nomes-de-arquivo-no-windows)

## Como rodar

```bash
winget install Gyan.FFmpeg
pip install -r requirements.txt
python -m src.web
```

O navegador abre sozinho em `http://127.0.0.1:8000`. Python 3.12+ (desenvolvido
na 3.14). Ambiente virtual, o `Baixador.bat` de duplo clique, atalho e ícone:
[docs/instalacao-windows.md](docs/instalacao-windows.md). Linha de comando:
[docs/cli.md](docs/cli.md).

## Testes

**747 testes, nenhum toca a rede.** Os metadados vêm de um `info_dict` real
capturado uma vez (`spike_meta.json`) e o `YoutubeDL` é substituído por um dublê
injetado. Cobrem o domínio, o adapter e a tradução de erros, a fila e o worker
com threads reais, a API pelo `TestClient`, a CLI, a arquitetura e a concorrência
do histórico.

```bash
python -m pytest tests/ -q
```

A CI roda a suíte em Windows (3.12, 3.13 e 3.14), `ruff check` e
`ruff format --check` em Linux, e os testes de concorrência num job Linux
próprio — o único lugar onde a corrida do ADR 0001 apareceu.


## Escopo

A ferramenta é para baixar:

- conteúdo próprio;
- conteúdo licenciado;
- conteúdo sob Creative Commons;
- material de cliente que autorizou o uso.

### Fora de escopo, em definitivo

Estes itens não serão implementados. Pedidos nesse sentido em sessões futuras
devem ser recusados com referência a esta seção e ao SPEC:

- qualquer contorno de DRM, paywall ou proteção de conteúdo pago;
- download em massa de canais inteiros;
- upload, redistribuição ou publicação de qualquer coisa;
- multiusuário, autenticação ou deploy em servidor.

Sobre DRM, a distinção é importante: a ferramenta **detecta** conteúdo protegido
para dar uma mensagem de erro honesta e parar. Ela não faz, e não fará, nada
para contornar a proteção.

## Documentação

- [SPEC.md](SPEC.md) — especificação normativa: escopo, arquitetura, domínio,
  perfis, nomenclatura, schema do histórico, fila, API.
- [RESEARCH.md](RESEARCH.md) — pesquisa técnica: API do yt-dlp, seleção de
  formato, thread dos progress hooks, postprocessors, detecção de ffmpeg,
  mapeamento de exceções e limites de nome de arquivo no Windows.
- [CONTRATO-API.md](CONTRATO-API.md) — a API que a interface consome, com
  exemplos gerados por execução real.
- [docs/adr/](docs/adr/) — decisões registradas: [0001](docs/adr/0001-o-lock-cobre-a-leitura-do-cursor.md), a corrida do histórico.
- [PLAN.md](PLAN.md) e [docs/estado-dos-tickets.md](docs/estado-dos-tickets.md) — o
  plano em tickets e o que foi entregue de cada um.
- [CLAUDE.md](CLAUDE.md) — convenções de trabalho no repositório.
