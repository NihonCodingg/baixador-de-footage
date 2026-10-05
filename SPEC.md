# SPEC.md — Baixador de Footage

Especificação funcional e técnica. Documento normativo: quando o código e o
SPEC discordarem, um dos dois está errado e a discussão começa aqui.

Base factual: [RESEARCH.md](RESEARCH.md). Onde este documento afirma algo sobre
o comportamento do yt-dlp, a evidência está lá.

---

## 1. Produto

### 1.1 O que é

Aplicação local com interface web para baixar footage de vídeo destinado a
edição, construída sobre o yt-dlp usado como **biblioteca Python**.

Usuário: uma pessoa, na própria máquina, sozinha. Não é serviço, não tem
autenticação, não é exposto na rede.

### 1.2 O problema, declarado com precisão

O yt-dlp já baixa vídeo. O download não é o produto.

O que custa tempo é a camada em volta: traduzir "quero 1080p que abra bem no
Premiere" para um seletor de formato; salvar na pasta do cliente certo; nomear
de um jeito que ainda faça sentido em seis meses; não rebaixar o que já foi
baixado; e saber onde cada arquivo foi parar.

**Critério de corte:** se uma funcionalidade é resolvida por uma opção do yt-dlp
sem nenhuma lógica própria, ela não é o produto. Ela pode existir como
conveniência, mas não justifica código no domínio.

### 1.3 Fluxo de uso

1. Abrir a ferramenta no navegador
2. Colar um ou vários links
3. A tela mostra na hora: thumbnail, título, canal, duração e qualidades
   disponíveis — **sem baixar nada**
4. Escolher perfil de qualidade e projeto de destino
5. Mandar para a fila
6. Acompanhar o progresso enquanto continua trabalhando
7. Consultar o histórico depois e saber onde cada arquivo está

### 1.4 Fronteiras

| | |
|---|---|
| **Entrada** | link(s) + perfil + projeto |
| **Saída** | arquivo no disco + registro no histórico |

---

## 2. Escopo

### 2.1 Escopo de uso declarado

A ferramenta destina-se a baixar conteúdo próprio, licenciado, sob Creative
Commons, e material de cliente que autorizou o uso.

### 2.2 Fora de escopo, em definitivo

Os itens abaixo **não serão implementados**. Pedidos nesse sentido, em qualquer
sessão futura, devem ser recusados com referência a esta seção.

| Item | Observação |
|---|---|
| Contorno de DRM, paywall ou proteção de conteúdo pago | Ver 2.3 |
| Download em massa de canais inteiros | A validação de link rejeita URL de canal/playlist (§5.3) |
| Upload, redistribuição ou publicação | A ferramenta escreve em disco local e nada mais |
| Multiusuário, autenticação, deploy em servidor | Vincula-se em `127.0.0.1` |

### 2.3 A posição sobre DRM

A distinção importa e é deliberada.

O yt-dlp **detecta** conteúdo protegido e levanta erro com a mensagem
`"This video is DRM protected"` (RESEARCH §6.3). Esta ferramenta **propaga essa
detecção** e a traduz para uma mensagem honesta em português, encerrando o job.

Detectar e parar é o comportamento correto de um cliente bem-comportado. Não há,
e não haverá, código que tente contornar a proteção.

---

## 3. Restrições técnicas

| # | Restrição | Consequência de projeto |
|---|---|---|
| 1 | yt-dlp como **biblioteca** (`import yt_dlp`), nunca por `subprocess` | O próprio README do yt-dlp avisa que stdout não é contrato estável |
| 2 | ffmpeg é dependência externa | Verificar na inicialização; aviso claro na interface, nunca stack trace |
| 3 | Nada de rede, disco ou yt-dlp na camada de domínio | Garantido por teste de arquitetura (§4.3) |
| 4 | **Um download por vez**, sem paralelismo entre jobs | Uma thread de trabalho; banda é uma só |
| 5 | Roda em `127.0.0.1` | Sem exposição na rede |

> **Nota sobre a restrição 4.** Ela vale para **jobs**. Ela não impede o yt-dlp de
> usar threads internamente **dentro** de um job — e ele usa: o caminho DASH
> multi-formato dispara `ThreadPoolExecutor` sem configuração especial
> (RESEARCH §3.4, caso 4). São coisas diferentes. A fila serializa jobs; o
> estado precisa de lock mesmo assim.

---

## 4. Arquitetura

### 4.1 Camadas

```
src/download/    único lugar que conhece o yt-dlp (adapter)
src/domain/      PURO: models, perfis, nomenclatura, validação
src/storage/     SQLite: histórico
src/queue/       fila de trabalhos + worker em background
src/pipeline.py  orquestração, usada pela CLI E pela web
src/cli.py       linha de comando
src/web/         FastAPI, só orquestração
web/             index.html + style.css + app.js (vanilla)
config/          perfis.yaml e projetos.yaml
tests/
```

### 4.2 Regras duras

**REGRA 1** — `src/domain/` nunca importa de `src/download/`, `src/storage/` ou
`src/queue/`.

**REGRA 2** — `src/web/` nunca importa de `src/domain/`. Ele fala com
`src/pipeline.py`.

### 4.3 Garantia

`tests/test_arquitetura.py` percorre a árvore de `src/` com o módulo `ast` e
falha se qualquer import violar as regras. É um teste de verdade, não um
comentário: quebra o build.

### 4.4 Direção das dependências

```
        cli.py ─┐
                ├──> pipeline.py ──┬──> domain/    (puro)
        web/  ──┘                  ├──> download/  ──> yt_dlp
                                   ├──> storage/   ──> sqlite3
                                   └──> queue/     ──> threading
```

`domain/` é folha: não depende de ninguém dentro de `src/`.

---

## 5. O domínio — o que vive lá e por quê

Esta é a seção mais importante do documento.

**O risco declarado do projeto:** como o yt-dlp faz o trabalho pesado, existe o
perigo de `src/domain/` ficar vazia e tudo virar wrapper. Cada item abaixo
precisa justificar por que é lógica **minha** e não do yt-dlp.

O critério é: *o yt-dlp resolve isso? Se sim, não é meu. Se resolve
parcialmente, o que falta é meu.*

### 5.1 Nomenclatura e sanitização de arquivo

**Vive no domínio. Justificativa: a mais forte de todas.**

O yt-dlp tem `sanitize_filename`. Ele **não** resolve o problema. Medições em
RESEARCH §7.4, na máquina de destino:

| O que falta | Evidência |
|---|---|
| Nomes reservados do DOS (`CON`, `NUL`, `AUX`, `COM1`…) | `sanitize_filename('NUL')` → `'NUL'`. Nenhum tratamento no pacote inteiro. |
| Truncamento por tamanho | `'A'*300` sai com 300 chars. NTFS rejeita acima de 255. |
| Nome vazio | `sanitize_filename('')` → `''` |
| Ponto ou espaço final | `'Final da season.'` preservado; o shell do Windows não suporta |
| Conhecimento do diretório de destino | Não faz ideia do comprimento do caminho do projeto |
| Colisão com arquivo existente | Fora do escopo dele |

E o que ele **faz** é ativamente indesejado para este caso de uso: substitui
caracteres proibidos por homóglifos Unicode de largura total (`:` → `U+FF1A`
`：`). O arquivo é válido, mas o nome não pode ser digitado no teclado, não é
encontrado por busca com os caracteres normais, e quebra o console cp1252 do
Windows com `UnicodeEncodeError`.

**O achado decisivo** (RESEARCH §7.3): escrever num arquivo chamado `NUL` na
máquina de destino **não levanta erro**, `exists()` retorna `True`, e os bytes
desaparecem. Um título que sanitize para `NUL` produziria job "concluído",
histórico gravado e arquivo inexistente.

Sanitização, aqui, é **questão de correção**, não de estilo.

O objetivo do domínio é diferente do objetivo do yt-dlp: gerar um nome que um
**editor de vídeo** consiga achar, digitar e abrir no Premiere, seis meses
depois, num HD externo.

### 5.2 Resolução de perfil YAML → seletor de formato

**Vive no domínio.** O yt-dlp aceita um seletor; ele não tem conceito de perfil
nomeado, não valida se o perfil existe, não sabe que `,` (múltiplos formatos)
é proibido neste produto porque quebra a premissa de um arquivo por job, e não
sabe que um `key` de postprocessor inválido só falha em runtime com `KeyError`
(RESEARCH §4).

A tradução "nome legível → configuração validada" é o produto.

### 5.3 Validação e normalização de link

**Vive no domínio.** Três coisas que o yt-dlp não faz por nós:

1. **Rejeitar canal/playlist.** `extract_info` percorre a lista inteira
   (RESEARCH §1.2). Download em massa está fora de escopo (§2.2), então a
   recusa tem que ser anterior à chamada.
2. **Normalizar** para forma canônica, de modo que `youtu.be/X`,
   `youtube.com/watch?v=X` e a versão com `&t=42` sejam o mesmo vídeo — sem
   isso, o histórico registra duplicatas.
3. **Deduplicar** a lista colada pelo usuário.

#### Escopo da normalização, e a limitação que ele cria

A normalização conhece **apenas o YouTube**. O comportamento é de três vias:

| Entrada | Tratamento |
|---|---|
| URL do YouTube | Normaliza, deduplica, casa com o histórico |
| URL de outro site suportado pelo yt-dlp | **Passa adiante sem normalizar**, com aviso |
| Texto que não é URL nenhuma | `LinkInvalido` |

Outros sites **não são rejeitados**: o yt-dlp suporta centenas de extractors e
a ferramenta não deve fechar essa porta.

**Limitação conhecida, aceita conscientemente:** para sites que não o YouTube,
a deduplicação do lote e a checagem de "já baixei isso" só funcionam quando a
URL colada é byte a byte idêntica à anterior. Duas formas diferentes da mesma
URL geram dois registros no histórico e dois downloads.

O motivo de não resolver isso genericamente: cada site tem sua própria forma
canônica, e escrever normalizadores para centenas de extractors seria
reimplementar o yt-dlp. O `webpage_url` do info_dict traz a forma canônica de
qualquer site — mas só **depois** da chamada de rede, tarde demais para
deduplicar um lote colado sem custo.

A interface deve deixar o aviso visível no cartão de preview, e não escondê-lo
num log.

### 5.4 Montagem do caminho por projeto e tratamento de colisão

**Vive no domínio.** O `outtmpl` do yt-dlp monta caminhos, mas não conhece o
conceito de "projeto", não valida que o destino existe e é gravável, e sua
política de colisão é sobrescrever ou pular. A política deste produto é
desambiguar preservando os dois arquivos.

### 5.5 Modelos: `Video`, `Job`, `Perfil`, `Projeto`

**Vivem no domínio.** O `info_dict` do yt-dlp tem centenas de campos, é
"dicionário-like" mas não garantidamente `dict` (RESEARCH §1.3), e muda entre
versões. Um modelo próprio, com os campos que o produto usa, é a fronteira
anticorrupção contra essa instabilidade.

### 5.6 Classificação de erro

**A taxonomia vive no domínio; o mapeamento vive em `download/`.**

Divisão deliberada. O *enum* de motivos (`PRIVADO`, `BLOQUEIO_REGIONAL`, …) e as
mensagens em português são regra de produto — decisões sobre o que o usuário lê
e o que vale a pena tentar de novo. A tabela que traduz exceção do yt-dlp para
esse enum precisa conhecer as classes do yt-dlp, e por isso fica no adapter.

Isso importa porque a classificação é **frágil por natureza**: os motivos de
indisponibilidade só são distinguíveis por substring de mensagem, e a mensagem
vem do site, não do yt-dlp (RESEARCH §6.3). Quando o YouTube mudar o texto, a
correção é uma linha de tabela no adapter, e o domínio não é tocado.

### 5.7 Máquina de estados do job

**Vive no domínio.** Quais transições são legais é regra de produto. Em
particular, duas regras que o yt-dlp desconhece:

- só é cancelável um job que ainda não começou;
- um job interrompido por fechamento do programa é `interrompido`, nunca
  `concluido`.

### 5.8 O que NÃO vive no domínio

Registro explícito, para evitar arquitetura por inércia:

| Item | Onde vive | Por quê |
|---|---|---|
| Chamar o yt-dlp | `download/` | É I/O |
| Detectar ffmpeg | `download/` | Toca o sistema de arquivos |
| Baixar thumbnail | Em lugar nenhum | O navegador busca direto do CDN (RESEARCH §8) |
| Calcular velocidade/ETA | `queue/` | Vem pronto do progress hook |
| Escolher o melhor formato | yt-dlp | O seletor faz; nós só o construímos |
| Merge de vídeo e áudio | ffmpeg | Não é nosso problema |

---

## 6. Perfis de qualidade

Definidos em `config/perfis.yaml`. Os quatro iniciais têm justificativa em
RESEARCH §2.2; o `maxima` entrou depois:

| Perfil | Alvo | Container |
|---|---|---|
| `edicao_1080` | 1080p, preferindo H.264 + AAC | mp4 |
| `edicao_4k` | até 2160p, qualquer codec | mkv |
| `premiere_4k` | até 2160p, depois **convertido para ProRes 422 HQ** (§6.5) | mov |
| `maxima` | o melhor que o site tiver, **sem teto** (`bv*+ba/b`) | mkv |
| `so_audio` | só a trilha de áudio, em m4a | m4a |
| `preview_leve` | até 480p, menor arquivo | mp4 |

**Regra que o `edicao_4k` documenta:** ele não filtra por `avc1`, ao contrário do
`edicao_1080`. O YouTube não serve H.264 acima de 1080p; um filtro de codec ali
faria o perfil cair silenciosamente para o fallback, entregando 1080p a quem
pediu 4K.

### 6.1 Esquema de um perfil

```yaml
edicao_1080:
  descricao: "1080p H.264 para timeline"
  limite_dimensao: 1080
  format: "bv*{dim}[vcodec^=avc1]+ba[acodec^=mp4a]/bv*{dim}+ba/b{dim}/b"
  format_sort: ["res:1080", "vcodec:h264", "acodec:aac", "fps"]
  merge_output_format: "mp4"
  postprocessors: []
  exige_ffmpeg: true
```

`{dim}` é resolvido em tempo de execução (§6.3). `limite_dimensao: null`
significa que o perfil não filtra por dimensão — é o caso do `so_audio`.

### 6.2 Validação obrigatória na carga

- `format` presente e não vazio
- `format` **não contém `,`** — múltiplos formatos quebram um-arquivo-por-job
- `merge_output_format` numa lista conhecida
- todo `key` de postprocessor existe em `yt_dlp.postprocessor`
- `exige_ffmpeg: true` + ffmpeg ausente → perfil marcado indisponível na UI

### 6.3 O teto de qualidade vai na MENOR dimensão

**Decisão:** o limite de um perfil é aplicado à menor dimensão do vídeo, e o
campo (`width` ou `height`) é resolvido em tempo de execução pela função pura
`campo_limite()`.

#### O problema

Um filtro fixo `[height<=1080]` pressupõe vídeo horizontal. Num vídeo vertical
(Short), a altura é a dimensão *maior*, então o filtro corta exatamente o que
deveria manter.

Medido no `spike_meta.json` real, um Short com formatos de 144x256 até 1080x1920:

```
pedindo edicao_1080 com [height<=1080]  ->  480x854
o formato 1080 real do vídeo            ->  1080x1920
```

O resultado é pior que a simples perda de resolução: o primeiro ramo do seletor
exige `[height<=1080]` **e** `[vcodec^=avc1]`, e o melhor H.264 abaixo de 1080
de altura é 480x854. Os dois filtros se compõem e a degradação é maior que a
soma das partes.

#### Por que não dá para resolver na sintaxe do yt-dlp

A condição correta é `min(width, height) <= N`, equivalente a
`height <= N OR width <= N`. **A gramática do yt-dlp não tem OR.**

`_build_format_filter` faz `fullmatch` de uma única comparação `chave op valor`;
colchetes justapostos são AND. E o `/` não substitui o OR: ele escolhe o
**primeiro ramo que produzir qualquer resultado**, não o melhor entre os ramos.
Em `bv*[width<=1080]/bv*[height<=1080]`, um vídeo horizontal casa no primeiro
ramo com formatos pequenos (854x480) e o ramo correto nunca é alcançado.
Inverter a ordem apenas espelha o defeito.

Também não existe filtro `res` — `res` só existe no `format_sort`, onde é
calculado pela menor dimensão.

#### Candidatos avaliados

Testados com o motor de seleção real do yt-dlp (`build_format_selector` +
`sort_formats`) contra os 45 formatos do fixture. A variante horizontal é
derivada dos dados reais trocando largura por altura, para as duas orientações
terem os mesmos codecs e bitrates.

| Candidato | Vertical | Horizontal | Veredito |
|---|---|---|---|
| `[height<=1080]` | **480x854** | 1920x1080 | quebrado em vertical |
| `[width<=1080]` | 1080x1920 | **1080x608** | quebrado em horizontal |
| `[w<=1920][h<=1920]` | 1080x1920 | 1920x1080 | funciona, mas embute suposição de 16:9 num número mágico; vídeo quadrado 1920x1920 passa sem aviso |
| sem teto + `format_sort res:1080` | 1080x1920 | 1920x1080 | funciona, mas é teto **suave**: só respeita o limite quando existe algum formato abaixo dele |
| **campo resolvido por orientação** | **1080x1920** | **1920x1080** | **adotado** |

O candidato adotado domina: entrega teto duro **e** correção em qualquer
proporção, sem troca.

#### A regra

```python
def campo_limite(formatos) -> str | None:
    com_dimensao = [f for f in formatos if f.largura and f.altura]
    if not com_dimensao:
        return None
    maior = max(com_dimensao, key=lambda f: f.largura * f.altura)
    return "width" if maior.altura > maior.largura else "height"
```

- A orientação vem do formato de **maior área**, não do primeiro da lista — a
  ordem dos formatos no info_dict não é garantida.
- Formatos sem dimensão (só-áudio, storyboards) são ignorados na decisão.
- Sem nenhum formato com dimensão, devolve `None` e o filtro é **omitido** do
  seletor — não substituído por um padrão.

#### Vídeo quadrado

Largura igual a altura devolve **`height`**.

Nesse caso os dois filtros selecionam exatamente o mesmo conjunto, então a
escolha é convenção e não correção. `height` porque é assim que se descreve
qualidade de vídeo — "1080p" significa 1080 linhas. A comparação é **estrita**:
usa `width` apenas quando `altura > largura`.

#### Regressão travada

`tests/test_perfis.py` fixa o resultado medido: com o fixture real e o perfil
`edicao_1080`, a seleção tem que ser `137+140` em 1080x1920, H.264 + AAC. Um
segundo teste documenta que o filtro antigo entregava 480x854 no mesmo vídeo.

O teste lê o `config/perfis.yaml` de verdade — editar o seletor quebra o teste.
---

## 7. Projetos

`config/projetos.yaml` mapeia nome legível para diretório de destino.

```yaml
projetos:
  cliente_x:
    nome: "Cliente X"
    pasta: "D:/FOOTAGE/cliente_x"
  pessoal:
    nome: "Canal pessoal"
    pasta: "D:/FOOTAGE/pessoal"
```

Validação na carga: a pasta existe ou é criável, e é gravável. Um projeto
inválido aparece desabilitado na interface, com o motivo.

**A validação não cria nada.** Para uma pasta que ainda não existe, a checagem
sobe até o primeiro ancestral existente e verifica se ele é gravável. A pasta
só nasce quando um download precisa dela.

O motivo é concreto: o `projetos.yaml` distribuído tem um projeto de exemplo, e
criar as pastas na subida faria aparecer um `D:/FOOTAGE/cliente_exemplo` vazio
na primeira execução, sem o usuário ter pedido nada.

> **Recomendação forte:** apontar `pasta` para fora do repositório. O
> `.gitignore` cobre as variações comuns, mas um `.mp4` que entra no histórico
> do Git não sai mais sem reescrever o histórico.

---

## 8. Nomenclatura de arquivo

### 8.1 Padrão

```
{data_upload} - {titulo_sanitizado} [{video_id}].{ext}
```

Exemplo: `20250814 - Melhores momentos do major [dQw4w9WgXcQ].mp4`

Racional de cada parte:

- **data primeiro** — ordenação cronológica no Explorer sem depender de metadados
- **título** — é como um editor procura material
- **id entre colchetes** — desambiguação estável e chave de reconciliação com o
  histórico

### 8.2 Regras de sanitização

Ordem de aplicação. **A ordem importa**: truncar antes de tratar ponto final
reintroduziria o problema.

1. Remover caracteres de controle (0–31, 127)

2. Substituir os proibidos por **mapeamento individual**, e não por uma regra
   única. Cada caractere carrega um significado diferente no título, e achatar
   todos em `-` destrói informação:

   | Caractere | Vira | Motivo |
   |---|---|---|
   | `\|` | `" - "` | separador visual; comum em título de gaming |
   | `/` | `"-"` | colado: é intervalo. `2024/2025` → `2024-2025` |
   | `\` | `"-"` | idem |
   | `:` | `" "` | vira espaço; `Round1:Final` → `Round1 Final`, sem colar palavras |
   | `?` `*` `"` `<` `>` | `""` | removidos; não carregam significado |

   **Nunca** substituir por homóglifos Unicode de largura total. É o que o
   yt-dlp faz, e torna o nome não-digitável e não-buscável (RESEARCH §7.4).

3. Colapsar espaços repetidos

4. Remover ponto e espaço **do fim**. Ponto no início é legal no Windows e é
   preservado.

5. Se o nome-base (sem extensão) for **igual** a um nome reservado do DOS,
   case-insensitive → sufixar `_`. Comparação por **igualdade**, nunca por
   prefixo: `CONSOLE`, `CONTRA` e `NULO` não podem ser mutilados.

6. Truncar respeitando o orçamento de caminho (§8.3), preservando a extensão e
   o `[video_id]`.

7. **Fallback para o título `video`** quando o título sanitizado não contém
   nenhum caractere alfanumérico (categoria Unicode `L*` ou `N*`).

   Esta regra única cobre quatro casos de uma vez: título vazio, só espaços, só
   pontuação e **só emoji**. O critério é utilidade prática: um nome sem
   nenhuma letra ou dígito é impossível de digitar numa busca, e o trabalho
   acontece em pastas com centenas de arquivos.

   Título com texto **e** emoji mantém os emoji — `Rush B 🎮` continua
   `Rush B 🎮`. Só o caso degenerado cai no fallback.

   O fallback é `video` e **não** `video_{id}`: o `[{id}]` do template já
   garante unicidade, e repetir o id no meio do nome vira ruído.
   Resultado: `20260901 - video [LzS8kB6lIm0].mp4`.

8. **Normalizar a saída para NFC.**

   `ç` pode chegar como um caractere (NFC) ou como `c` + cedilha combinante
   (NFD). São strings diferentes que parecem idênticas. Sem normalizar, a mesma
   origem variando geraria dois nomes de arquivo e dois registros no histórico
   para o mesmo vídeo — um bug caro de diagnosticar.

   O conteúdo do projeto é em português e cheio de acento, e o custo é uma
   linha.

### 8.3 Orçamento de caminho

**O teto é do CAMINHO COMPLETO, não do nome do arquivo.** O que quebra o
Premiere é o caminho inteiro; orçar só o nome deixaria a pasta de projeto fora
da conta, que é justamente a parte que cresce.

```
teto do caminho completo = 240 caracteres
```

**De onde vem o 240:** 260 é o `MAX_PATH` clássico do Windows, menos **20 de
folga** para os arquivos temporários que o yt-dlp cria antes do merge. Esses
temporários são mais longos que o nome final:

| Arquivo | Custo sobre `.mp4` |
|---|---|
| `NOME.f137.mp4.part` | +10 |
| `NOME.f251.webm.part` | +11 |
| `NOME.f616-drc.mp4.part` | +14 |

Verificado em `downloader/common.py:217-230`: `.part` e `.ytdl` são sufixos
simples concatenados. A folga de 20 cobre o pior caso com margem.

`LongPathsEnabled=1` na máquina de destino mascara o problema localmente: o
Windows aceita e o Premiere não abre. E o material vai para HD externo, NAS e
máquina de cliente, onde o registro pode estar desligado.

#### Ordem de reserva

O custo fixo é reservado **primeiro**; o que sobra é o orçamento do título:

```
fixo   = len(pasta) + 1 + len(data) + len(" - ") + len(" [") + len(id) + len("]") + len(ext)
título = 240 - fixo - 5
```

Para um id do YouTube (11) com `.mp4` e data de 8 dígitos, o fixo é
`len(pasta) + 30`.

**Os 5 caracteres a mais são reserva para o sufixo de colisão** (` (2)` a
` (99)`, §8.4). Ele é acrescentado **depois** do truncamento; sem a reserva, um
título que couber exatamente no orçamento estoura o teto assim que houver uma
colisão. É barato — o orçamento típico do título passa de 170 caracteres, e o
YouTube limita títulos a 100.

Se `data_upload` for `None`, o bloco da data e o separador saem do template, e o
fixo cai para `len(pasta) + 19`.

#### Aviso de pasta profunda

Se sobrarem **menos de 40 caracteres** para o título, emitir **aviso, não erro**:
o download prossegue com o título truncado, e a interface informa que a pasta do
projeto está profunda demais.

Erro só quando o orçamento não cobre nem o custo fixo — aí não existe nome
válido, e a mensagem precisa dizer qual projeto e por quantos caracteres passou.

> Como o YouTube limita títulos a 100 caracteres, o truncamento só é acionado
> quando a pasta do projeto passa de ~110 caracteres. É um caminho raro, mas o
> aviso existe para ele ser visível quando acontecer.

### 8.4 Colisão

Sufixo numérico antes da extensão: `nome (2).mp4`, `nome (3).mp4`. Nunca
sobrescrever.

**A comparação é case-insensitive.** O Windows não diferencia maiúsculas de
minúsculas, mas os IDs do YouTube diferenciam: `LzS8kB6lIm0` e `lzs8kb6lim0`
seriam vídeos distintos com o mesmo nome de arquivo no disco. Uma checagem
sensível a caixa responderia "não existe" e o disco sobrescreveria footage em
silêncio.

---

## 9. Histórico — schema SQLite

Arquivo: `data/historico.db` (ignorado pelo Git).

```sql
CREATE TABLE IF NOT EXISTS historico (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT    NOT NULL,
    perfil          TEXT    NOT NULL,
    extractor       TEXT    NOT NULL,
    url_original    TEXT    NOT NULL,
    url_canonica    TEXT    NOT NULL,
    titulo          TEXT    NOT NULL,
    canal           TEXT,
    duracao_s       INTEGER,
    projeto         TEXT    NOT NULL,
    caminho         TEXT,
    tamanho_bytes   INTEGER,
    status          TEXT    NOT NULL,
    motivo_falha    TEXT,
    mensagem_falha  TEXT,
    criado_em       TEXT    NOT NULL,
    concluido_em    TEXT,

    UNIQUE (extractor, video_id, perfil)
);

CREATE INDEX IF NOT EXISTS idx_historico_titulo  ON historico (titulo);
CREATE INDEX IF NOT EXISTS idx_historico_projeto ON historico (projeto);
CREATE INDEX IF NOT EXISTS idx_historico_criado  ON historico (criado_em DESC);
```

### 9.1 Decisões do schema

**A chave é `(extractor, video_id, perfil)`.** O mesmo vídeo em qualidades
diferentes são registros distintos — é requisito. `extractor` entra na chave
porque `video_id` só é único dentro de um site; dois sites podem colidir.

**Datas em TEXT ISO-8601 UTC.** SQLite não tem tipo de data. ISO-8601 ordena
corretamente como texto, o que torna o índice em `criado_em` útil.

**`caminho` é `NULL` até concluir.** Um caminho preenchido com status diferente
de `concluido` é inconsistência.

**`motivo_falha` guarda o enum; `mensagem_falha` guarda o texto original do
yt-dlp.** Os dois. O enum serve para filtrar e decidir retry; o texto original é
o fallback que impede "erro desconhecido" quando o site muda a mensagem
(§5.6).

### 9.2 Valores de `status`

`baixando`, `concluido`, `falhou`, `interrompido`.

Duas colunas acompanham o status:

- **`ja_existia`** — o arquivo já estava no destino e o download foi pulado.
  É sucesso, mas o usuário precisa saber que nada foi baixado (§9.3).
- **`aviso`** — texto não-bloqueante, acumulado com ` | `. Usado quando o
  arquivo já existia, quando o histórico não pôde ser atualizado, e quando um
  `interrompido` tem arquivo no destino (§10.6).

`baixando` é gravado **no início** do download. Sem essa linha, a reconciliação
de §10.1 não teria o que marcar: um job morto no meio não deixaria rastro, e
"marcar como interrompido" seria impossível. A fila tem mais estados (§10.2);
o histórico registra o início e o desfecho.

**UMA LINHA POR TENTATIVA de download**, não por chave.

A primeira versão tinha `UNIQUE (extractor, video_id, perfil)` e fazia upsert.
Um smoke test com download real mostrou o resultado: depois de rebaixar com
`forcar`, o disco tinha **3 arquivos e o histórico 2 registros** — o arquivo
anterior ficou órfão, sem nenhuma linha apontando para ele. E um re-download
que *falhasse* apagava o caminho de um arquivo que continuava no disco.

Isso violava as duas promessas centrais do produto: footage não some em
silêncio, e o histórico não mente sobre onde o arquivo está. Cada arquivo
baixado tem agora a sua linha.

Consequências:

- `iniciar()` devolve o registro criado; `concluir()`, `falhar()`,
  `registrar_destino()` e `avisar()` identificam a tentativa pelo **`id`**.
- `ja_baixado()` devolve a tentativa **concluída mais recente** da tripla. Uma
  falha posterior não apaga o arquivo que está no disco, então ele continua
  sendo encontrado.
- O histórico cresce com as tentativas. Para uso pessoal isso é aceitável, e é
  exatamente o que "nunca some em silêncio" pede.

O schema traz ainda `titulo_busca` (título normalizado: minúsculas, sem
acento — o `LIKE` do SQLite só ignora caixa em ASCII) e `resolucao` (a
resolução **realmente baixada**, para o usuário enxergar quando o seletor caiu
num fallback abaixo do que o perfil pedia).

---

## 10. Fila — schema e estados

### 9.3 Destino já ocupado: sucesso com aviso

O caminho de destino é resolvido contra colisão **na hora de baixar**
(`resolver_colisao`), então normalmente ele não existe. Mas entre a resolução e
a gravação há uma janela, e o arquivo pode aparecer.

Nesse caso o yt-dlp, com `overwrites=False`, dispara `finished` sem baixar — e
o job pareceria um download normal. O worker checa o destino antes e trata o
caso explicitamente:

- o job termina em **`concluido`**, não em falha: o footage está lá;
- `ja_existia = true` e um **aviso** dizem que nada foi baixado;
- o downloader **não é chamado**, e o arquivo existente não é tocado.

---

## 10. Fila

### 10.1 A fila é em memória, com reconciliação no histórico

A fila viva não é persistida. Ao subir, o programa marca como `interrompido`
todo registro que ficou em estado não-terminal, e a fila começa vazia.

Justificativa: é uma ferramenta local de um usuário. Persistir a fila
introduziria o problema de retomar downloads parciais entre execuções, que é
complexidade que o produto não precisa. O que **não** pode acontecer é um job
interrompido ser lido como concluído — e isso a reconciliação resolve.

### 10.2 Estados

```
na_fila ──> baixando ──> concluido
   │            │
   │            └──────> falhou
   │            │
   │            └──────> interrompido   (programa fechou no meio)
   │
   └──> cancelado                       (só antes de começar)
```

| Estado | Terminal? | Descrição |
|---|---|---|
| `na_fila` | não | Aguardando o worker |
| `baixando` | não | Em progresso |
| `concluido` | sim | Arquivo no disco, histórico gravado |
| `falhou` | sim | Erro classificado |
| `cancelado` | sim | Cancelado antes de começar |
| `interrompido` | sim | Programa fechou durante o download |

**Transições ilegais** (rejeitadas pelo domínio): cancelar um job em `baixando`;
qualquer saída de estado terminal; `na_fila` direto para `concluido`.

### 10.3 Estrutura de um job em memória

| Campo | Tipo | Nota |
|---|---|---|
| `id` | str | UUID gerado na criação |
| `video` | `Video` | metadados já obtidos na inspeção |
| `perfil` | str | nome do perfil |
| `projeto` | str | nome do projeto |
| `estado` | enum | §10.2 |
| `progresso` | `Progresso` | substituído atomicamente, nunca mutado |
| `caminho_final` | str \| None | preenchido ao concluir |
| `motivo_falha` | enum \| None | |
| `mensagem_falha` | str \| None | texto original do yt-dlp |
| `criado_em` | datetime | |

### 10.4 Concorrência

**Uma** thread de trabalho, consumindo uma `queue.Queue`. Um download por vez.

O estado de todos os jobs fica num dicionário protegido por
`threading.Lock`. O progress hook adquire o lock apenas para **substituir** o
objeto `Progresso` do job — não muta campo a campo.

Isso é obrigatório, não precaução: o hook pode ser chamado de threads criadas
pelo próprio yt-dlp, sem configuração especial (RESEARCH §3.4, caso 4).

**O hook não faz I/O.** Nada de SQLite, nada de `print`. Ele dispara muitas
vezes por segundo; a persistência acontece nas transições terminais.

### 10.5 Cancelamento

- Job em `na_fila`: marcado `cancelado`; o worker o descarta ao retirá-lo.
- Job em `baixando`: **não cancelável**. A API responde `409 Conflict`.

Justificativa: interromper o yt-dlp no meio exigiria levantar exceção de dentro
do progress hook, o que deixa arquivos `.part` órfãos e estado ambíguo. Para uma
ferramenta de um usuário, esperar o download atual terminar é aceitável; estado
corrompido não é.

### 10.6 Avisos: o que não é falha mas precisa ser visto

Quatro situações terminam bem, ou quase, e mesmo assim o usuário precisa saber.
Todas viajam no campo `aviso` do job e do registro de histórico — nunca em log,
que a interface não mostra.

| Situação | O que acontece | O que o usuário vê |
|---|---|---|
| **Arquivo já existia** (§9.3) | Job `concluido`, `ja_existia: true` | "O arquivo já existia no destino; o download foi pulado" |
| **Histórico indisponível** | Job com o estado certo na fila; a linha do histórico não foi atualizada | "O download terminou, mas o histórico não pôde ser atualizado" |
| **Interrompido com arquivo no destino** | Na subida seguinte, o registro `interrompido` tem um arquivo no caminho pretendido | "Há um arquivo de N bytes; não é possível verificar se está completo" |
| **Resolução abaixo do perfil** | Job `concluido` cuja menor dimensão ficou abaixo do `limite_dimensao` | "O perfil aceita até 2160p, mas o arquivo veio em 1920x1080" |

O terceiro caso merece explicação. Um download que termina **depois** de
`parar()` deixa o job `interrompido` e o arquivo no disco. Na subida seguinte,
a reconciliação encontra os dois.

Concluir automaticamente seria mentira: o arquivo pode estar truncado, e **não
há como verificar** — o tamanho esperado nunca chegou a ser gravado, porque o
`concluir()` não rodou. Baixar de novo em silêncio produziria uma duplicata
" (2)" sem explicação.

Então o produto **avisa e para**: mostra o arquivo, diz que não dá para
garantir a integridade, e deixa a decisão com quem sabe o que fazer com
footage. Para isso funcionar, o caminho pretendido é gravado no histórico
**antes** do download (`registrar_destino`); sem ele, um `interrompido` não
teria onde ser procurado.

O quarto caso fecha um laço antigo. A resolução REAL vinha do evento
`finished` do yt-dlp e era gravada no histórico desde o T5, mas ninguém a
comparava com o perfil — e "pedi 4K, veio 1080p" é indistinguível de um
acerto enquanto ninguém compara. A comparação usa a **menor dimensão**, a
mesma medida do teto (§6.3): um Short 1080x1920 tem qualidade 1080, não 1920.
O teto é máximo, não promessa, então não é falha — o site pode não ter, ou
ter só num codec que o perfil recusa. Perfil sem teto (`maxima`,
`so_audio`) e resolução desconhecida nunca avisam.

---

## 11. API HTTP

Toda em `127.0.0.1`. Só JSON. Zero regra de negócio — delega a `pipeline.py`.

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/inspecionar` | link(s) → metadados, **sem baixar** |
| POST | `/api/fila` | adiciona job(s) com perfil e projeto |
| GET | `/api/fila` | estado atual de todos os jobs |
| DELETE | `/api/fila/{id}` | cancela job que ainda não começou |
| GET | `/api/historico` | com busca e filtro |
| GET | `/api/config` | perfis, projetos, status do ffmpeg |

### 11.1 Códigos de resposta

| Código | Quando |
|---|---|
| 200 | Sucesso |
| 400 | Link inválido, perfil ou projeto inexistente |
| 404 | Job não encontrado |
| 409 | Cancelamento de job já em andamento |
| 422 | Corpo malformado (FastAPI) |

`POST /api/inspecionar` responde **200 com resultado parcial** quando alguns
links falham: cada item traz seu próprio `ok` e, se falhou, o motivo. Um link
ruim numa lista de dez não invalida os outros nove.

---

## 12. Interface

HTML/CSS/JS puro em `web/`. Sem framework, sem etapa de build.

- Campo de colar links, vários, um por linha
- Cartão de preview por vídeo: thumbnail, título, canal, duração, seletor de
  perfil, seletor de projeto
- Fila com barra de progresso, velocidade e tempo restante, via **polling a cada
  1 s**
- Botão de cancelar por item
- Histórico navegável com busca e botão "abrir pasta"
- Aviso no topo se o ffmpeg não estiver instalado
- Variáveis CSS no topo de `style.css`
- Código comentado, sem minificação

**Polling e não WebSocket**, deliberadamente: um usuário, uma aba, atualização a
cada segundo. WebSocket adicionaria reconexão e gestão de estado sem benefício
perceptível.

---

## 12b. Projetos editados pela tela

O `config/projetos.yaml` continua sendo a fonte da verdade e continua editável
à mão. O que mudou é que a interface também escreve nele.

### 12b.1 Nome de projeto

Chave de YAML, identificador na API e segmento de URL em
`DELETE /api/projetos/{nome}`. Aceita `[A-Za-z0-9]` no primeiro caractere e
`[A-Za-z0-9_-]` nos demais, até 40. `avulso` é **reservado**.

A restrição é do domínio (`validar_nome`), pura, e existe para não haver três
lugares diferentes escapando a mesma string.

### 12b.2 Escrita preservando comentários

O arquivo é editado por TEXTO, não reserializado: acrescenta um bloco no fim,
remove um bloco por chave. Reserializar com o PyYAML apagaria os comentários —
inclusive o aviso de apontar `pasta` para fora do repositório — e reordenaria
as chaves.

Duas redes de proteção, porque um `projetos.yaml` corrompido derruba a
aplicação na subida seguinte: o resultado é relido e conferido **antes** de
substituir o arquivo, e a gravação é atômica.

### 12b.3 Gravabilidade testada com escrita real

`os.access` no Windows ignora ACL e responde que dá para escrever onde não dá.
A validação grava um arquivo temporário na pasta e apaga. Descobrir que o
destino é somente-leitura depois de baixar 4 GB seria a pior hora.

### 12b.4 Remoção não mexe no histórico

Tirar um projeto tira o **destino da lista**. As linhas do histórico continuam
apontando para os arquivos, que continuam no disco — o histórico nunca mente
sobre onde o arquivo está.

Remover é recusado em dois casos: quando o projeto tem download na fila ou em
andamento (o worker ficaria sem para onde gravar, no meio da gravação), e
quando é o último (sem nenhum projeto a aplicação não sobe).

### 12b.5 Destino avulso

`POST /api/fila` aceita `pasta` no lugar de `projeto`: um caminho digitado na
hora, válido só naqueles downloads e não gravado no YAML. Passa pela mesma
validação de um projeto. No job e no histórico o `projeto` vira `"avulso"`; o
`caminho` é que diz para onde o arquivo foi.

### 12b.6 O seletor de pasta é do back-end

O navegador **não entrega caminho de disco**. A File System Access API está
disponível em `127.0.0.1` (contexto seguro), mas devolve um handle sem
nenhum acessor de caminho — é decisão da especificação, para um site não
mapear o disco de quem o abre. `<input webkitdirectory>` e arrastar pasta dão
apenas caminho relativo.

Como servidor e navegador rodam na mesma máquina (§11 vincula em 127.0.0.1),
quem abre o seletor nativo é o back-end, em **subprocesso** com timeout: o Tk
não é thread-safe, os handlers rodam em threadpool, e um diálogo esquecido
aberto penduraria a requisição. Medido: 190 ms para o processo, contra 654 ms
para criar um Tk dentro de uma thread do servidor.

---

## 12c. Cookies do navegador e o bloqueio antibot

### 12c.1 O erro

O YouTube responde `Sign in to confirm you're not a bot` quando desconfia do
tráfego. Ele caía em `desconhecido`, e a tela despejava o texto em inglês do
yt-dlp. Virou motivo próprio: `bloqueio_bot`, com mensagem em português que
diz o que fazer.

A substring da tabela é `not a bot`, sem apóstrofo: o texto real usa U+2019
(`you’re`), e comparar com o apóstrofo ASCII não casa.

O bloqueio é **intermitente**. Medido em 03/09/2026: o mesmo link que falhou
passou minutos depois, sem cookies e sem trocar de versão do yt-dlp. Não é
regressão do yt-dlp nem falta de atualização.

### 12c.2 Atualizar o yt-dlp não resolve

Verificado antes de implementar: a versão instalada (2026.08.19) já era a
mais nova do PyPI. Ao contrário do HTTP 403 do spike, este erro não é
resolvido por atualização — é decisão do site sobre a requisição.

### 12c.3 Cookies do navegador, desligados por padrão

`config/cookies.yaml` guarda `navegador` e `perfil`, e a tela edita em
Ajustes. Desligado por padrão: ler o banco de cookies do navegador é
intrusivo e a maioria dos downloads não precisa.

A lista de navegadores aceitos vem de `yt_dlp.cookies.SUPPORTED_BROWSERS`, do
yt-dlp instalado — não de uma cópia nossa, que desatualizaria em silêncio.

### 12c.4 A leitura é testada na hora de ligar

`load_cookies` do yt-dlp (cookies.py:113) embrulha **toda** exceção em
`CookieLoadError('failed to load cookies')` e descarta a original. Pelo
caminho normal, portanto, a causa é irrecuperável.

Por isso ligar os cookies chama o extrator DIRETO antes de gravar: a causa
real aparece na hora de escolher, e uma opção que só falharia no meio do
próximo download não chega a ser gravada.

### 12c.5 As três armadilhas do Windows, medidas

| Detalhe do yt-dlp | Causa | Contorno |
|---|---|---|
| `could not find <nav> cookies database` | Navegador não instalado ou sem perfil | Escolher outro |
| `Could not copy Chrome cookie database` | Navegador **aberto** travando o arquivo | Fechar o navegador |
| `Failed to decrypt with DPAPI` | App-Bound Encryption do Chrome 127+ | **Nenhum** pela ferramenta |

O terceiro é o pior: falha mesmo com o navegador fechado. Nesta máquina, em
03/09/2026, o Edge cai exatamente nele. O Firefox não tem nenhum dos três.

---

## 12d. Conversão para ProRes 422 HQ (perfil `premiere_4k`)

O YouTube só serve 4K em VP9 ou AV1, e o Premiere não edita esses codecs bem.
O perfil `premiere_4k` baixa como o `edicao_4k` e, depois, converte com o
ffmpeg para ProRes 422 HQ em `.mov`: `prores_ks` profile 3, 10 bits 4:2:2,
áudio PCM 24 bits.

Reverte uma decisão antiga (RESEARCH §2.2 dizia que o mezzanine era trabalho de
ffmpeg fora do projeto). Reverte por pedido do autor: o objetivo é o arquivo
cair no disco já pronto para a timeline.

- **Não é postprocessor do yt-dlp.** O `FFmpegVideoConvertor` só recebe o
  container; ProRes HQ exige `-profile:v 3`. A conversão é etapa própria do
  worker, depois do download e antes do `concluido`.
- **Não é estado novo.** O job fica em `baixando` com `fase: "convertendo"` e
  progresso em SEGUNDOS de vídeo (o tamanho final não é conhecido antes, a
  duração é). A máquina de estados do §10.2 não muda.
- **O `.mkv` baixado fica.** Apagar footage sozinho é o que o projeto evita. O
  histórico aponta para o `.mov`.
- **Falha na conversão não falha o job.** O download deu certo e o footage
  existe: o job conclui apontando para o `.mkv`, com aviso.
- **Nunca sobrescreve.** A colisão do `.mov` é resolvida como a do download. E
  há uma recusa prévia na própria conversão, por um motivo medido: com `-n` e
  destino existente, o ffmpeg desta máquina sai com código 0 **sem gravar**, e
  o `.mov` antigo pareceria a conversão nova.

Custos medidos (clipe 4K 60fps sintético, nesta máquina): conversão cerca de
**4x mais lenta que o tempo real**, e o ProRes HQ em 4K60 fica na ordem de
**5 a 10 GB por minuto** — o maior valor vale para gameplay, que tem mais
detalhe.

---

## 13. Decisões tomadas sem consulta

Registradas para revisão.

| # | Decisão | Racional |
|---|---|---|
| 1 | `(extractor, video_id, perfil)` como chave, e não só `(video_id, perfil)` | `video_id` só é único por site |
| 2 | Fila em memória + reconciliação de interrompidos na subida | Evita retomada parcial entre execuções |
| 3 | Job em `baixando` não é cancelável (409) | Cancelar no meio deixa `.part` órfão e estado ambíguo |
| 4 | Datas em TEXT ISO-8601 UTC | SQLite não tem tipo de data; ISO ordena como texto |
| 5 | Proibidos viram `-`, não homóglifos Unicode | Nome tem que ser digitável e buscável |
| 6 | Orçamento de 240 chars de **caminho completo** | 260 do Windows menos 20 de folga para os temporários do yt-dlp (§8.3) |
| 6b | Mapeamento **por caractere** proibido, não regra única | `\|` é separador, `/` é intervalo, `:` é ruído — achatar tudo em `-` destrói informação (§8.2) |
| 6c | Fallback `video_{id}` quando não sobra nenhum alfanumérico | Uma regra cobre título vazio, só espaços, só pontuação e só emoji; o critério é ser digitável numa busca |
| 6d | Aviso, e não erro, quando sobram menos de 40 chars para o título | O download ainda é possível; o que o usuário precisa é enxergar que a pasta está profunda demais |
| 6e | Colisão comparada de forma case-insensitive | Windows não diferencia caixa, IDs do YouTube sim — sobrescrita silenciosa de footage é o pior modo de falha do projeto |
| 7 | Polling de 1 s em vez de WebSocket | Um usuário, uma aba |
| 8 | `motivo_falha` **e** `mensagem_falha`, ambos | Enum para lógica, texto original como fallback |
| 9 | Perfil com `,` no seletor é erro de config | Quebra a premissa de um arquivo por job |
| 10 | Thumbnail não é baixada pelo backend | O navegador busca do CDN |

### 13.1 Decisões da etapa 2 (revisão pós-smoke-test)

Tomadas com o autor depois da primeira integração real, sob o princípio
*footage nunca some em silêncio, e o histórico nunca mente sobre onde o arquivo
está*.

| # | Decisão | Onde |
|---|---|---|
| 1 | Destino já ocupado é **sucesso com aviso** (`ja_existia`), não falha | §9.3 |
| 2 | `continuedl` **mantido** no padrão do yt-dlp | §13.2 |
| 3 | Histórico passa a guardar **uma linha por tentativa** | §9.2 |
| 4 | Falha ao gravar o desfecho vira **aviso visível**, nunca silêncio | §10.6 |
| 5 | `interrompido` com arquivo no destino **avisa**, não conclui nem duplica | §10.6 |
| 6 | A pasta do projeto **não é criada** na subida, só ao baixar | §7 |

### 13.2 Dívida conhecida: `continuedl`

O yt-dlp retoma um `.part` parcial por padrão, e o projeto **mantém** esse
comportamento: reiniciar do zero custa banda, e o risco é teórico.

O risco, registrado para quando fizer falta: se o site reencodar o stream entre
a tentativa interrompida e a retomada, os bytes do `.part` antigo e os novos
pertencem a codificações diferentes, e o arquivo resultante pode ficar
corrompido sem nenhum erro.

**Se aparecer um arquivo corrompido sem explicação, este é o primeiro
suspeito.** O teste é simples: apagar o `.part` e baixar de novo do zero.

## 14. Decisões em aberto

| # | Questão | Situação |
|---|---|---|
| 1 | Container do `edicao_4k`: `mkv` ou forçar `mp4`? | Proposto `mkv`; depende do fluxo de edição |
| 2 | Embutir thumbnail no arquivo? | Recomendado **não**; `writethumbnail` como `.jpg` ao lado é mais barato |
| 3 | ~~Orçamento de caminho~~ | **DECIDIDO**: 240 chars de caminho completo (§8.3) |
| 4 | Retry automático em falha de rede? | Não especificado. Sugestão: manual no MVP |
| 5 | Limpeza de `.part` órfãos na subida? | Não especificado |
