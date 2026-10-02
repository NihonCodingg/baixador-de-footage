# CONTRATO-API.md — Baixador de Footage

Este documento descreve a API que a interface consome. Foi escrito para quem
**não conhece o projeto** e vai desenhar a tela a partir daqui.

Todos os exemplos de resposta (§10) foram **gerados por execução real** da API
(`scripts/gerar_exemplos_contrato.py`), sobre os metadados de um vídeo real. O
download em si é simulado — o gerador não toca a rede —, então os números de
progresso e tamanho são ilustrativos, mas a **forma** de cada resposta é
exatamente a que a interface vai receber. Nenhum exemplo foi editado à mão.

O back-end foi validado com um download real de ponta a ponta antes desta
versão: o arquivo saiu em 1080x1920 H.264 + AAC, o histórico registrou o
caminho e a resolução corretos, e o conflito de duplicata disparou.

---

## 1. O que a aplicação faz

É uma ferramenta **local**, de um usuário só, que roda no próprio computador
(`http://127.0.0.1:8000`). O usuário é editor de vídeo e usa a ferramenta para
baixar footage que vai entrar nas edições dele.

O fluxo de uso, na ordem em que a tela deve conduzir:

1. Cola um ou vários **links** (um por linha) numa caixa de texto.
2. A tela mostra **na hora**, sem baixar nada: thumbnail, título, canal,
   duração e as qualidades disponíveis de cada vídeo.
3. Para cada vídeo, escolhe um **perfil de qualidade** (ex.: "1080p para
   edição") e um **projeto de destino** (a pasta do cliente).
4. Manda para a **fila**. Os downloads acontecem **um por vez**, em sequência.
5. Acompanha o **progresso** enquanto continua trabalhando.
6. Consulta o **histórico** depois e vê onde cada arquivo foi parar.

Não há login, não há vários usuários, não há upload. A interface é uma página
só.

**Dois princípios governam o produto**, e a tela deve refletir os dois:

> **Footage nunca some em silêncio.** Nenhum arquivo baixado fica sem registro,
> nada é sobrescrito, e toda situação estranha vira um aviso visível.
>
> **O histórico nunca mente sobre onde o arquivo está.** Se o histórico diz que
> um arquivo está num caminho, ele está lá.

## 2. Regras gerais da API

| Regra | Detalhe |
|---|---|
| Base | `http://127.0.0.1:8000` |
| Formato | JSON em tudo: corpo de requisição e de resposta |
| Codificação | UTF-8. Títulos têm acento e podem ter emoji |
| Erros | **Uma forma só**, em qualquer status de erro: `{"erro": "mensagem legível em português"}`. O 422 acrescenta `detalhes`. Nunca vem stack trace |
| Estáticos | `GET /` serve a página (`index.html`); os demais arquivos da pasta `web/` são servidos na raiz |

Códigos de status que a tela precisa tratar:

| Código | Significado | O que mostrar |
|---|---|---|
| 200 | Sucesso | — |
| 400 | Entrada inválida: link, perfil ou projeto que não existe, ffmpeg ausente | A mensagem de `erro`, junto do campo que a causou |
| 404 | Job inexistente (só em `DELETE /api/fila/{id}`) | A mensagem; provavelmente a fila mudou — recarregar |
| 409 | Conflito: já baixado, já na fila, ou cancelar job em andamento | A mensagem. Para "já baixado", oferecer **baixar de novo** (`forcar`) |
| 422 | Corpo malformado (campo faltando ou tipo errado) | Bug de integração; mostrar `erro` e logar `detalhes` |
| 500 | Erro interno | A mensagem de `erro`, com opção de tentar de novo |

## 3. Endpoints

| Método | Caminho | Para quê |
|---|---|---|
| `GET` | `/api/config` | Perfis, projetos e status do ffmpeg. **Chamar primeiro**, ao abrir a página |
| `POST` | `/api/inspecionar` | Links colados → metadados de cada vídeo, **sem baixar** |
| `POST` | `/api/fila` | Enfileira um ou mais links com um perfil e um projeto |
| `GET` | `/api/fila` | Estado de todos os jobs. **Polling a cada 1 s** |
| `DELETE` | `/api/fila/{id}` | Cancela um job que ainda não começou |
| `GET` | `/api/historico` | O que já foi baixado, com busca e filtro |
| `POST` | `/api/abrir-pasta` | Abre no explorador a pasta de um arquivo baixado |
| `GET` | `/api/projetos` | Os projetos cadastrados |
| `POST` | `/api/projetos` | Cadastra um projeto novo |
| `DELETE` | `/api/projetos/{nome}` | Remove um projeto |
| `POST` | `/api/escolher-pasta` | Abre o seletor NATIVO de pasta do sistema |
| `POST` | `/api/cookies` | Liga ou desliga os cookies do navegador |

> **Não existe mais nenhum endpoint além destes doze.** O que a tela precisar
> e não estiver aqui não existe.

### 3.1 `GET /api/config`

Sem parâmetros. Devolve três blocos:

- `ffmpeg` — `disponivel` (o ffmpeg foi encontrado), `completo` (ffmpeg **e**
  ffprobe), e os caminhos. **Se `disponivel` for `false`, a tela mostra um aviso
  fixo no topo**: sem ffmpeg os perfis de edição não funcionam.
- `perfis[]` — `nome` (o identificador a enviar em `/api/fila`), `descricao`
  (o texto para o usuário), `disponivel` (se `false`, mostrar desabilitado com o
  motivo "exige ffmpeg"), `limite_dimensao` (o teto de qualidade, `null` para o
  perfil de áudio), `container` (extensão do arquivo final: `mp4`, `mkv`, `m4a`).
- `projetos[]` — `nome` (identificador a enviar), `rotulo` (texto para o
  usuário), `pasta` (o destino no disco), `valido` e `motivo` (se `false`,
  mostrar desabilitado com o motivo).
- `cookies` — `navegador` e `perfil` configurados, `ativo`, `motivo` (por que
  está desligado, se houver) e `navegadores[]` (a lista aceita, vinda do
  yt-dlp instalado). Ver §3.10.

A pasta de um projeto **pode ainda não existir** e mesmo assim ser válida: ela é
criada no primeiro download. Não trate "pasta inexistente" como erro.

### 3.2 `POST /api/inspecionar`

Corpo: `{"links": "texto colado, um link por linha"}`. Envie o conteúdo da
caixa de texto **como está** — a API apara espaços, ignora linhas vazias e
remove duplicatas.

Resposta: `{"itens": [...]}`, **um item por link distinto**, na ordem colada.
Cada item tem `ok`:

- `ok: true` — traz `video` (metadados), `url` (a forma canônica do link),
  `e_youtube`, `aviso` (ver §7) e `baixados` (ver §8).
- `ok: false` — traz `erro` (mensagem para o usuário) e `motivo` (código, ver
  §6). Um link ruim **não invalida os outros**: a tela mostra um cartão de erro
  para ele e cartões normais para os demais.

Esta chamada **espera a rede** (consulta o site de cada vídeo). Com dez links
pode levar alguns segundos: mostrar estado de carregamento. Chamadas repetidas
para o mesmo link são instantâneas (a API tem cache na sessão).

Dentro de `video`:

| Campo | Tipo | Nota |
|---|---|---|
| `id` | string | Identificador no site de origem |
| `titulo` | string | Pode ter acento, emoji, ser longo |
| `canal` | string ou `null` | |
| `duracao_s` | inteiro ou `null` | **Segundos**. Ver §5 |
| `thumbnail` | URL ou `null` | Imagem hospedada no site de origem; usar direto no `<img>`. Ver §5 |
| `data_upload` | `"AAAAMMDD"` ou `null` | |
| `qualidades` | lista de inteiros | Qualidades disponíveis, pela **menor dimensão**. Ver §5 |
| `formatos[]` | lista | Todos os streams disponíveis. Detalhe técnico; a tela principal não precisa exibir |

### 3.3 `POST /api/fila`

Corpo:

```json
{"urls": ["https://..."], "perfil": "edicao_1080", "projeto": "pessoal", "forcar": false}
```

- `urls`: lista de links (os mesmos que foram inspecionados, ou não — a
  inspeção não é pré-requisito).
- `perfil`: o `nome` vindo de `/api/config`.
- `projeto` **ou** `pasta`, nunca os dois:
  - `projeto` — o `nome` de um projeto cadastrado;
  - `pasta` — um caminho digitado na hora, usado **só neste download** e não
    gravado no `projetos.yaml`. Passa pela mesma validação de um projeto
    (existe, é pasta, aceita escrita). Nos jobs e no histórico o `projeto`
    desses downloads vem como `"avulso"`; quem diz para onde o arquivo foi é
    o `caminho`, que é exato.

  Faltarem os dois é **400**, não 422: `projeto` é opcional no corpo, e qual
  destino usar é regra de negócio.
- `forcar`: opcional, padrão `false`. `true` baixa de novo um vídeo já
  concluído neste perfil. O arquivo antigo **nunca é sobrescrito**: o novo ganha
  sufixo ` (2)`, e **os dois continuam no histórico**.

Resposta: `{"ids": ["..."]}` — um id por job criado, na ordem das `urls`.

**Tudo ou nada**: se qualquer link da lista for inválido ou conflitar, nada é
enfileirado e a resposta é 400/409.

### 3.4 `GET /api/fila`

Sem parâmetros. `{"jobs": [...]}` na ordem de chegada, incluindo os já
terminados (a fila é a sessão atual; ao fechar o programa ela zera, e o que
importa vai para o histórico).

A tela faz **polling a cada 1 segundo** enquanto houver job em `na_fila` ou
`baixando`. Não disparar uma nova requisição antes de a anterior responder.

Campos de cada job:

| Campo | Tipo | Nota |
|---|---|---|
| `id` | string | Para o `DELETE` |
| `estado` | string | Um dos seis de §6 |
| `url` | string | O link que originou o job. Reenfileirar com ele ("tentar de novo") não depende do que a aba lembra |
| `perfil`, `projeto` | string | Os `nome`s |
| `criado_em` | ISO-8601 UTC | Ex.: `2026-09-02T18:48:58+00:00` |
| `video` | objeto | `id`, `titulo`, `canal`, `duracao_s`, `thumbnail` |
| `progresso` | objeto ou `null` | `null` antes de começar. Ver §5 |
| `caminho_final` | string ou `null` | Preenchido só em `concluido` |
| **`ja_existia`** | booleano | `true` = o arquivo já estava no destino e **nada foi baixado**. Ver §7 |
| **`aviso`** | string ou `null` | Aviso não-bloqueante. **Mostrar sempre que existir.** Ver §7 |
| `motivo_falha`, `mensagem_falha` | string ou `null` | Preenchidos só em `falhou`. Ver §6 |

### 3.5 `DELETE /api/fila/{id}`

Cancela. Só funciona para job em `na_fila`. Respostas:

- `200 {"cancelado": true}`
- `404` — id não existe
- `409` — o job já começou (ou já terminou). **Um download em andamento não
  pode ser cancelado**: é decisão do produto, para não deixar arquivo parcial
  corrompido. A tela deve **desabilitar o botão de cancelar** quando o estado for
  diferente de `na_fila`.

### 3.6 `GET /api/historico`

Parâmetros de query, todos opcionais:

- `termo` — busca no título. **Ignora acento e maiúsculas**: `selecao` acha
  "Seleção".
- `projeto` — filtra pelo `nome` do projeto.
- `limite` — padrão 100, entre 1 e 1000.

Resposta: `{"registros": [...]}`, mais recente primeiro.

**Cada registro é uma tentativa de download, não um vídeo.** Baixar o mesmo
vídeo duas vezes no mesmo perfil produz **duas linhas**, cada uma com o seu
arquivo. Isso é deliberado: cada arquivo que existe no disco tem a sua linha, e
uma tentativa que falha não apaga o registro do arquivo anterior.

Consequência para a tela: **agrupar por vídeo ao exibir**, ou o usuário verá
entradas repetidas. Sugestão: agrupar por `video_id` + `perfil`, mostrar a
tentativa mais recente e permitir expandir as anteriores.

Campos: `titulo`, `canal`, `duracao_s`, `perfil`, `projeto`, `caminho` (onde o
arquivo está), `tamanho_bytes`, `resolucao` (a que **realmente** foi baixada,
ex.: `1080x1920`), `status`, `ja_existia`, `aviso`, `motivo_falha`,
`mensagem_falha`, `criado_em`, `concluido_em`.

`status` no histórico: `baixando`, `concluido`, `falhou`, `interrompido`.

### 3.7 `POST /api/abrir-pasta`

Corpo: `{"caminho": "..."}` — o `caminho_final` de um job ou o `caminho` de um
registro do histórico. Aceita arquivo **ou** pasta.

Resposta: `200 {"aberto": true, "pasta": "D:\\FOOTAGE\\pessoal"}`. Quando o
caminho é de um arquivo, quem abre é a **pasta que o contém** — o arquivo não
fica selecionado.

**O caminho precisa estar dentro de um projeto configurado.** Qualquer outro é
`400`, e nada é aberto. A aplicação roda no seu computador, mas isso não é
motivo para ter um endpoint que abre qualquer pasta do disco: a checagem
compara segmento a segmento (então `.../cliente_x_secreto` não passa por estar
"dentro" de `.../cliente_x`), resolve `..` antes de comparar e ignora a caixa,
como o Windows.

Também é `400`, com mensagem própria, quando o caminho está num projeto mas o
arquivo não está mais no disco.

### 3.8 `GET`, `POST` e `DELETE` `/api/projetos`

O destino de um download sai do `config/projetos.yaml`, e estes três
endpoints deixam editá-lo pela tela. **O arquivo é editado preservando os
comentários** — ele é escrito à mão e carrega avisos que valem.

- `GET /api/projetos` → `{"projetos": [...]}`, os mesmos objetos de
  `/api/config`.
- `POST /api/projetos` com `{"nome": "...", "caminho": "...", "rotulo": "..."}`
  → `200 {"projeto": {...}}`. `rotulo` é opcional; sem ele, o `nome` serve.
- `DELETE /api/projetos/{nome}` → `200 {"removido": true}`.

O `nome` é chave de YAML, identificador na API e segmento de URL: aceita
letras, números, hífen e sublinhado, começa por letra ou número e vai até 40
caracteres. `avulso` é reservado.

Erros do `POST`, todos com mensagem pronta para a tela:

| Código | Quando |
|---|---|
| 400 | Nome fora do formato, ou reservado |
| 400 | A pasta não existe, não é pasta, ou **não aceita escrita** |
| 409 | Já existe projeto com esse nome (ignorando maiúsculas) |
| 422 | Falta `nome` ou `caminho` no corpo |

E do `DELETE`:

| Código | Quando |
|---|---|
| 404 | O projeto não existe |
| 409 | O projeto tem download **na fila ou em andamento** |
| 400 | É o **último** projeto — a aplicação não sobe sem nenhum |

> A gravabilidade é testada **escrevendo um arquivo de verdade** e apagando
> em seguida, não com `os.access`: no Windows o `os.access` ignora ACL e
> responde que dá para escrever onde não dá. Descobrir isso só na hora do
> download seria tarde.

Remover um projeto **não toca no histórico**: as linhas antigas continuam
apontando para os arquivos, que continuam no disco. O que sai é o destino da
lista.

### 3.9 `POST /api/escolher-pasta`

Sem corpo. Abre o seletor **nativo** de pasta do sistema e devolve
`200 {"caminho": "D:\\FOOTAGE\\algo"}`, ou `{"caminho": null}` se o usuário
cancelar — cancelar não é erro.

Existe porque **o navegador não entrega caminho de disco**. A File System
Access API (`showDirectoryPicker`) funciona em `127.0.0.1`, que é contexto
seguro, mas devolve um *handle*: toda a superfície de
`FileSystemDirectoryHandle` e `FileSystemHandle` é `getDirectoryHandle`,
`getFileHandle`, `removeEntry`, `resolve`, `entries`, `keys`, `values`,
`kind`, `name`, `isSameEntry`, `queryPermission`, `remove` e
`requestPermission` — nenhum caminho absoluto, por decisão da especificação.
O `resolve()` engana pelo nome: devolve caminho relativo entre dois handles.
`<input webkitdirectory>` e arrastar pasta também só dão caminho relativo.

Então quem abre o seletor é o back-end, que roda na mesma máquina do
navegador (a aplicação vincula em `127.0.0.1`). O diálogo roda em
**subprocesso**, com timeout de 180 s: o Tk não é thread-safe, e um diálogo
esquecido aberto penduraria a requisição.

`400` quando o seletor não pôde abrir ou passou do tempo — a tela deve
continuar aceitando o caminho digitado ou colado.

### 3.10 `POST /api/cookies`

Corpo: `{"navegador": "firefox", "perfil": "default"}`. `navegador` nulo ou
vazio **desliga**; `perfil` é opcional. Resposta: `200 {"cookies": {...}}`,
no mesmo formato do bloco de `/api/config`.

Serve ao motivo de falha `bloqueio_bot` (§6): quando o YouTube pede
confirmação de que você não é um robô, a saída é usar os cookies da sessão
já aberta no navegador.

**Desligado por padrão** — ler o banco de cookies do navegador é intrusivo, e
a maioria dos downloads não precisa.

`400`, com o detalhe do yt-dlp junto, quando:

- o navegador não está na lista de `navegadores[]`;
- **os cookies não puderam ser lidos agora**. A leitura é testada na hora de
  ligar, não no meio do próximo download. Isso é deliberado: no caminho
  normal o yt-dlp embrulha toda falha de cookie em `failed to load cookies` e
  **descarta a causa**. Chamando o extrator direto, a causa sobrevive.

Causas reais medidas no Windows, yt-dlp 2026.08.19:

| Detalhe | O que é |
|---|---|
| `could not find <navegador> cookies database` | O navegador não está instalado, ou nunca criou perfil |
| `Could not copy Chrome cookie database` | O navegador está **aberto** e travou o arquivo. Feche e tente de novo |
| `Failed to decrypt with DPAPI` | App-Bound Encryption do Chrome 127+. Falha **mesmo com o navegador fechado**; não há contorno pela ferramenta |

---

## 4. O que NÃO está implementado

Nada. Os doze endpoints de §3 existem e funcionam.

> `POST /api/abrir-pasta` esteve nesta seção como "não existe" até o T8, e a
> tela usava só "Copiar caminho" no lugar. Hoje os dois botões convivem:
> copiar serve para colar num Premiere ou num chat, abrir serve para ir até o
> arquivo. Um navegador continua não abrindo pasta sozinho — quem abre é o
> back-end, por isso o endpoint.

---

## 5. Campos que a tela precisa formatar

A API entrega valores **crus**. Não formata nada. Estes exigem tratamento:

| Campo | Vem como | Mostrar como |
|---|---|---|
| `duracao_s` | **segundos** inteiros (`65`) | `1:05`. Acima de uma hora, `1:02:05`. `null` → `--:--` |
| `tamanho_bytes` | **bytes** (`11062598`) | `10,6 MB`. `null` → `--` |
| `progresso.percentual` | float (`38.88888`) ou `null` | Arredondar: `39%`. `null` = total desconhecido → barra indeterminada |
| `progresso.velocidade_bps` | **bytes por segundo**, float ou `null` | `3,1 MB/s`. `null` → `--` |
| `progresso.eta_s` | segundos ou `null` | `0:02`. `null` → `--` |
| `fps` (em `formatos[]`) | float **fracionário** (`29.97`, `59.94`) | Uma casa decimal quando não for inteiro. **Nunca truncar** para inteiro — `59.94` virar `59` é erro |
| `thumbnail` | URL ou **`null`** | Se `null`, um placeholder. Nunca `<img>` quebrado |
| `criado_em`, `concluido_em` | ISO-8601 em **UTC** | Converter para o fuso local |
| `qualidades` | `[144, 240, ..., 1080]` | Ex.: `1080p`. É pela **menor dimensão**: um vídeo vertical 1080x1920 e um horizontal 1920x1080 mostram o mesmo `1080` |
| `resolucao` | `"1080x1920"` | Pode exibir como está; vertical tem altura maior que largura |
| `caminho`, `caminho_final`, `pasta` | string | Caminhos do Windows. Os que vêm do download usam `\`; os da configuração vêm como foram escritos, geralmente com `/`. Exibir como está; nunca editar |

### `tem_audio` e `tem_video` — três estados, não dois

Cada item de `formatos[]` traz `vcodec` e `acodec`. Aqui há uma armadilha:

- a string `"none"` significa **"não tem"**;
- **`null` significa "desconhecido"**, não "não tem".

No vídeo de exemplo, dois formatos têm `acodec: null` e **são** faixas de
áudio — o site simplesmente não informou o codec. Tratar `null` como "sem
áudio" os esconderia.

Para a tela não repetir essa lógica, a API já entrega **`tem_video` e
`tem_audio` resolvidos como booleanos**. Use-os. Só use `vcodec`/`acodec` se for
exibir o nome do codec, e aí trate `null` como "não informado".

---

## 6. Estados de um job e o que a tela mostra

```
na_fila ──> baixando ──> concluido
   │            │
   │            ├──────> falhou
   │            └──────> interrompido   (programa fechou no meio)
   └──> cancelado                       (só antes de começar)
```

São **seis** estados. `interrompido` só aparece no histórico.

| Estado | O que mostrar | Cancelar? |
|---|---|---|
| `na_fila` | Posição na fila, sem barra | **Sim** |
| `baixando` | Barra de progresso, velocidade, tempo restante | Não (botão desabilitado) |
| `concluido` | Caminho do arquivo, com "Copiar caminho" e "Abrir pasta" (§3.7). **Se `ja_existia` for `true`, um selo "já existia — não baixado"** | Não |
| `falhou` | `mensagem_falha` em destaque; se `motivo_falha` for `rede` ou `rate_limit`, oferecer **tentar de novo** | Não |
| `cancelado` | Discreto, pode colapsar | Não |
| `interrompido` | "O programa fechou durante o download". Se houver `aviso`, mostrá-lo — pode haver um arquivo parcial no disco (§7) | — |

`motivo_falha` é um código estável para lógica; `mensagem_falha` é o texto
para o usuário, já em português. Códigos possíveis:

| `motivo_falha` | Significa | Vale tentar de novo? |
|---|---|---|
| `indisponivel` | Vídeo removido ou inexistente | Não |
| `privado` | Vídeo privado | Não |
| `restricao_idade` | Exige conta autenticada | Não |
| `bloqueio_regional` | Bloqueado no país | Não |
| `drm` | Conteúdo protegido. **Fora do escopo da ferramenta**, por decisão | Não |
| `site_nao_suportado` | O site não é suportado | Não |
| `rede` | Falha de rede | **Sim** |
| `rate_limit` | O site limitou as requisições | **Sim**, depois de esperar |
| `bloqueio_bot` | O YouTube pediu confirmação de que não é um robô | Não sozinho — ligar os cookies em §3.10. O bloqueio é intermitente, então às vezes o mesmo link passa minutos depois |
| `cookies` | Os cookies do navegador configurado não puderam ser lidos | Não — corrigir ou desligar em §3.10 |
| `sem_ffmpeg` | ffmpeg ausente | Não (instalar) |
| `disco` | Falha ao gravar | Não |
| `desconhecido` | Não classificado; `mensagem_falha` traz o texto original do erro | — |

Em `/api/inspecionar`, o `motivo` de um item com `ok: false` usa os mesmos
códigos, mais `link_invalido` (a linha colada não é um link, ou é link de
canal/playlist — download em massa está fora do escopo).

---

## 7. Avisos: coisas que não são erro, mas precisam ser vistas

O campo **`aviso`** existe no job (`/api/fila`), no registro de histórico e no
item de `/api/inspecionar`. Quando não for `null`, **mostre**. Nunca esconda
num log: é a única forma de o usuário saber.

Vários avisos no mesmo objeto vêm concatenados com ` | `.

| Aviso | Quando aparece | O que a tela mostra |
|---|---|---|
| **Site que não é YouTube** | Item de `/api/inspecionar`, com `e_youtube: false` | Aviso no cartão do vídeo. Não bloqueia nada. Ver abaixo |
| **Arquivo já existia** | Job/registro `concluido` com `ja_existia: true` | Selo no card: "já existia no destino — nada foi baixado" |
| **Histórico não atualizado** | Job terminado cujo registro não pôde ser gravado | Aviso no card: o download terminou, mas o histórico ficou desatualizado |
| **Interrompido com arquivo no destino** | Registro `interrompido` na subida seguinte | Aviso no histórico: há um arquivo de N bytes, e **não dá para garantir que está completo** |
| **Resolução abaixo do perfil** | Job/registro `concluido` cuja menor dimensão ficou abaixo do teto do perfil | Aviso no card: o perfil aceitava até N p, o arquivo veio em `LxA`. Não é falha — o site pode não ter a qualidade, ou ter só num codec que o perfil recusa |

**Sobre o aviso de site não-YouTube.** A ferramenta conhece o YouTube:
normaliza o link (remove parâmetros de rastreio, aceita `youtu.be`, `/shorts/`),
evita duplicatas e casa com o histórico. Para qualquer outro site o link passa
como foi colado, e a deduplicação só funciona se ele for colado exatamente
igual. O texto vem pronto da API.

**Sobre o aviso de interrompido.** Se o programa fechou durante um download,
pode haver um arquivo parcial no destino. A ferramenta **não conclui
automaticamente** (o arquivo pode estar truncado, e não há como verificar) e
**não baixa uma duplicata em silêncio**. Ela avisa e deixa a decisão com o
usuário. A tela deve tornar isso acionável: mostrar o aviso e oferecer "baixar
de novo" (que usa `forcar: true`).

---

## 8. Duplicatas: `baixados` e `forcar`

No item de `/api/inspecionar`, `baixados` é um objeto **por perfil** com o que
já foi concluído para aquele vídeo:

```json
{"baixados": {"edicao_1080": {"caminho": "...", "projeto": "pessoal", "resolucao": "1080x1920", "concluido_em": "..."}}}
```

Vazio (`{}`) se nunca foi baixado. A tela usa isso para avisar **antes de
enfileirar**: se o perfil selecionado está em `baixados`, mostrar "já baixado
em &lt;caminho&gt;" e oferecer "baixar de novo". Se o usuário insistir, enviar
`forcar: true` em `/api/fila`; sem isso a API responde 409.

---

## 9. Resumo para quem vai desenhar

Quatro áreas na página:

1. **Entrada** — textarea de links, seletor de perfil, seletor de projeto,
   botão de inspecionar. Aviso fixo no topo se o ffmpeg faltar. Um
   **"Projetos"** abre o cadastro (§3.8), e o seletor de projeto traz
   *"Pasta avulsa…"* para um destino digitado só naquele download.
2. **Prévia** — um card por link: thumbnail (pode faltar), título, canal,
   duração formatada, qualidades. Cards de erro para links inválidos. Avisos
   de site não-YouTube e de "já baixado" aparecem aqui.
3. **Fila** — um card por job, com barra de progresso, velocidade e tempo
   restante. Botão de cancelar habilitado só em `na_fila`. Selo de `ja_existia`
   e avisos quando houver. Atualiza a cada segundo.
4. **Histórico** — busca por texto e filtro por projeto. Agrupar por vídeo, já
   que cada tentativa é uma linha. Botões "Copiar caminho" e "Abrir pasta"
   (§3.7).

O que **não** desenhar: login, upload, seleção de formato avulso (os perfis
cobrem isso), botão de cancelar download em andamento.

---

## 10. Exemplos reais

Gerados por `python scripts/gerar_exemplos_contrato.py`. Vídeo real: um Short
vertical de 65 segundos. Listas de `formatos` truncadas em 3 itens para caber.

### GET /api/config

`200`

```json
{
  "ffmpeg": {
    "disponivel": true,
    "completo": true,
    "ffmpeg": "C:\\ffmpeg\\bin\\ffmpeg.exe",
    "ffprobe": "C:\\ffmpeg\\bin\\ffprobe.exe"
  },
  "perfis": [
    {
      "nome": "edicao_1080",
      "descricao": "1080p H.264 + AAC — abre nativo no Premiere/Resolve",
      "disponivel": true,
      "exige_ffmpeg": true,
      "limite_dimensao": 1080,
      "container": "mp4"
    },
    {
      "nome": "edicao_4k",
      "descricao": "Até 2160p — VP9/AV1, pode exigir transcode para a timeline",
      "disponivel": true,
      "exige_ffmpeg": true,
      "limite_dimensao": 2160,
      "container": "mkv"
    },
    {
      "nome": "maxima",
      "descricao": "O melhor que o site tiver, sem teto — VP9/AV1, pode exigir transcode",
      "disponivel": true,
      "exige_ffmpeg": true,
      "limite_dimensao": null,
      "container": "mkv"
    },
    {
      "nome": "so_audio",
      "descricao": "Só a trilha de áudio, em m4a",
      "disponivel": true,
      "exige_ffmpeg": true,
      "limite_dimensao": null,
      "container": "m4a"
    },
    {
      "nome": "preview_leve",
      "descricao": "Até 480p, menor arquivo — para bater o olho antes de baixar",
      "disponivel": true,
      "exige_ffmpeg": true,
      "limite_dimensao": 480,
      "container": "mp4"
    }
  ],
  "projetos": [
    {
      "nome": "cliente_x",
      "rotulo": "Cliente X",
      "pasta": "D:/FOOTAGE/cliente_x",
      "valido": true,
      "motivo": null
    },
    {
      "nome": "pessoal",
      "rotulo": "Canal pessoal",
      "pasta": "D:/FOOTAGE/pessoal",
      "valido": true,
      "motivo": null
    }
  ],
  "cookies": {
    "navegador": null,
    "perfil": null,
    "ativo": false,
    "motivo": null,
    "navegadores": [
      "brave",
      "chrome",
      "chromium",
      "edge",
      "firefox",
      "opera",
      "safari",
      "vivaldi",
      "whale"
    ]
  }
}
```

### POST /api/inspecionar — três links: real, outro site, lixo

`200`

```json
{
  "itens": [
    {
      "ok": true,
      "original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "e_youtube": true,
      "aviso": null,
      "video": {
        "id": "LzS8kB6lIm0",
        "extractor": "Youtube",
        "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg",
        "data_upload": "20260901",
        "qualidades": [
          144,
          240,
          360,
          480,
          608,
          720,
          1080
        ],
        "formatos": [
          {
            "format_id": "233",
            "ext": "mp4",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": null,
            "tem_video": false,
            "tem_audio": true,
            "tbr": null,
            "tamanho_bytes": null
          },
          {
            "format_id": "234",
            "ext": "mp4",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": null,
            "tem_video": false,
            "tem_audio": true,
            "tbr": null,
            "tamanho_bytes": null
          },
          {
            "format_id": "139-drc",
            "ext": "m4a",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": "mp4a.40.5",
            "tem_video": false,
            "tem_audio": true,
            "tbr": 48.889,
            "tamanho_bytes": 397606
          },
          "... mais 38 formatos omitidos neste exemplo ..."
        ]
      },
      "baixados": {}
    },
    {
      "ok": true,
      "original": "https://vimeo.com/123456789",
      "url": "https://vimeo.com/123456789",
      "e_youtube": false,
      "aviso": "Link fora do YouTube: o download pode funcionar, mas a deduplicação e o histórico só reconhecem este endereço se ele for colado exatamente igual.",
      "video": {
        "id": "123456789",
        "extractor": "Vimeo",
        "url_canonica": "https://vimeo.com/123456789",
        "titulo": "Exemplo simulado de outro site",
        "canal": "alguem",
        "duracao_s": 120,
        "thumbnail": null,
        "data_upload": null,
        "qualidades": [],
        "formatos": []
      },
      "baixados": {}
    },
    {
      "ok": false,
      "original": "não é um link",
      "url": null,
      "erro": "Não é um link válido: 'não é um link'",
      "motivo": "link_invalido"
    }
  ]
}
```

### POST /api/fila

`200`

```json
{
  "ids": [
    "ff18d896ab9a46c6a007f8a5f25d09f4"
  ]
}
```

### GET /api/fila — durante o download

`200`

```json
{
  "jobs": [
    {
      "id": "ff18d896ab9a46c6a007f8a5f25d09f4",
      "estado": "baixando",
      "ja_existia": false,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "edicao_1080",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": {
        "baixados": 3670016,
        "total": 9437184,
        "percentual": 38.88888888888889,
        "velocidade_bps": 3276800.0,
        "eta_s": 2
      },
      "caminho_final": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": null
    }
  ]
}
```

### POST /api/fila — mesmo vídeo e perfil já na fila (409)

`409`

```json
{
  "erro": "Este vídeo já está na fila no perfil 'edicao_1080'."
}
```

### POST /api/fila — perfil inexistente (400)

`400`

```json
{
  "erro": "Perfil 'nao_existe' não existe."
}
```

### POST /api/fila — corpo malformado (422)

`422`

```json
{
  "erro": "Corpo ou parâmetros inválidos.",
  "detalhes": [
    {
      "type": "list_type",
      "loc": [
        "body",
        "urls"
      ],
      "msg": "Input should be a valid list",
      "input": "isso deveria ser uma lista"
    },
    {
      "type": "missing",
      "loc": [
        "body",
        "perfil"
      ],
      "msg": "Field required",
      "input": {
        "urls": "isso deveria ser uma lista"
      }
    }
  ]
}
```

### DELETE /api/fila/{id} — job ainda na fila

`200`

```json
{
  "cancelado": true
}
```

### DELETE /api/fila/{id} — job em andamento (409)

`409`

```json
{
  "erro": "Só é possível cancelar um job que ainda não começou (SPEC 10.5)."
}
```

### DELETE /api/fila/{id} — inexistente (404)

`404`

```json
{
  "erro": "Job 'nao-existe' não existe."
}
```

### GET /api/fila — depois: um concluído, um cancelado

`200`

```json
{
  "jobs": [
    {
      "id": "ff18d896ab9a46c6a007f8a5f25d09f4",
      "estado": "concluido",
      "ja_existia": false,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "edicao_1080",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": {
        "baixados": 9437184,
        "total": 9437184,
        "percentual": 100.0,
        "velocidade_bps": null,
        "eta_s": 0
      },
      "caminho_final": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": null
    },
    {
      "id": "9453cd22a3b741eb98b05b4d8d9ff381",
      "estado": "cancelado",
      "ja_existia": false,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "so_audio",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": null,
      "caminho_final": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": null
    }
  ]
}
```

### GET /api/historico

`200`

```json
{
  "registros": [
    {
      "id": 1,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_1080",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "tamanho_bytes": 9437184,
      "resolucao": "1080x1920",
      "status": "concluido",
      "ja_existia": false,
      "aviso": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    }
  ]
}
```

### GET /api/historico?termo=selecao&projeto=pessoal

`200`

```json
{
  "registros": [
    {
      "id": 1,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_1080",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "tamanho_bytes": 9437184,
      "resolucao": "1080x1920",
      "status": "concluido",
      "ja_existia": false,
      "aviso": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    }
  ]
}
```

### POST /api/inspecionar — depois de baixado: `baixados` preenchido

`200`

```json
{
  "itens": [
    {
      "ok": true,
      "original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "e_youtube": true,
      "aviso": null,
      "video": {
        "id": "LzS8kB6lIm0",
        "extractor": "Youtube",
        "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg",
        "data_upload": "20260901",
        "qualidades": [
          144,
          240,
          360,
          480,
          608,
          720,
          1080
        ],
        "formatos": [
          {
            "format_id": "233",
            "ext": "mp4",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": null,
            "tem_video": false,
            "tem_audio": true,
            "tbr": null,
            "tamanho_bytes": null
          },
          {
            "format_id": "234",
            "ext": "mp4",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": null,
            "tem_video": false,
            "tem_audio": true,
            "tbr": null,
            "tamanho_bytes": null
          },
          {
            "format_id": "139-drc",
            "ext": "m4a",
            "resolucao": "audio only",
            "largura": null,
            "altura": null,
            "fps": null,
            "vcodec": "none",
            "acodec": "mp4a.40.5",
            "tem_video": false,
            "tem_audio": true,
            "tbr": 48.889,
            "tamanho_bytes": 397606
          },
          "... mais 38 formatos omitidos neste exemplo ..."
        ]
      },
      "baixados": {
        "edicao_1080": {
          "caminho": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
          "projeto": "pessoal",
          "resolucao": "1080x1920",
          "concluido_em": "2026-10-02T19:05:37+00:00"
        }
      }
    }
  ]
}
```

### POST /api/fila — já baixado neste perfil (409)

`409`

```json
{
  "erro": "Já baixado no perfil 'edicao_1080': D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4. Use forcar=true para baixar de novo."
}
```

### POST /api/abrir-pasta — arquivo dentro de um projeto

`200`

```json
{
  "aberto": true,
  "pasta": "D:\\FOOTAGE\\pessoal"
}
```

### POST /api/abrir-pasta — caminho fora dos projetos (400)

`400`

```json
{
  "erro": "Só é possível abrir pastas dentro de um projeto configurado. Fora de todos eles: C:\\Windows\\System32"
}
```

### POST /api/escolher-pasta — seletor nativo do sistema

`200`

```json
{
  "caminho": "D:\\FOOTAGE\\escolhida"
}
```

### POST /api/projetos — cadastra

`200`

```json
{
  "projeto": {
    "nome": "cliente_novo",
    "rotulo": "Cliente Novo",
    "pasta": "D:\\FOOTAGE\\escolhida",
    "valido": true,
    "motivo": null
  }
}
```

### POST /api/projetos — pasta que não existe (400)

`400`

```json
{
  "erro": "Pasta inválida: a pasta não existe: D:\\FOOTAGE\\nao_existe"
}
```

### POST /api/projetos — nome já usado (409)

`409`

```json
{
  "erro": "Já existe um projeto chamado 'cliente_novo'. Escolha outro nome ou remova o antigo."
}
```

### GET /api/projetos

`200`

```json
{
  "projetos": [
    {
      "nome": "cliente_x",
      "rotulo": "Cliente X",
      "pasta": "D:/FOOTAGE/cliente_x",
      "valido": true,
      "motivo": null
    },
    {
      "nome": "pessoal",
      "rotulo": "Canal pessoal",
      "pasta": "D:/FOOTAGE/pessoal",
      "valido": true,
      "motivo": null
    },
    {
      "nome": "cliente_novo",
      "rotulo": "Cliente Novo",
      "pasta": "D:\\FOOTAGE\\escolhida",
      "valido": true,
      "motivo": null
    }
  ]
}
```

### DELETE /api/projetos/{nome}

`200`

```json
{
  "removido": true
}
```

### GET /api/fila — job com `ja_existia`: o arquivo já estava no destino

`200`

```json
{
  "jobs": [
    {
      "id": "ff18d896ab9a46c6a007f8a5f25d09f4",
      "estado": "concluido",
      "ja_existia": false,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "edicao_1080",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": {
        "baixados": 9437184,
        "total": 9437184,
        "percentual": 100.0,
        "velocidade_bps": null,
        "eta_s": 0
      },
      "caminho_final": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": null
    },
    {
      "id": "9453cd22a3b741eb98b05b4d8d9ff381",
      "estado": "cancelado",
      "ja_existia": false,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "so_audio",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": null,
      "caminho_final": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": null
    },
    {
      "id": "6b4a158f5e8840c68b5e96a8fcbce695",
      "estado": "concluido",
      "ja_existia": true,
      "url": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "perfil": "edicao_1080",
      "projeto": "pessoal",
      "criado_em": "2026-10-02T19:05:37+00:00",
      "video": {
        "id": "LzS8kB6lIm0",
        "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
        "canal": "Canal Michuruca",
        "duracao_s": 65,
        "thumbnail": "https://i.ytimg.com/vi/LzS8kB6lIm0/maxresdefault.jpg"
      },
      "progresso": null,
      "caminho_final": "D:/FOOTAGE/pessoal/20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "motivo_falha": null,
      "mensagem_falha": null,
      "aviso": "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito."
    }
  ]
}
```

### GET /api/historico — uma linha por tentativa, com `aviso`

`200`

```json
{
  "registros": [
    {
      "id": 2,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_1080",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:/FOOTAGE/pessoal/20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "tamanho_bytes": 9437184,
      "resolucao": null,
      "status": "concluido",
      "ja_existia": true,
      "aviso": "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito.",
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    },
    {
      "id": 1,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_1080",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:\\FOOTAGE\\pessoal\\20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "tamanho_bytes": 9437184,
      "resolucao": "1080x1920",
      "status": "concluido",
      "ja_existia": false,
      "aviso": null,
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    }
  ]
}
```

### GET /api/historico — na subida seguinte: `interrompido` com aviso

`200`

```json
{
  "registros": [
    {
      "id": 3,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_4k",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:\\FOOTAGE\\pessoal\\parcial-de-um-download-interrompido.mp4",
      "tamanho_bytes": null,
      "resolucao": null,
      "status": "interrompido",
      "ja_existia": false,
      "aviso": "O download foi interrompido, mas há um arquivo de 3145728 bytes em D:\\FOOTAGE\\pessoal\\parcial-de-um-download-interrompido.mp4. Não é possível verificar se está completo — confira antes de usar, ou baixe de novo com forcar.",
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    },
    {
      "id": 2,
      "extractor": "Youtube",
      "video_id": "LzS8kB6lIm0",
      "perfil": "edicao_1080",
      "url_original": "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw",
      "url_canonica": "https://www.youtube.com/watch?v=LzS8kB6lIm0",
      "titulo": "Camisa azul da Seleção: críticas ao design e lembrança histórica",
      "canal": "Canal Michuruca",
      "duracao_s": 65,
      "projeto": "pessoal",
      "caminho": "D:/FOOTAGE/pessoal/20260901 - Camisa azul da Seleção críticas ao design e lembrança histórica [LzS8kB6lIm0].mp4",
      "tamanho_bytes": 9437184,
      "resolucao": null,
      "status": "concluido",
      "ja_existia": true,
      "aviso": "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito.",
      "motivo_falha": null,
      "mensagem_falha": null,
      "criado_em": "2026-10-02T19:05:37+00:00",
      "concluido_em": "2026-10-02T19:05:37+00:00"
    }
  ]
}
```
