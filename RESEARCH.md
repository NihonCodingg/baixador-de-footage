# RESEARCH.md — Pesquisa técnica para o Baixador de Footage

**Fase 2 do projeto.** Este documento é a base técnica para as decisões de arquitetura
da Fase 4. Ele não define arquitetura — só levanta e verifica os fatos.

## Como esta pesquisa foi feita

Cada afirmação aqui vem de uma de três origens, e eu marco qual:

- 📗 **Documentação oficial** — README do yt-dlp, Microsoft Learn. Tem URL no final.
- 🔬 **Código-fonte instalado** — li o `.py` da sua própria instalação. Cito arquivo e linha.
- 🧪 **Medido na sua máquina** — rodei código e observei o resultado. Cito a saída.

Eu priorizei 🔬 e 🧪 sobre 📗 porque documentação envelhece e o código não mente.
Em dois pontos importantes (nomes reservados do Windows e o comportamento do
`sanitize_filename`) o que eu **medi** diverge do que se costuma **afirmar** — e essa
divergência está registrada, porque ela muda o seu código.

### Ambiente verificado

| Componente | Versão | Como verifiquei |
|---|---|---|
| Python | 3.14.3 | `python --version` |
| yt-dlp | 2026.07.04 | `yt_dlp.version.__version__` |
| ffmpeg / ffprobe | 9.0-full_build (Gyan) | `FFmpegPostProcessor.get_versions()` |
| Windows | 11 Pro, build 26200 | `sys.getwindowsversion()` |
| `LongPathsEnabled` | **1 (ativado)** | registro `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem` |

> ⚠️ Esse `LongPathsEnabled=1` é da **sua** máquina, não é o padrão do Windows. A Seção 7
> explica por que você não deve depender dele.

---

# 1. API Python do yt-dlp

## 1.1 O objeto `YoutubeDL` e o gerenciador de contexto

A biblioteca inteira gira em torno de uma classe: `yt_dlp.YoutubeDL`. Você a
configura com **um dicionário de opções** passado no construtor, e usa como
gerenciador de contexto (`with`).

```python
import yt_dlp

opcoes = {
    "quiet": True,
    "format": "bv*[height<=1080]+ba/b",
}

with yt_dlp.YoutubeDL(opcoes) as ydl:
    info = ydl.extract_info(URL, download=False)
```

📗 Esse é o padrão canônico do README, seção *Embedding yt-dlp*.

**Por que `with` importa (e o que ele realmente faz):** o `__exit__` do `YoutubeDL`
chama `self.close()`, que fecha o cache de conexões de rede e os *request handlers*.
Se você criar um `YoutubeDL` sem `with` e não chamar `close()`, sockets ficam abertos.
Num programa que roda por horas com uma fila, isso vaza recursos de verdade — não é
preciosismo.

**Consequência prática para você:** um `YoutubeDL` por operação, dentro de um `with`.
Não guarde uma instância global viva. O custo de criar uma é baixo; o custo de vazar
conexões num worker de longa duração não é.

## 1.2 `extract_info(download=False)` vs `download=True`

Essa é a distinção que sustenta o passo 3 do seu fluxo de uso ("a tela mostra na hora
thumbnail, título, duração... sem baixar nada ainda").

| Chamada | O que faz | Rede | Disco |
|---|---|---|---|
| `extract_info(url, download=False)` | Só extrai metadados | Sim (páginas/API do site) | Não |
| `extract_info(url, download=True)` | Extrai **e** baixa | Sim (+ mídia) | Sim |
| `ydl.download([urls])` | Atalho: extrai e baixa, retorna código de saída | Sim | Sim |

`download=False` é exatamente o que o endpoint `POST /api/inspecionar` precisa. Ele é
rápido e barato porque não toca no stream de mídia — só na página/API de metadados.

⚠️ **Cuidado com `download=False` e listas.** Se a URL for uma playlist ou um canal,
`extract_info` percorre a lista inteira. Como download em massa de canais está fora do
escopo do projeto, a validação de link (T2, domínio) precisa **rejeitar** URLs de
playlist/canal antes de chegar no adapter. Existe também a opção `'noplaylist': True`,
que faz o yt-dlp pegar só o vídeo quando a URL tem vídeo *e* playlist juntos — mas ela
é uma rede de segurança, não a defesa principal.

## 1.3 `sanitize_info` — o passo que quase todo mundo esquece

📗 O README avisa explicitamente que o retorno de `extract_info` **não é garantido ser
serializável em JSON**, nem necessariamente um `dict`. Ele é "dicionário-like", e pode
conter objetos internos (geradores, referências a instâncias do extractor).

```python
info = ydl.extract_info(URL, download=False)
info_limpo = ydl.sanitize_info(info)  # agora dá para json.dumps()
```

**Isso importa muito no seu projeto**, em dois lugares:

1. O `spike_meta.json` da Fase 3 (que vira fixture dos testes) **precisa** passar por
   `sanitize_info`, senão o `json.dumps` quebra ou grava lixo.
2. A resposta do `POST /api/inspecionar` vira JSON. Mesma coisa.

## 1.4 `ignoreerrors`: o padrão da biblioteca é diferente do padrão da CLI

🔬 `YoutubeDL.py:288-290`, na docstring:

> `ignoreerrors`: Do not stop on download/postprocessing errors. Can be `'only_download'`
> to ignore only download errors. **Default is `'only_download'` for CLI, but `False` for API.**

Traduzindo o impacto: **na biblioteca, os erros sobem como exceção por padrão.** Você
não precisa configurar nada para conseguir capturá-los — é o comportamento que o T1
quer. Se alguém setar `ignoreerrors=True` "para não quebrar", o adapter passa a engolir
falhas silenciosamente e o histórico vai registrar sucesso onde não houve. Não faça isso.

---

# 2. Seleção de formato (a seção mais importante)

Essa é a parte que justifica os perfis nomeados em YAML. A sintaxe do yt-dlp é
poderosa e ilegível — é exatamente o tipo de coisa que você não quer decorar.

## 2.1 Construindo o entendimento em camadas

### Camada 1 — um formato é um stream, não um vídeo

No YouTube (e na maioria dos sites modernos), um "vídeo em 1080p" **não existe como um
arquivo só**. O site serve:

- streams **só de vídeo** (video-only): 1080p, 1440p, 2160p...
- streams **só de áudio** (audio-only): opus 128k, m4a 128k...
- alguns streams **combinados** (progressive): geralmente só até 720p, e cada vez mais raros

Por isso baixar 1080p **exige juntar dois streams**, e juntar exige ffmpeg. Não é
capricho do yt-dlp; é como o site entrega.

### Camada 2 — os apelidos

📗 Do README, seção *Format Selection*:

| Apelido | Significado exato | Equivalente literal |
|---|---|---|
| `b` / `best` | melhor que tem vídeo **e** áudio juntos | `best*[vcodec!=none][acodec!=none]` |
| `b*` / `best*` | melhor que tem vídeo **ou** áudio | `vcodec!=none or acodec!=none` |
| `bv` / `bestvideo` | melhor **só-vídeo** | `best*[acodec=none]` |
| `bv*` / `bestvideo*` | melhor que **contém** vídeo (pode ter áudio junto) | `best*[vcodec!=none]` |
| `ba` / `bestaudio` | melhor **só-áudio** | `best*[vcodec=none]` |
| `w`, `wv`, `wa` | as versões "pior" de cada um | — |

**A diferença entre `bv` e `bv*` é a que mais pega gente.** `bv` exige que o stream
**não tenha áudio nenhum**. Se um site só oferece formatos combinados, `bv` não acha
nada e falha. `bv*` aceita ambos, então é mais robusto. **Use `bv*`.**

### Camada 3 — os três operadores

| Operador | Nome | O que faz | Exemplo |
|---|---|---|---|
| `+` | merge | baixa os dois e junta num arquivo (**exige ffmpeg**) | `bv*+ba` |
| `/` | fallback | tenta o da esquerda; se falhar, o da direita | `bv*+ba/b` |
| `,` | múltiplos | baixa **todos**, gerando vários arquivos | `bv,ba` |

⚠️ **Nunca use `,` neste projeto.** Ele gera múltiplos arquivos por job, e sua fila
assume um arquivo por job. Isso deve ser validado no domínio (T2): se o seletor
resolvido contiver `,`, é erro de configuração do perfil.

O `/` é o que dá robustez. Leia `bv*[height<=1080]+ba/b[height<=1080]/b` como:

1. tente 1080p só-vídeo + melhor áudio, juntados;
2. se não der, tente o melhor combinado até 1080p;
3. se não der, tente qualquer melhor combinado.

Sempre termine a cadeia com um fallback simples (`/b`), senão vídeos com formatos
incomuns falham com "Requested format is not available".

### Camada 4 — os filtros entre colchetes

📗 Campos **numéricos**, comparáveis com `<`, `<=`, `>`, `>=`, `=`, `!=`:

`filesize`, `filesize_approx`, `width`, `height`, `aspect_ratio`, `tbr` (bitrate total,
kbps), `abr` (áudio), `vbr` (vídeo), `asr` (sample rate, Hz), `fps`, `audio_channels`,
`stretched_ratio`.

📗 Campos **de texto**, comparáveis com `=`, `^=` (começa com), `$=` (termina com),
`*=` (contém), `~=` (regex):

`ext`, `acodec`, `vcodec`, `container`, `protocol`, `language`, `dynamic_range`,
`format_id`, `format`, `format_note`, `resolution`, `url`.

Prefixe com `!` para negar: `[vcodec!^=avc1]`.

⚠️ **A pegadinha do `?`:** 📗 "Formats for which the value is not known are excluded
unless you put a question mark (`?`) after the operator." Ou seja, `[height<=1080]`
**descarta** formatos cuja altura o site não informou. `[height<=?1080]` os mantém.
Para vídeo de gaming no YouTube a altura sempre vem, então eu usei a forma estrita nos
perfis — mas se você adicionar suporte a outro site e ele começar a falhar, é aqui que
você olha primeiro.

### Camada 5 — `format` (regra dura) vs `format_sort` (preferência)

Essa distinção é a coisa mais útil desta seção inteira.

- **`format`** é um **filtro eliminatório**. `[height<=1080]` remove tudo acima de 1080p.
  Se sobrar nada, falha.
- **`format_sort`** (`-S` na CLI) é uma **ordem de preferência**. Ele nunca elimina
  nada; só decide quem é "melhor". Se a preferência não existir, ele pega a próxima.

📗 A ordem padrão do `format_sort` é:
`lang,quality,res,fps,hdr:12,vcodec,channels,acodec,size,br,asr,proto,ext,hasaud,source,id`

**A regra que eu recomendo:** ponha no `format` só o que é **inegociável** (limite de
resolução), e no `format_sort` o que é **desejável** (codec H.264, container mp4).
Assim o perfil nunca quebra por causa de uma preferência — ele degrada.

## 2.2 Os quatro perfis

🧪 Todos os quatro seletores abaixo foram validados com
`YoutubeDL.build_format_selector()` na sua máquina — **os quatro parseiam sem erro**.
Isso valida a *sintaxe*, não o resultado (que depende dos formatos que o site oferece).

### `edicao_1080` — o cavalo de batalha

```yaml
edicao_1080:
  format: "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
  format_sort: ["res:1080", "vcodec:h264", "acodec:aac", "fps"]
  merge_output_format: "mp4"
```

Dissecando o `format`, ramo por ramo:

| Pedaço | O que faz |
|---|---|
| `bv*` | melhor stream que contém vídeo |
| `[height<=1080]` | descarta acima de 1080p |
| `[vcodec^=avc1]` | codec de vídeo **começa com** `avc1` — isto é, H.264 |
| `+` | junta com... |
| `ba[acodec^=mp4a]` | ...melhor áudio cujo codec começa com `mp4a` (AAC) |
| `/bv*[height<=1080]+ba` | **se não houver H.264**, aceita qualquer codec em 1080p |
| `/b[height<=1080]/b` | último recurso: combinado pronto |

**Por que `avc1` e `mp4a`?** Porque você edita. 🔬 Os valores reais de `vcodec` no
YouTube são strings como `avc1.640028`, `vp9`, `av01.0.08M.08` — por isso `^=` (começa
com) e não `=`. H.264 (`avc1`) em container MP4 é o que Premiere, After Effects e
Resolve abrem nativamente e com scrub decente. VP9 e AV1 em WebM funcionam, mas com
performance de timeline visivelmente pior e, no Premiere, historicamente problemática.
**Essa é uma decisão de editor, não de programador** — e é exatamente o tipo de coisa
que um perfil nomeado deve carregar para você não ter que lembrar.

### `edicao_4k` — e a pegadinha que você precisa saber

```yaml
edicao_4k:
  format: "bv*[height<=2160]+ba/b[height<=2160]/b"
  format_sort: ["res:2160", "fps", "vcodec:vp9", "acodec:aac"]
  merge_output_format: "mkv"
```

⚠️ **Repare que aqui eu NÃO filtrei por `avc1`.** Motivo: **o YouTube não serve H.264
acima de 1080p.** De 1440p para cima só existe VP9 e AV1. Se eu copiasse o filtro
`[vcodec^=avc1]` do perfil 1080, o primeiro ramo nunca casaria e o perfil silenciosamente
cairia para o fallback — você pediria 4K e receberia 1080p sem entender por quê.

Consequência para você como editor: **4K do YouTube sempre vem em VP9/AV1.** Se a
timeline engasgar, o caminho é transcodificar para um mezzanine (DNxHR/ProRes) depois —
o que é trabalho de ffmpeg, não deste projeto.

Usei `mkv` no merge porque MKV aceita qualquer combinação de codecs sem reclamar; MP4
com VP9 é tecnicamente válido mas mal suportado.

### `so_audio`

```yaml
so_audio:
  format: "ba/b"
  postprocessors:
    - key: "FFmpegExtractAudio"
      preferredcodec: "m4a"
      preferredquality: "0"
```

`ba/b` = melhor só-áudio, ou qualquer coisa se não houver só-áudio. O postprocessor
`FFmpegExtractAudio` descarta o vídeo e deixa só a trilha. 📗 `preferredquality: "0"`
significa melhor qualidade VBR.

**Escolhi `m4a` (AAC) e não `mp3`** porque o YouTube já entrega AAC ou Opus; converter
para MP3 é uma segunda geração de perda sem ganho nenhum. Com `m4a`, quando o áudio-fonte
já é AAC, o ffmpeg faz *copy* do stream — sem reencodar. Isso é rápido e sem perda.

### `preview_leve`

```yaml
preview_leve:
  format: "bv*[height<=480]+ba/b[height<=480]/b"
  format_sort: ["res:480", "+size"]
  merge_output_format: "mp4"
```

O `+size` no sort é o detalhe: 📗 o prefixo `+` **inverte** a ordenação, então `+size`
prefere o **menor** arquivo. É para bater o olho e decidir se vale baixar em qualidade
cheia.

📗 O README recomenda explicitamente `-S +size` em vez de `-f worst`, porque `worst`
escolhe o pior em *todos* os aspectos (pode te dar 144p com áudio horrível), enquanto
`+size` te dá o menor arquivo dentro do limite que você pôs.

## 2.3 O que acontece se o ffmpeg não existir

📗 "if ffmpeg is unavailable [...] the default becomes `-f best/bestvideo+bestaudio`".

Sem ffmpeg, o operador `+` não funciona — e como todo perfil de edição depende de `+`,
**sem ffmpeg você fica limitado aos formatos combinados**, tipicamente 720p ou menos.
É por isso que a restrição técnica nº 2 do projeto (avisar na interface) importa tanto:
sem esse aviso, o sintoma que você veria é "pedi 1080p e veio 720p", que é muito difícil
de diagnosticar sem saber a causa.

---

# 3. Progress hooks — e a pergunta da thread

## 3.1 Assinatura

```python
def meu_hook(d: dict) -> None: ...


opcoes = {"progress_hooks": [meu_hook]}
```

Uma função, um argumento (um `dict`), retorno ignorado. É uma lista, então dá para
registrar vários.

## 3.2 As chaves do dicionário

🔬 `YoutubeDL.py:398-421` (docstring), verificado contra `downloader/common.py`:

| Chave | Sempre presente? | Conteúdo |
|---|---|---|
| `status` | **Sim** | `'downloading'`, `'finished'` ou `'error'` |
| `info_dict` | **Sim** | o info_dict do vídeo |
| `filename` | quando `downloading`/`finished` | nome final do arquivo |
| `tmpfilename` | geralmente | o `.part` em que está escrevendo |
| `downloaded_bytes` | geralmente | bytes já no disco |
| `total_bytes` | **não** | tamanho total, `None` se desconhecido |
| `total_bytes_estimate` | **não** | estimativa, `None` se indisponível |
| `speed` | **não** | bytes/segundo, `None` se desconhecido |
| `eta` | **não** | segundos restantes, `None` se desconhecido |
| `elapsed` | geralmente | segundos desde o início |
| `fragment_index` / `fragment_count` | só em download fragmentado | contador de fragmentos |

⚠️ **A regra de ouro:** 🔬 a própria docstring diz *"Check this first and ignore unknown
values"* sobre o `status`, e marca quase tudo como *"may also be present"*. Trate o
dicionário como **não confiável**: use `d.get('speed')`, nunca `d['speed']`. Um
`KeyError` dentro de um progress hook derruba o download inteiro, porque ele é chamado
no meio do laço de escrita.

🔬 Garantia útil: *"Progress hooks are guaranteed to be called at least once (with status
'finished') if the download is successful."* Ou seja, você sempre recebe um evento final
em caso de sucesso — dá para fechar o job com segurança nele.

⚠️ Um detalhe que engana: 🔬 em `downloader/common.py:449-453`, se o arquivo **já existe**,
o yt-dlp dispara direto um `status: 'finished'` **sem nenhum `'downloading'` antes**. Sua
máquina de estados não pode assumir que sempre passa por "baixando".

## 3.3 Frequência

🔬 O hook é chamado a cada bloco lido do socket, com um *throttle* controlado pelo
parâmetro `progress_delta` (padrão: sem throttle). Na prática, isso significa **muitas
chamadas por segundo** numa conexão rápida.

**Consequência de projeto:** o hook não pode fazer nada caro. Nada de escrever no SQLite
a cada chamada — você teria centenas de escritas por segundo por download. O hook deve
apenas atualizar um estado em memória; a persistência acontece nas transições
(`finished`, `error`).

## 3.4 🔴 De qual thread o hook é chamado

Esta é a pergunta que você marcou como importante, e a resposta honesta tem quatro casos.
Eu segui o caminho no código-fonte.

🔬 `downloader/common.py:488-495` — `_hook_progress` simplesmente itera e chama:

```python
def _hook_progress(self, status, info_dict):
    status["info_dict"] = info_dict
    for ph in self._progress_hooks:
        ph(status)
```

Não há fila, não há marshalling, não há `call_soon_threadsafe`. **O hook roda
sincronamente na thread que estiver executando o download naquele momento.** Então a
pergunta vira: qual thread é essa?

| Caso | Downloader | Thread que chama o hook |
|---|---|---|
| **1.** MP4/WebM progressivo, DASH não-fragmentado | `http.py` | 🟢 A thread que chamou `ydl.download()` |
| **2.** Fragmentado (HLS/DASH) com `concurrent_fragment_downloads=1` (**padrão**) | `fragment.py`, ramo sequencial | 🟢 A thread que chamou `ydl.download()` |
| **3.** Fragmentado com `concurrent_fragment_downloads>1` | `fragment.py` + `ThreadPoolExecutor` | 🔴 Threads do pool |
| **4.** Múltiplos formatos fragmentados em paralelo (DASH vídeo+áudio) | `download_and_append_fragments_multiple` | 🔴 Uma thread por formato |

Evidência de cada um:

- **Caso 3:** 🔬 `fragment.py:485-493`.
  `max_workers = ceil(concurrent_fragment_downloads / max_progress)`; se `max_workers > 1`,
  os fragmentos vão para um `ThreadPoolExecutor` e o hook dispara dentro dos workers.
  🔬 `options.py:1013` confirma que o **padrão de `concurrent_fragment_downloads` é `1`**,
  então esse caso só ocorre se você ligar explicitamente.
- **Caso 4:** 🔬 `fragment.py:367-415` — `download_and_append_fragments_multiple` cria um
  `ThreadPoolExecutor` **por formato** e submete `thread_func`. 🔬 `dash.py:68` chama essa
  função. Ou seja: **um DASH com vídeo e áudio separados pode baixar os dois em paralelo,
  em duas threads, cada uma chamando o seu hook.** Este caso **não** depende de você ligar
  nada.

### 🎯 Conclusão de projeto

**Você deve escrever o hook como se ele fosse chamado de outra thread, sempre.**

Os casos 1 e 2 cobrem a maioria dos downloads de YouTube, mas o caso 4 é real, não
requer configuração especial, e vai acontecer com conteúdo DASH. Um hook que assume
thread única vai corromper o estado da fila de forma intermitente e praticamente
impossível de reproduzir — o pior tipo de bug.

Isso significa, concretamente, para o T5:

1. O estado de progresso de um job protegido por um `threading.Lock`, ou guardado num
   objeto que só é **substituído** atomicamente (rebind de atributo é atômico no
   CPython; mutação de dict não é).
2. O hook faz o mínimo: calcula e guarda. Nenhuma I/O, nenhum SQLite, nenhum
   `print` — 🧪 e, no seu caso específico, **nenhum `print` mesmo** (veja Seção 7.4:
   imprimir título de vídeo no console do Windows levanta `UnicodeEncodeError`).
3. O endpoint `GET /api/fila` lê esse estado sob o mesmo lock.
4. Como o caso 4 dispara hooks de **dois formatos concorrentes para o mesmo job**, os
   `downloaded_bytes` que chegam não são um contador global do job — são por stream. A
   barra de progresso precisa lidar com isso (o campo `info_dict` do hook permite
   distinguir a origem).

> Nota sobre a restrição nº 4 do projeto ("um download por vez"): ela vale para **jobs**.
> Ela não impede o yt-dlp de usar threads internamente dentro de um job — e, como o caso 4
> mostra, ele usa. São coisas diferentes e é importante não confundir.

---

# 4. Postprocessors

Postprocessors rodam **depois** do download, em cadeia. São declarados como lista de
dicionários com uma chave `key` (o nome da classe sem o sufixo `PP`).

```python
'postprocessors': [
    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a'},
]
```

🔬 `postprocessor/__init__.py:52-53` — a resolução do nome é literalmente
`postprocessors.value[key + 'PP']`. Um `key` errado só falha em tempo de execução, com
`KeyError`. **Isso é argumento para validar os nomes de postprocessor no domínio (T2),
ao carregar o YAML** — melhor falhar ao ler a config do que no meio de um job.

## 4.1 O que precisa de ffmpeg

| Operação | `key` | Precisa de ffmpeg? | Observações |
|---|---|---|---|
| **Merge vídeo+áudio** | *(automático)* | 🔴 **Sim** | Não é um `key` que você declara — é acionado pelo operador `+` no seletor |
| **Extrair áudio** | `FFmpegExtractAudio` | 🔴 **Sim** | Faz *copy* sem reencodar quando o codec de destino já é o da fonte |
| **Embutir metadados** | `FFmpegMetadata` | 🔴 **Sim** | Grava título, canal, data nos tags do container |
| **Converter container** | `FFmpegVideoRemuxer` | 🔴 **Sim** | Só troca o container, sem reencodar |
| **Converter codec** | `FFmpegVideoConvertor` | 🔴 **Sim** | Reencoda. Lento. |
| **Embutir thumbnail** | `EmbedThumbnail` | 🟡 **Depende** | Ver abaixo |
| **Baixar thumbnail como arquivo** | *(opção `writethumbnail`)* | 🟢 **Não** | É só um GET; não é postprocessor |
| **Mover arquivos ao final** | `MoveFilesAfterDownload` | 🟢 **Não** | Python puro |

**O caso do `EmbedThumbnail`** 🔬 `postprocessor/embedthumbnail.py`: o requisito muda
conforme o container de destino.

- `mkv`/`mka`: usa ffmpeg (`-attach`) — linha 98-113
- `mp4`/`m4a`/`m4v`/`mov`: tenta **`mutagen`** (biblioteca Python pura) primeiro; se não
  houver, tenta o binário externo **`AtomicParsley`**; e só então cai para ffmpeg — linhas 115-165
- 🔬 Linha 83: se o container não é mkv/mka, a thumbnail **tem que ser** jpg/jpeg/png

**Recomendação para o seu caso:** embutir thumbnail é enfeite, não é necessidade de
footage para edição — e adiciona uma dependência opcional (`mutagen`) e um caminho de
falha a mais. Eu deixaria de fora. Se quiser a thumbnail, `writethumbnail: True` salva
como arquivo separado, não precisa de ffmpeg nenhum, e para catalogar footage um `.jpg`
ao lado é até mais útil que um tag embutido.

## 4.2 `merge_output_format`

Não é postprocessor, é opção — mas é o par natural do `+`:

```python
'merge_output_format': 'mp4'   # ou 'mkv'
```

Diz em que container o merge deve resultar. Sem isso, o yt-dlp escolhe sozinho e você
pode receber `.webm` onde esperava `.mp4`. Para nomenclatura previsível (T3), **fixe
isso em cada perfil.**

---

# 5. Detectar o ffmpeg no PATH, no Windows

## 5.1 Como o yt-dlp faz (e por que não é o que você quer)

🔬 `postprocessor/ffmpeg.py:102-128` — `_determine_executables()`. Se você não passou
`ffmpeg_location`, ele retorna literalmente `{'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe'}`,
ou seja, **delega a resolução ao PATH do sistema no momento do `subprocess`**. A checagem
real de existência acontece em `_get_ffmpeg_version`, que **executa o binário** com
`-bsfs` e faz parse da saída.

Isso é robusto (confirma que o binário roda de verdade) mas custa criar um processo.

## 5.2 As duas opções, e a que eu recomendo

**Opção A — `shutil.which` (biblioteca padrão):**

```python
import shutil

caminho = shutil.which("ffmpeg")  # None se não achar
```

🧪 Na sua máquina retorna:
```
C:\Users\Pichau\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\ffmpeg.EXE
```

`shutil.which` no Windows já respeita `PATHEXT` automaticamente — por isso ele acha
`ffmpeg.EXE` mesmo você tendo pedido `ffmpeg`. **Não** monte o nome com `.exe` na mão;
é justamente o que `which` existe para resolver.

**Opção B — perguntar ao próprio yt-dlp:**

```python
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

pp = FFmpegPostProcessor(ydl)
pp.available  # bool
FFmpegPostProcessor.get_versions(ydl)
```

🧪 Na sua máquina:
```
pp.available       : True
pp.basename        : ffmpeg
pp.probe_available : True
get_versions       : {'ffmpeg': '9.0-full_build-www.gyan.dev', 'ffprobe': '9.0-full_build-www.gyan.dev'}
```

**Recomendação: use a Opção A (`shutil.which`)** para a checagem de inicialização que
alimenta o aviso na interface e o `GET /api/config`.

Justificativa: é instantânea, não cria processo, não depende de API interna do yt-dlp
(`FFmpegPostProcessor` não é API pública documentada e pode mudar entre versões), e é
trivial de simular nos testes com `monkeypatch`. A Opção B te dá a **versão**, que é
informação bonita mas que não muda nenhuma decisão sua.

⚠️ **Cheque `ffprobe` também.** Vários postprocessors o usam, e ele é distribuído junto
mas nem sempre está no mesmo lugar. Um aviso que diz "ffmpeg ok" enquanto `ffprobe`
falta produz erro confuso lá na frente.

⚠️ E cheque **na inicialização, uma vez** — não a cada job. `shutil.which` toca o disco.

---

# 6. Exceções: distinguindo os motivos de falha

## 6.1 A hierarquia

🔬 `utils/_utils.py:969-1190` e `networking/exceptions.py`:

```
Exception
└── YoutubeDLError                      (raiz de tudo do yt-dlp)
    ├── ExtractorError                  (falha ao extrair metadados)
    │   ├── UnsupportedError            → site não suportado    [tem .url]
    │   ├── GeoRestrictedError          → bloqueio regional     [tem .countries]
    │   ├── RegexNotFoundError          → extractor quebrou (site mudou)
    │   └── UserNotLive
    ├── DownloadError                   ← O QUE VOCÊ VAI PEGAR  [tem .exc_info]
    ├── PostProcessingError             (falha no ffmpeg, etc.)
    ├── UnavailableVideoError
    ├── DownloadCancelled
    └── RequestError                    (rede)
        ├── HTTPError                   [tem .status, .reason]
        └── TransportError
            ├── IncompleteRead, SSLError, CertificateVerifyError, ProxyError
```

## 6.2 🔑 O detalhe que muda tudo: `DownloadError` embrulha a exceção original

🔬 `YoutubeDL.py`, método `trouble()`:

```python
if not self.params.get("ignoreerrors"):
    if (
        sys.exc_info()[0]
        and hasattr(sys.exc_info()[1], "exc_info")
        and sys.exc_info()[1].exc_info[0]
    ):
        exc_info = sys.exc_info()[1].exc_info
    else:
        exc_info = sys.exc_info()
    raise DownloadError(message, exc_info)
```

Quase tudo que dá errado passa por `trouble()` e sai como **`DownloadError`**. A exceção
real fica preservada em **`err.exc_info`**, que é uma tupla `(tipo, valor, traceback)`.

**Portanto o padrão correto no adapter (T1) é:**

```python
try:
    info = ydl.extract_info(url, download=False)
except yt_dlp.utils.DownloadError as err:
    original = err.exc_info[1] if err.exc_info else None
    # classifica a partir de `original`, não de `err`
```

Se você classificar olhando só o `DownloadError`, **tudo vira "erro genérico"** — é o
erro clássico de quem integra o yt-dlp pela primeira vez.

## 6.3 O que dá para distinguir por TIPO e o que só dá por MENSAGEM

Aqui preciso ser direto sobre uma limitação: **o yt-dlp não tem uma exceção por motivo
de indisponibilidade.** "Vídeo privado", "removido" e "restrito por idade" são todos
`ExtractorError` com mensagens diferentes.

**Por tipo (confiável):**

| Situação | Como detectar | Confiabilidade |
|---|---|---|
| Bloqueio regional | `isinstance(orig, GeoRestrictedError)` | 🟢 Alta — 🔬 `common.py:1233` levanta essa classe especificamente |
| Site não suportado | `isinstance(orig, UnsupportedError)` | 🟢 Alta |
| Falha de rede | `isinstance(orig, (HTTPError, TransportError))` | 🟢 Alta |
| HTTP 404 / 403 | `orig.status` no `HTTPError` | 🟢 Alta |

**Por mensagem (frágil):**

| Situação | Substring | 🔬 Origem |
|---|---|---|
| Privado | `"Private video"` | `youtube/_video.py:197` |
| Indisponível/removido | `"Video unavailable"` | `youtube/_video.py:901` |
| Restrição de idade | `"age-restricted"` / `"confirm your age"` | `youtube/_video.py:2894, 3149, 3157` |
| Exige login | `"only available for registered users"` / `"sign in"` | `common.py` `raise_login_required` |
| **DRM** | `"This video is DRM protected"` | `common.py:1228` `report_drm` |
| Rate limit | `"This content isn't available, try again later"` | `youtube/_video.py:4056` |

⚠️ **Essas mensagens vêm do site, não do yt-dlp** — 🔬 em `_video.py:4040-4045` o texto é
lido do `playerErrorMessageRenderer` do JSON do YouTube. **O YouTube pode mudar esse
texto a qualquer momento, e você não controla isso.**

**Consequência de projeto:** a classificação por mensagem precisa ser (a) uma **tabela
de dados**, não uma cascata de `if`, (b) com um **fallback obrigatório** que mostra a
mensagem original em vez de "erro desconhecido", e (c) **testada com strings fixas**
(não com a rede). Quando o YouTube mudar o texto, você conserta uma linha de tabela e o
usuário nunca fica sem informação, porque o fallback mostrou o original.

## 6.4 Tradução sugerida para português

| Classificação | Mensagem legível | Vale a pena tentar de novo? |
|---|---|---|
| `INDISPONIVEL` | "Vídeo indisponível ou removido." | Não |
| `PRIVADO` | "Vídeo privado. Só o dono tem acesso." | Não |
| `RESTRICAO_IDADE` | "Vídeo com restrição de idade — exige conta autenticada." | Não |
| `BLOQUEIO_REGIONAL` | "Bloqueado na sua região" + países de `.countries` | Não |
| `DRM` | "Conteúdo protegido por DRM. **Fora do escopo desta ferramenta.**" | Não |
| `SITE_NAO_SUPORTADO` | "Este site não é suportado pelo yt-dlp." | Não |
| `REDE` | "Falha de rede (HTTP {status})." | **Sim** |
| `RATE_LIMIT` | "YouTube limitou a taxa de requisições. Aguarde." | **Sim**, com espera |
| `SEM_FFMPEG` | "ffmpeg não encontrado — não é possível juntar vídeo e áudio." | Não |
| `DESCONHECIDO` | mensagem original do yt-dlp, sem mascarar | — |

> 📌 O `DRM` merece nota: ele se conecta direto ao "fora de escopo, definitivo" da Fase 1.
> A ferramenta **detecta** DRM para dar uma mensagem honesta e parar; ela não faz e não
> fará nada para contornar. Vale registrar isso no SPEC quando chegar a Fase 4.

---

# 7. Nomes de arquivo no Windows

Esta seção é a que mais justifica ter uma camada de domínio própria. Eu medi tudo na
sua máquina.

## 7.1 A regra documentada 📗

Da Microsoft (*Naming Files, Paths, and Namespaces*):

**Caracteres proibidos:** `<` `>` `:` `"` `/` `\` `|` `?` `*` e os caracteres de
controle 0–31.

**Nomes reservados:** `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, e
também `COM¹ ² ³` / `LPT¹ ² ³` (os superscripts ISO-8859-1 contam como dígito).

> 📗 Citação relevante: *"Also avoid these names followed immediately by an extension;
> for example, NUL.txt and NUL.tar.gz are both equivalent to NUL."*

**Fim do nome:** 📗 *"Do not end a file or directory name with a space or a period."*

**Tamanho:** `MAX_PATH` = **260** caracteres para o caminho completo. Cada *componente*
é limitado pelo sistema de arquivos (NTFS: **255**).

## 7.2 🧪 O que a sua máquina realmente faz (e onde diverge)

| Teste | Resultado medido |
|---|---|
| Criar `CON.mp4` | ✅ criou arquivo real, legível, aparece no `listdir` |
| Criar `CON` (sem extensão) | ✅ criou arquivo real, legível |
| Criar `COM1.mp4` | ✅ criou arquivo real |
| Criar `aux.txt` | ✅ criou arquivo real |
| **Criar `NUL` (sem extensão)** | 🔴 **"sucesso" aparente, mas ver abaixo** |
| `video..mp4`, `video .mp4`, `   .mp4` | ✅ aceitos |
| Emoji `🎮.mp4` | ✅ aceito |
| Nome com 250 chars (caminho total 388) | ✅ aceito (`LongPathsEnabled=1`) |
| Nome com 300 chars | ❌ `OSError [Errno 22] Invalid argument` |

## 7.3 🔴 O achado que importa: `NUL` causa perda silenciosa de dados

Este foi o resultado do teste com `NUL`:

```
NUL   escreveu=OK  lido=b''  exists=True  aparece_no_listdir=False
```

Leia com atenção: eu escrevi `b"conteudo-de-teste"`, **nenhuma exceção foi levantada**,
`exists()` retornou `True`, e ao reler eu recebi **string vazia**. O arquivo **não
aparece na listagem do diretório**. Os bytes foram para o dispositivo nulo do DOS e
sumiram.

**Esse é o pior modo de falha possível para o seu projeto.** Traduzindo para o fluxo
real: um vídeo cujo título sanitizado vire `NUL` seria "baixado com sucesso", o
progress hook dispararia `finished`, o histórico registraria concluído com um caminho —
e o arquivo não existiria. Você só descobriria na hora de montar, com o projeto aberto e
o prazo correndo.

**Sobre a divergência:** a regra documentada da Microsoft diz que `NUL.txt` ≡ `NUL`, mas
🧪 no build 26200 o `NUL.mp4` criou arquivo real. Ou seja, o Windows 11 relaxou parte do
comportamento legado. **Isso não é motivo para relaxar o seu código**, por três razões:
o comportamento varia por build do Windows; o material vai para HD externo, NAS e
máquina de cliente, que podem ser outros Windows; e o custo de tratar a lista inteira é
uma função de dez linhas.

**Recomendação:** trate o conjunto reservado **completo**, com e sem extensão,
case-insensitive. Não confie no que a sua máquina permite hoje.

## 7.4 🧪 O que o `sanitize_filename` do yt-dlp faz — e o que ele NÃO faz

Medido na sua instalação:

| Entrada | Saída do yt-dlp | Veredito |
|---|---|---|
| `Gameplay: "Rush B" <CS2> \| 100%? *melhor*` | `Gameplay： ＂Rush B＂ ＜CS2＞ ｜ 100%？ ＊melhor＊` | ⚠️ legal, mas ver abaixo |
| `Highlights 2024/2025 \ Final` | `Highlights 2024⧸2025 ⧹ Final` | ⚠️ idem |
| `CON` | `CON` | 🔴 **não trata** |
| `NUL` | `NUL` | 🔴 **não trata** |
| `''` (vazio) | `''` (vazio) | 🔴 **nome vazio** |
| `'   '` (só espaços) | `'   '` | 🔴 não trata |
| `'...'` | `'...'` | 🔴 não trata |
| `'Final da season.'` | `'Final da season.'` | 🔴 ponto final preservado |
| `'A' * 300` | 300 chars | 🔴 **não trunca** |
| `🎮🔥💀` | `🎮🔥💀` | ⚠️ passa (título só-emoji vira nome só-emoji) |
| Caracteres de controle | removidos | 🟢 trata |

🔬 Confirmei com `grep` em todo o pacote: **não existe nenhum tratamento de nomes
reservados do Windows no yt-dlp.** A busca por `CON`/`PRN`/`LPT1`/`COM1` no código só
retorna `_RESERVED_NAMES` do extractor do YouTube, que é sobre nomes de URL de canal —
coisa completamente diferente.

**A substituição por caracteres de largura total merece explicação.** 🧪 Os codepoints
que ele gera são `U+FF1A` (：), `U+FF1C/FF1E` (＜＞), `U+FF5C` (｜), `U+FF0A` (＊),
`U+FF02` (＂), `U+FF1F` (？), `U+29F8` (⧸), `U+29F9` (⧹). São caracteres Unicode
diferentes que *parecem* os proibidos. O arquivo é válido, mas:

- Você não consegue digitar esse nome no teclado para buscar o arquivo
- Busca por "Rush B" com aspas normais **não encontra** o arquivo
- 🧪 **Imprimir esse nome no console do Windows levanta `UnicodeEncodeError`**, porque o
  console usa cp1252. Eu bati nisso literalmente ao rodar os testes desta pesquisa:

  ```
  UnicodeEncodeError: 'charmap' codec can't encode character '\uff1a'
  ```

  ⚠️ **Isso vai acontecer no `spike.py` da Fase 3** ao imprimir títulos. A correção é
  `sys.stdout.reconfigure(encoding='utf-8')` no topo do arquivo.

## 7.5 Tamanho: os dois limites diferentes

São **dois** limites, e confundi-los leva a bug:

1. **Componente individual:** 255 caracteres (NTFS). 🧪 O erro com 300 chars foi
   `Errno 22` justamente por isso — não por `MAX_PATH`.
2. **Caminho completo:** 260 (`MAX_PATH`), **a menos que** long paths estejam ativados.

🧪 A sua máquina tem `LongPathsEnabled=1`, e por isso o caminho de 388 chars funcionou.

⚠️ **Não dependa disso.** Long paths precisam do registro **e** de o programa se declarar
compatível no manifesto. Premiere, After Effects e Resolve não são confiáveis nesse
ponto, e HD externo/NAS/máquina de cliente podem ter o registro desligado. **Footage que
o Windows aceita mas o Premiere não abre é pior do que um download que falha na hora.**

🔬 O yt-dlp tem a opção `trim_file_name`, mas ela é `False` por padrão e é um corte cego
de caracteres (`YoutubeDL.py:1550`: `no_ext[:trim_file_name]`) — não conhece o
comprimento do diretório de destino nem faz corte em fronteira de palavra.

**Orçamento recomendado:** trabalhe com um teto conservador (algo como 200 chars de
caminho completo), calculado **a partir da raiz do projeto de destino**, não do nome
isolado. Truncar tem que preservar a extensão e o sufixo de desambiguação.

## 7.6 🎯 Por que isso tudo é lógica SUA e não do yt-dlp

Resumindo os fatos medidos: `sanitize_filename` **não** trata nomes reservados, **não**
trunca por tamanho, **não** trata nome vazio, **não** trata ponto/espaço final, **não**
conhece o comprimento do diretório de destino, **não** sabe nada sobre colisão com
arquivo existente, e substitui caracteres por homoglifos Unicode que atrapalham busca e
quebram o console.

Ele foi feito para "gerar um nome que o SO aceita". O seu T3 precisa de algo diferente:
"gerar um nome que **um editor de vídeo** consegue achar, digitar, e abrir no Premiere,
seis meses depois, num HD externo". **São problemas diferentes**, e essa diferença é
exatamente a resposta ao risco central que você levantou — o domínio não fica vazio
porque o yt-dlp não resolve esse problema.

---

# 8. Obter a URL da thumbnail sem baixar o vídeo

🔬 Documentado em `extractor/common.py:292-303`. O info_dict traz **dois** campos:

**`thumbnail`** — uma string com a URL. É o atalho.

**`thumbnails`** — uma lista de dicionários, do pior para o melhor:

```python
{
    "id": "39",  # opcional
    "url": "https://...",  # sempre
    "ext": "jpg",  # opcional
    "preference": 1,  # opcional, int — qualidade
    "width": 1920,  # opcional
    "height": 1080,  # opcional
    "filesize": 12345,  # opcional
    "http_headers": {...},  # headers necessários para o GET
}
```

🔬 A lista é ordenada do **pior para o melhor**, então `thumbnails[-1]` é a de maior
qualidade.

**Como usar no `POST /api/inspecionar`:**

```python
info = ydl.extract_info(url, download=False)  # não baixa mídia
thumb = info.get("thumbnail")  # string ou None
```

Você devolve essa URL no JSON e o `<img src="...">` do navegador busca a imagem
**direto do CDN do site**. O seu backend nunca toca na imagem — sem proxy, sem cache,
sem custo de banda no seu processo.

⚠️ **Três cuidados:**

1. `thumbnail` pode ser `None`. Use `.get()` e tenha um placeholder no CSS.
2. Para preview de cartão você quer uma **média**, não a de 1920px. Filtrar a lista por
   `width` (ex.: a menor com `width >= 320`) carrega a grade muito mais rápido quando
   você cola dez links de uma vez.
3. 🔬 Algumas thumbnails exigem `http_headers` (Referer). Isso é irrelevante para o
   `<img>` do navegador na maioria dos casos, mas se alguma imagem vier quebrada, é aqui
   que está a causa.

📌 Não confundir com a opção `writethumbnail`, que **baixa** a imagem para o disco. Para
exibir na interface você não precisa dela — só da URL.

---

# 9. Resumo dos achados que mudam decisões de projeto

| # | Achado | Impacto |
|---|---|---|
| 1 | O hook pode ser chamado de **threads diferentes** (DASH multi-formato, sem config especial) | T5 precisa de lock desde o início — não é otimização prematura |
| 2 | `DownloadError` embrulha a exceção real em `.exc_info` | T1 tem que desembrulhar, senão tudo vira "erro genérico" |
| 3 | Motivos de indisponibilidade só distinguíveis **por mensagem**, e a mensagem vem do site | T1 precisa de tabela de dados + fallback que mostra o original |
| 4 | `sanitize_filename` **não** trata nomes reservados, tamanho, nome vazio, ponto final | T3 é lógica genuinamente sua — o risco de "domínio vazio" não se aplica |
| 5 | 🔴 `NUL` grava no vazio **sem erro** | Falha silenciosa; a sanitização é questão de correção, não de estilo |
| 6 | Console do Windows é cp1252 e quebra com a saída do sanitizador | `sys.stdout.reconfigure(encoding='utf-8')` no spike e na CLI |
| 7 | YouTube não serve H.264 acima de 1080p | `edicao_4k` não pode filtrar por `avc1`, senão degrada em silêncio |
| 8 | `ignoreerrors` é `False` por padrão na API | Comportamento já é o desejado; não mexer |
| 9 | Sem ffmpeg, o `+` não funciona → máximo ~720p | O aviso na interface evita um diagnóstico muito difícil |
| 10 | `extract_info` percorre playlists inteiras | Validação de link no domínio precisa rejeitar playlist/canal |

## Perguntas em aberto para você decidir (Fase 4, não agora)

1. **Container do `edicao_4k`:** propus `mkv` (aceita VP9/AV1 sem atrito). Se o seu
   fluxo exige `.mp4`, dá para forçar, mas com ressalvas de compatibilidade.
2. **Embutir thumbnail:** recomendei **não** (dependência extra, pouco valor para
   footage). `writethumbnail` como arquivo `.jpg` ao lado é a alternativa barata.
3. **Teto de caminho:** sugeri 200 chars como orçamento conservador. Você conhece a
   profundidade real das suas pastas de projeto melhor que eu.

---

# Fontes

**Documentação oficial**

- yt-dlp — README (raiz): https://github.com/yt-dlp/yt-dlp
- yt-dlp — Format Selection: https://github.com/yt-dlp/yt-dlp#format-selection
- yt-dlp — Sorting Formats: https://github.com/yt-dlp/yt-dlp#sorting-formats
- yt-dlp — Embedding yt-dlp: https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp
- yt-dlp — Output Template: https://github.com/yt-dlp/yt-dlp#output-template
- yt-dlp — README bruto (usado nesta pesquisa): https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/README.md
- yt-dlp — Wiki: https://github.com/yt-dlp/yt-dlp/wiki
- Microsoft Learn — Naming Files, Paths, and Namespaces: https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
- Microsoft Learn — Maximum Path Length Limitation: https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
- Python — `shutil.which`: https://docs.python.org/3/library/shutil.html#shutil.which
- FFmpeg — documentação: https://ffmpeg.org/documentation.html

**Código-fonte lido (yt-dlp 2026.07.04, instalação local)**

| Arquivo | Linhas | Assunto |
|---|---|---|
| `yt_dlp/YoutubeDL.py` | 183-560 | dicionário de opções (docstring) |
| `yt_dlp/YoutubeDL.py` | 398-421 | contrato dos `progress_hooks` |
| `yt_dlp/YoutubeDL.py` | `trouble()` | onde nasce o `DownloadError` |
| `yt_dlp/YoutubeDL.py` | 1209-1224, 1547-1550 | sanitização de caminho e `trim_file_name` |
| `yt_dlp/downloader/common.py` | 428-495 | `_hook_progress`, `status: finished` antecipado |
| `yt_dlp/downloader/http.py` | 172, 300, 349 | pontos de disparo do hook |
| `yt_dlp/downloader/fragment.py` | 255-320 | agregação de progresso por fragmento |
| `yt_dlp/downloader/fragment.py` | 367-415 | 🔴 `ThreadPoolExecutor` por formato |
| `yt_dlp/downloader/fragment.py` | 478-512 | 🔴 pool de fragmentos concorrentes |
| `yt_dlp/downloader/dash.py` | 68 | quem aciona o caminho multi-formato |
| `yt_dlp/options.py` | 1013 | `concurrent_fragment_downloads` padrão = 1 |
| `yt_dlp/utils/_utils.py` | 969-1190 | hierarquia de exceções |
| `yt_dlp/utils/_utils.py` | `sanitize_filename` | substituição por largura total |
| `yt_dlp/networking/exceptions.py` | 11-99 | exceções de rede |
| `yt_dlp/postprocessor/ffmpeg.py` | 102-236 | detecção de ffmpeg/ffprobe |
| `yt_dlp/postprocessor/embedthumbnail.py` | 44-165 | mutagen / AtomicParsley / ffmpeg |
| `yt_dlp/postprocessor/__init__.py` | 38-69 | resolução do nome em `key` |
| `yt_dlp/extractor/common.py` | 292-303 | campos `thumbnail` / `thumbnails` |
| `yt_dlp/extractor/common.py` | 1228-1240 | `report_drm`, `raise_geo_restricted`, `raise_login_required` |
| `yt_dlp/extractor/youtube/_video.py` | 2894, 3149, 4020-4060 | mensagens reais de erro |

**Scripts de verificação** (no scratchpad da sessão, descartáveis): `check.py` (ffmpeg +
seletores), `san.py` (sanitização), `fs.py` / `fs2.py` (sistema de arquivos),
`perfis.py` (validação dos 4 perfis).
