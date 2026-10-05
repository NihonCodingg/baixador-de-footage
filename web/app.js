/* ============================================================
   BAIXADOR DE FOOTAGE — app.js
   JavaScript puro, sem dependências.

   Sumário:
     1. Constantes e utilidades
     2. Formatação (a API entrega valores crus)
     3. Camada de API  (fetch, sem invenção de dados)
     4. Estado da aplicação
     5. Boot / config
     6. Entrada e inspeção
     7. Preview
     8. Fila (render incremental + polling de 1s)
     9. Histórico
    10. Toasts, vazios, eventos
   ============================================================ */

'use strict';

/* ------------------------------------------------------------
   1. CONSTANTES E UTILIDADES
   ------------------------------------------------------------ */

var INTERVALO_POLLING = 1000;         // contrato: 1 s
var ESPERA_MAXIMA = 15000;            // teto do backoff quando a API cai
var LIMITE_HISTORICO = 100;
var CHAVE_ESCOLHAS = 'footage.escolhas';   // último perfil/projeto usados
var CHAVE_RASCUNHO = 'footage.rascunho';   // texto da caixa de links
var AVULSO = '__avulso';                   // opção de pasta digitada na hora

function $(sel) { return document.querySelector(sel); }

/* escapa texto antes de entrar em innerHTML (títulos têm caracteres livres) */
function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function debounce(fn, ms) {
  var t;
  return function () {
    var args = arguments, self = this;
    clearTimeout(t);
    t = setTimeout(function () { fn.apply(self, args); }, ms);
  };
}

/* ícones dos selos de estado: forma distinta para cada estado,
   para não depender só de cor */
var ICONES = {
  na_fila:      '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="6" cy="6" r="4.6"/><path d="M6 3.4V6l2 1.4"/></svg>',
  baixando:     '<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 1v6.2l2.4-2.4 1 1L6 9.4 2.6 5.8l1-1L6 7.2V1z"/><rect x="1.6" y="10.2" width="8.8" height="1.4" rx=".7"/></svg>',
  concluido:    '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2"><path d="M1.6 6.4 4.6 9.4 10.4 2.8"/></svg>',
  falhou:       '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2"><path d="M2.4 2.4l7.2 7.2M9.6 2.4 2.4 9.6"/></svg>',
  cancelado:    '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="6" cy="6" r="4.6"/><path d="M2.9 9.1 9.1 2.9"/></svg>',
  interrompido: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 1.4 11.2 10.6H.8z"/><path d="M6 4.6v2.6M6 8.8v.6"/></svg>'
};

var ROTULO_ESTADO = {
  na_fila: 'Na fila',
  baixando: 'Baixando',
  concluido: 'Concluído',
  falhou: 'Falhou',
  cancelado: 'Cancelado',
  interrompido: 'Interrompido'
};

/* motivos que valem uma nova tentativa (contrato §5) */
var MOTIVOS_RETENTAVEIS = { rede: true, rate_limit: true };

function selo(est, rotulo) {
  return '<span class="selo" data-estado="' + esc(est) + '">' +
    (ICONES[est] || '') + esc(rotulo || ROTULO_ESTADO[est] || est) + '</span>';
}

/* ------------------------------------------------------------
   2. FORMATAÇÃO
   ------------------------------------------------------------ */

/* 65 -> "1:05" ; 3725 -> "1:02:05" ; null -> "--:--" */
function fmtDuracao(s) {
  if (s === null || s === undefined) return '--:--';
  s = Math.round(s);
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), seg = s % 60;
  var mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  return (h > 0 ? h + ':' : '') + mm + ':' + String(seg).padStart(2, '0');
}

function num(v, casas) {
  return v.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
}

/* 9437184 -> "9,0 MB" ; null -> "--" */
function fmtBytes(b) {
  if (b === null || b === undefined) return '--';
  var un = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0, v = b;
  while (v >= 1024 && i < un.length - 1) { v /= 1024; i++; }
  return num(v, i === 0 ? 0 : 1) + ' ' + un[i];
}

function fmtVelocidade(bps) {
  if (bps === null || bps === undefined) return '--';
  return fmtBytes(bps) + '/s';
}

function fmtPercentual(p) {
  if (p === null || p === undefined) return null;
  return Math.round(p);
}

/* ISO-8601 UTC -> data/hora local */
function fmtDataHora(iso) {
  if (!iso) return '--';
  var d = new Date(iso);
  if (isNaN(d)) return '--';
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/* "20260901" -> "01/09/2026" */
function fmtDataUpload(s) {
  if (!s || s.length !== 8) return null;
  return s.slice(6, 8) + '/' + s.slice(4, 6) + '/' + s.slice(0, 4);
}

/* ------------------------------------------------------------
   3. CAMADA DE API
   Uma função só. A API é a ÚNICA fonte de dados desta tela: quando ela
   não responde, a tela diz isso. Nunca preenche o buraco com conteúdo
   inventado — footage que nunca existiu não pode aparecer aqui.
   ------------------------------------------------------------ */

function api(metodo, caminho, corpo) {
  var opcoes = { method: metodo, headers: { 'Accept': 'application/json' } };
  if (corpo !== undefined) {
    opcoes.headers['Content-Type'] = 'application/json';
    opcoes.body = JSON.stringify(corpo);
  }

  return fetch(caminho, opcoes).then(function (r) {
    var tipo = r.headers.get('content-type') || '';
    // resposta sem JSON: quem respondeu não é a API desta aplicação
    if (tipo.indexOf('json') === -1) return Promise.reject(quedaDaApi(r.status));
    return r.json().catch(function () { return {}; }).then(function (dados) {
      if (r.ok) return dados;
      // erro sempre na mesma forma: {erro: "..."} (+ detalhes no 422)
      if (r.status === 422 && dados.detalhes) console.warn('422 detalhes:', dados.detalhes);
      var e = new Error(dados.erro || 'Erro ' + r.status);
      e.status = r.status;
      e.detalhes = dados.detalhes || null;
      throw e;
    });
  }, function () {
    return Promise.reject(quedaDaApi(0));
  });
}

/* Falha de COMUNICAÇÃO (a API não respondeu), status 0 — diferente de um
   erro DA API, que vem com status e mensagem próprios. */
function quedaDaApi(status) {
  var e = new Error('A API em ' + location.host + ' não respondeu' +
    (status ? ' (HTTP ' + status + ').' : '.'));
  e.status = 0;
  return e;
}

/* Backoff: 1 s, 2, 4, 8, teto de 15 — a API caída não merece uma
   requisição por segundo. Enquanto ela estiver fora, um banner fixo diz
   isso: silêncio aqui é o que fazia a tela mentir. */
function esperaAtual() {
  var dobras = Math.max(0, estado.falhasApi - 1);
  return Math.min(INTERVALO_POLLING * Math.pow(2, dobras), ESPERA_MAXIMA);
}

/* Devolve true se era queda da API (e já a anunciou na tela). */
function registrarFalhaApi(e) {
  if (e.status !== 0) return false;
  estado.falhasApi++;
  $('#banner-api').hidden = false;
  $('#banner-api-msg').textContent = e.message +
    ' Tentando de novo a cada ' + Math.round(esperaAtual() / 1000) + ' s.';
  if (estado.falhasApi === 1) toast(e.message, 'erro');   // um toast, não um por tentativa
  ligarPolling();   // sonda até ela voltar, mesmo sem job na fila
  return true;
}

function registrarSucessoApi() {
  if (estado.falhasApi) {
    estado.falhasApi = 0;
    toast('Conexão com a API restabelecida.', 'ok');
  }
  $('#banner-api').hidden = true;
}

/* ------------------------------------------------------------
   4. ESTADO
   ------------------------------------------------------------ */

var estado = {
  config: null,
  preview: [],        // itens de /api/inspecionar + escolhas locais
  jobs: [],
  historico: [],
  urlPorJob: {},      // id do job -> url enviada (para "tentar de novo")
  pollAtivo: false,        // id do setTimeout do polling (não setInterval)
  pollEmVoo: false,
  falhasApi: 0,            // quedas seguidas da API: definem a espera
  tinhaPendente: false,    // havia job vivo na volta anterior do polling
  escolhas: { perfil: null, projeto: null }
};

var nosJob = {};      // cache de nós DOM da fila, por id, para atualizar sem recriar

function lerEscolhas() {
  try {
    var v = JSON.parse(localStorage.getItem(CHAVE_ESCOLHAS) || '{}');
    if (v && typeof v === 'object') estado.escolhas = { perfil: v.perfil || null, projeto: v.projeto || null };
  } catch (e) { /* ignora */ }
}
function salvarEscolhas() {
  try { localStorage.setItem(CHAVE_ESCOLHAS, JSON.stringify(estado.escolhas)); } catch (e) {}
}

/* ------------------------------------------------------------
   5. BOOT / CONFIG
   ------------------------------------------------------------ */

function iniciar() {
  lerEscolhas();
  $('#host').textContent = location.host;   // a porta pode não ser a 8000

  // rascunho da caixa de links sobrevive a um reload
  try {
    var r = localStorage.getItem(CHAVE_RASCUNHO);
    if (r) $('#campo-links').value = r;
  } catch (e) {}
  atualizarContadorLinks();

  ligarEventos();
  renderPreview();
  renderFila();
  renderHistorico();

  carregarConfig();
}

/* Sem /api/config não há perfis nem projetos: a tela não funciona. Se a
   API ainda não subiu, insiste com o mesmo backoff em vez de ficar morta
   esperando um F5. */
function carregarConfig() {
  api('GET', '/api/config').then(function (cfg) {
    registrarSucessoApi();
    aplicarConfig(cfg);
    renderProjetos();
    carregarHistorico();
    atualizarFila();          // a fila é da sessão, mas pode haver job vivo
  }).catch(function (e) {
    if (registrarFalhaApi(e)) setTimeout(carregarConfig, esperaAtual());
    else toast(e.message, 'erro');
  });
}

function aplicarConfig(cfg) {
  estado.config = cfg;

  // ffmpeg
  var f = cfg.ffmpeg || {};
  var pill = $('#pill-ffmpeg');
  pill.textContent = 'ffmpeg ' + (f.disponivel ? (f.completo ? 'ok' : 'parcial') : 'ausente');
  pill.dataset.tone = f.disponivel ? (f.completo ? 'ok' : 'neutral') : 'erro';
  pill.title = f.disponivel ? (f.ffmpeg || '') + (f.ffprobe ? '\n' + f.ffprobe : '') : 'não encontrado no sistema';

  if (!f.disponivel) {
    $('#banner-ffmpeg').hidden = false;
  } else if (!f.completo) {
    $('#banner-ffmpeg').hidden = false;
    $('#banner-ffmpeg-msg').textContent = 'ffprobe não foi encontrado. Alguns perfis podem falhar.';
  }

  // selects em lote
  preencherPerfis($('#lote-perfil'), estado.escolhas.perfil);
  preencherProjetos($('#lote-projeto'), estado.escolhas.projeto);

  preencherCookies(cfg.cookies || {});

  // filtro de projeto do histórico
  var filtro = $('#filtro-projeto');
  filtro.innerHTML = '<option value="">Todos os projetos</option>' +
    (cfg.projetos || []).map(function (p) {
      return '<option value="' + esc(p.nome) + '">' + esc(p.rotulo) + '</option>';
    }).join('');
}

function preencherPerfis(sel, escolhido) {
  var perfis = (estado.config && estado.config.perfis) || [];
  sel.innerHTML = perfis.map(function (p) {
    var motivo = p.disponivel ? '' : ' — exige ffmpeg';
    return '<option value="' + esc(p.nome) + '"' + (p.disponivel ? '' : ' disabled') + '>' +
      esc(p.descricao) + esc(motivo) + '</option>';
  }).join('');
  var alvo = escolhido || primeiroValido(perfis, 'disponivel');
  if (alvo) sel.value = alvo;
}

function preencherProjetos(sel, escolhido) {
  var projetos = (estado.config && estado.config.projetos) || [];
  sel.innerHTML = projetos.map(function (p) {
    var motivo = p.valido ? '' : ' — ' + (p.motivo || 'indisponível');
    return '<option value="' + esc(p.nome) + '"' + (p.valido ? '' : ' disabled') + ' title="' + esc(p.pasta) + '">' +
      esc(p.rotulo) + esc(motivo) + '</option>';
  }).join('') +
    '<option value="' + AVULSO + '">Pasta avulsa…</option>';
  var alvo = escolhido || primeiroValido(projetos, 'valido');
  if (alvo) sel.value = alvo;
}

function primeiroValido(lista, campo) {
  for (var i = 0; i < lista.length; i++) if (lista[i][campo]) return lista[i].nome;
  return lista.length ? lista[0].nome : null;
}

function perfilPorNome(nome) {
  var l = (estado.config && estado.config.perfis) || [];
  for (var i = 0; i < l.length; i++) if (l[i].nome === nome) return l[i];
  return null;
}
function projetoPorNome(nome) {
  var l = (estado.config && estado.config.projetos) || [];
  for (var i = 0; i < l.length; i++) if (l[i].nome === nome) return l[i];
  return null;
}
function rotuloProjeto(nome) {
  // 'avulso' é o nome que o back-end grava no histórico para um download que
  // foi para uma pasta digitada na hora; quem diz para onde é o caminho.
  if (nome === AVULSO || nome === 'avulso') return 'Pasta avulsa';
  var p = projetoPorNome(nome);
  return p ? p.rotulo : nome;
}

/* ------------------------------------------------------------
   6. ENTRADA E INSPEÇÃO
   ------------------------------------------------------------ */

function linhasDeLinks() {
  return $('#campo-links').value.split('\n')
    .map(function (l) { return l.trim(); })
    .filter(function (l) { return l.length > 0; })
    .filter(function (l, i, a) { return a.indexOf(l) === i; });
}

function atualizarContadorLinks() {
  var n = linhasDeLinks().length;
  $('#contador-links').textContent = n === 0 ? 'nenhum link' : n + (n === 1 ? ' link' : ' links');
  $('#btn-inspecionar').disabled = n === 0;
  try { localStorage.setItem(CHAVE_RASCUNHO, $('#campo-links').value); } catch (e) {}
}

function inspecionar() {
  var texto = $('#campo-links').value;
  if (!linhasDeLinks().length) return;

  $('#btn-inspecionar').disabled = true;
  // a chamada espera a rede: mostra carregamento
  $('#lista-preview').innerHTML = '<div class="carregando">Consultando ' +
    linhasDeLinks().length + ' link(s)…</div>';
  $('#contador-preview').textContent = '';
  $('#lote').hidden = true;

  api('POST', '/api/inspecionar', { links: texto }).then(function (r) {
    registrarSucessoApi();
    estado.preview = (r.itens || []).map(function (item) {
      return {
        item: item,
        perfil: $('#lote-perfil').value || null,
        projeto: $('#lote-projeto').value || null,
        forcar: false,
        enfileirado: false
      };
    });
    renderPreview();
  }).catch(function (e) {
    estado.preview = [];
    renderPreview();
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  }).then(function () {
    $('#btn-inspecionar').disabled = linhasDeLinks().length === 0;
  });
}

/* ------------------------------------------------------------
   7. PREVIEW
   ------------------------------------------------------------ */

function renderPreview() {
  var alvo = $('#lista-preview');
  var lista = estado.preview;

  if (!lista.length) {
    $('#lote').hidden = true;
    $('#contador-preview').textContent = '';
    alvo.innerHTML = vazio('Nada inspecionado',
      'Cole os links acima e clique em Inspecionar. Os metadados aparecem aqui antes de qualquer download.');
    return;
  }

  var validos = lista.filter(function (p) { return p.item.ok && !p.enfileirado; }).length;
  $('#lote').hidden = false;
  $('#btn-enfileirar-todos').disabled = validos === 0;
  $('#btn-enfileirar-todos').textContent = 'Enfileirar todos' + (validos ? ' (' + validos + ')' : '');
  $('#contador-preview').textContent = lista.length + ' item(ns)';

  alvo.innerHTML = lista.map(function (p, i) {
    return p.item.ok ? cartaoVideo(p, i) : cartaoErro(p.item, i);
  }).join('');
  preencherSelectsPreview();
}

function cartaoErro(item, i) {
  return '' +
    '<article class="cartao cartao--erro" data-i="' + i + '">' +
      '<div class="cartao__topo">' +
        '<div class="cartao__info">' +
          '<div class="row row--gap">' + selo('falhou') +
            '<span class="tag">' + esc(item.motivo || 'desconhecido') + '</span></div>' +
          '<p class="falha__msg">' + esc(item.erro) + '</p>' +
          '<p class="caminho">' + esc(item.original) + '</p>' +
        '</div>' +
      '</div>' +
    '</article>';
}

function cartaoVideo(p, i) {
  var v = p.item.video || {};
  var perfilSel = p.perfil;
  var jaBaixado = (p.item.baixados || {})[perfilSel] || null;
  var dataUp = fmtDataUpload(v.data_upload);
  var maxQual = (v.qualidades && v.qualidades.length) ? Math.max.apply(null, v.qualidades) : null;

  var html = '<article class="cartao" data-i="' + i + '">';

  // topo: thumbnail + identificação
  html += '<div class="cartao__topo">';
  html += '<div class="thumb' + (v.thumbnail ? '' : ' thumb--vazia') + '">' +
    (v.thumbnail
      ? '<img src="' + esc(v.thumbnail) + '" alt="" loading="lazy" ' +
        'onerror="this.remove(); this.parentNode.classList.add(\'thumb--vazia\')">'
      : '') +
    '<span class="thumb__dur mono">' + fmtDuracao(v.duracao_s) + '</span>' +
    '</div>';

  html += '<div class="cartao__info">';
  html += '<h3 class="titulo" title="' + esc(v.titulo) + '">' + esc(v.titulo) + '</h3>';
  html += '<div class="meta">' +
    '<span class="meta__canal">' + esc(v.canal || 'canal desconhecido') + '</span>' +
    (dataUp ? '<span class="meta__sep">/</span><span class="mono">' + esc(dataUp) + '</span>' : '') +
    '<span class="meta__sep">/</span><span class="mono">' + esc(v.extractor || '') + '</span>' +
    '</div>';

  if (v.qualidades && v.qualidades.length) {
    html += '<div class="quals">' + v.qualidades.map(function (q) {
      return '<span class="qual' + (q === maxQual ? ' qual--max' : '') + '">' + q + 'p</span>';
    }).join('') + '</div>';
  } else {
    html += '<div class="quals"><span class="qual">qualidades não informadas</span></div>';
  }
  html += '</div></div>';

  // escolhas por vídeo
  html += '<div class="cartao__acoes">' +
    '<label class="field field--inline"><span class="field__label">Perfil</span>' +
      '<select class="select" data-acao="perfil" data-i="' + i + '"></select></label>' +
    '<label class="field field--inline"><span class="field__label">Projeto</span>' +
      '<select class="select" data-acao="projeto" data-i="' + i + '"></select></label>' +
    '<button class="btn ' + (p.enfileirado ? 'btn--ghost' : 'btn--primary') + '" data-acao="enfileirar" data-i="' + i + '"' +
      (p.enfileirado ? ' disabled' : '') + '>' +
      (p.enfileirado ? 'Na fila' : (jaBaixado && !p.forcar ? 'Enfileirar' : 'Enfileirar')) + '</button>' +
    '</div>';

  // aviso de site fora do YouTube — visível, não escondido
  if (p.item.aviso) {
    html += '<div class="nota nota--aviso">' +
      '<span class="nota__tag">Fora do YouTube</span>' +
      '<span class="nota__corpo">' + esc(p.item.aviso) + '</span></div>';
  }

  // duplicata para o perfil escolhido
  if (jaBaixado) {
    html += '<div class="nota nota--dup">' +
      '<span class="nota__tag">Já baixado</span>' +
      '<span class="nota__corpo">' +
        '<span class="caminho">' + esc(jaBaixado.caminho) + '</span><br>' +
        esc(rotuloProjeto(jaBaixado.projeto)) + ' / ' + esc(jaBaixado.resolucao || '--') +
        ' / ' + esc(fmtDataHora(jaBaixado.concluido_em)) +
      '</span>' +
      '<button class="btn btn--mini' + (p.forcar ? ' btn--primary' : '') + '" data-acao="forcar" data-i="' + i + '">' +
        (p.forcar ? 'Vai baixar de novo' : 'Baixar de novo') + '</button>' +
      '</div>';
  }

  html += '</article>';
  return html;
}

/* os <select> de cada cartão são preenchidos após o innerHTML,
   para reaproveitar as mesmas funções de preenchimento */
/* O campo da pasta avulsa só aparece quando ela foi escolhida em algum
   lugar — na barra de lote ou no seletor de um cartão. */
function atualizarCampoAvulso() {
  var usado = $('#lote-projeto').value === AVULSO ||
    estado.preview.some(function (p) { return p.projeto === AVULSO; });
  $('#campo-avulso').hidden = !usado;
}

function preencherSelectsPreview() {
  var selects = $('#lista-preview').querySelectorAll('select[data-acao]');
  for (var k = 0; k < selects.length; k++) {
    var s = selects[k], p = estado.preview[Number(s.dataset.i)];
    if (!p) continue;
    if (s.dataset.acao === 'perfil') preencherPerfis(s, p.perfil);
    else preencherProjetos(s, p.projeto);
    p[s.dataset.acao] = s.value;
  }
  atualizarCampoAvulso();
}

function enfileirar(indices) {
  var itens = indices.map(function (i) { return estado.preview[i]; })
    .filter(function (p) { return p && p.item.ok && !p.enfileirado; });
  if (!itens.length) return;

  // /api/fila aceita um perfil e um projeto por chamada:
  // agrupa por (perfil, projeto, forcar)
  var grupos = {};
  itens.forEach(function (p) {
    var chave = p.perfil + '|' + p.projeto + '|' + (p.forcar ? 1 : 0);
    (grupos[chave] = grupos[chave] || []).push(p);
  });

  Object.keys(grupos).forEach(function (chave) {
    var grupo = grupos[chave];
    var partes = chave.split('|');
    var corpo = {
      urls: grupo.map(function (p) { return p.item.url; }),
      perfil: partes[0],
      forcar: partes[2] === '1'
    };
    // Projeto cadastrado OU pasta avulsa: a API recusa os dois juntos.
    if (partes[1] === AVULSO) {
      corpo.pasta = $('#pasta-avulsa').value.trim();
      if (!corpo.pasta) {
        toast('Informe a pasta avulsa, ou escolha um projeto.', 'erro');
        return;
      }
    } else {
      corpo.projeto = partes[1];
    }

    api('POST', '/api/fila', corpo).then(function (r) {
      registrarSucessoApi();
      // tudo ou nada: se voltou 200, todos entraram
      (r.ids || []).forEach(function (id, k) {
        if (grupo[k]) estado.urlPorJob[id] = grupo[k].item.url;
      });
      grupo.forEach(function (p) { p.enfileirado = true; });
      estado.escolhas.perfil = corpo.perfil;
      estado.escolhas.projeto = corpo.projeto;
      salvarEscolhas();
      renderPreview();
      preencherSelectsPreview();
      atualizarFila();
      ligarPolling();
      toast(grupo.length + (grupo.length === 1 ? ' job na fila' : ' jobs na fila'), 'ok');
    }).catch(function (e) {
      if (registrarFalhaApi(e)) return;
      // 409 de "já baixado" ganha caminho de saída: marcar forcar e tentar de novo
      if (e.status === 409 && /forcar=true/i.test(e.message)) {
        grupo.forEach(function (p) { p.forcar = true; });
        renderPreview();
        preencherSelectsPreview();
        toast(e.message + ' Marquei "baixar de novo" — clique em Enfileirar outra vez.', 'erro');
      } else {
        toast(e.message, 'erro');
      }
    });
  });
}

/* ------------------------------------------------------------
   8. FILA
   Render incremental: cada job tem um nó reaproveitado, para a
   barra transicionar entre os passos do polling em vez de recriar.
   ------------------------------------------------------------ */

function atualizarFila() {
  if (estado.pollEmVoo) return;                 // nunca duas requisições juntas
  estado.pollEmVoo = true;
  return api('GET', '/api/fila').then(function (r) {
    registrarSucessoApi();
    estado.jobs = r.jobs || [];
    renderFila();
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  }).then(function () {
    estado.pollEmVoo = false;
    ligarPolling();
  });
}

/* Um setTimeout por vez, e não setInterval: assim o intervalo pode crescer
   quando a API cai e voltar a 1 s quando ela responde. Com a API fora, o
   polling continua mesmo sem job vivo — é ele que descobre que ela voltou. */
function ligarPolling() {
  var pendente = estado.jobs.some(function (j) {
    return j.estado === 'na_fila' || j.estado === 'baixando';
  });
  if (estado.pollAtivo) { clearTimeout(estado.pollAtivo); estado.pollAtivo = false; }
  if (pendente || estado.falhasApi) {
    estado.pollAtivo = setTimeout(atualizarFila, esperaAtual());
  }
  if (estado.tinhaPendente && !pendente && !estado.falhasApi) {
    carregarHistorico();      // o que terminou já está no banco
  }
  estado.tinhaPendente = pendente;
}

function renderFila() {
  var alvo = $('#lista-fila');
  var jobs = estado.jobs;

  // resumo no cabeçalho + pill do topo
  var cont = { na_fila: 0, baixando: 0, concluido: 0, falhou: 0, cancelado: 0 };
  jobs.forEach(function (j) { if (cont[j.estado] !== undefined) cont[j.estado]++; });
  var ativos = cont.na_fila + cont.baixando;
  $('#pill-fila').textContent = 'fila ' + ativos;
  $('#pill-fila').dataset.tone = cont.baixando ? 'ativo' : (cont.falhou ? 'erro' : 'neutral');
  $('#resumo-fila').textContent = jobs.length
    ? [cont.baixando + ' baixando', cont.na_fila + ' na fila', cont.concluido + ' ok', cont.falhou + ' falha'].join('  ·  ')
    : '';

  if (!jobs.length) {
    nosJob = {};
    alvo.innerHTML = vazio('Fila vazia',
      'Downloads acontecem um por vez, em sequência. O que você enfileirar aparece aqui com progresso ao vivo.');
    return;
  }

  if (alvo.firstElementChild && alvo.firstElementChild.classList.contains('vazio')) {
    alvo.innerHTML = '';
    nosJob = {};
  }

  var posicao = 0;
  var vistos = {};

  jobs.forEach(function (j) {
    if (j.estado === 'na_fila') posicao++;
    vistos[j.id] = true;
    var no = nosJob[j.id];
    if (!no) {
      no = document.createElement('article');
      no.className = 'job';
      no.dataset.id = j.id;
      nosJob[j.id] = no;
      alvo.appendChild(no);
    }
    atualizarNoJob(no, j, j.estado === 'na_fila' ? posicao : 0);
  });

  // jobs que sumiram (cancelados e removidos)
  Object.keys(nosJob).forEach(function (id) {
    if (!vistos[id]) { nosJob[id].remove(); delete nosJob[id]; }
  });
}

function atualizarNoJob(no, j, posicao) {
  var pr = j.progresso;
  var pct = pr ? fmtPercentual(pr.percentual) : null;

  // a estrutura só é reconstruída quando o estado (ou o "já existia") muda
  var jaExistia = j.ja_existia ? '1' : '';
  var fase = j.fase || '';
  if (no.dataset.estado !== j.estado || no.dataset.jaExistia !== jaExistia ||
      no.dataset.fase !== fase) {
    no.dataset.estado = j.estado;
    no.dataset.jaExistia = jaExistia;
    no.dataset.fase = fase;
    no.innerHTML = estruturaJob(j, posicao);
  }

  var q = function (sel) { return no.querySelector(sel); };

  if (j.estado === 'na_fila') {
    var pos = q('[data-campo="posicao"]');
    if (pos) pos.textContent = posicao === 1 ? 'próximo da fila' : posicao + 'º da fila';
  }

  if (pr) {
    var barra = q('.barra'), fill = q('.barra__fill');
    if (barra) {
      barra.classList.toggle('barra--indet', pct === null);
      if (pct !== null) fill.style.width = pct + '%';
      barra.setAttribute('aria-valuenow', pct === null ? '' : pct);
    }
    var elPct = q('[data-campo="pct"]');
    if (elPct) elPct.innerHTML = pct === null ? '--<small>%</small>' : pct + '<small>%</small>';

    var elTam = q('[data-campo="tamanho"]');
    // Na conversão o progresso vem em SEGUNDOS de vídeo, não em bytes.
    if (elTam && pr) elTam.textContent = j.fase === 'convertendo'
      ? fmtDuracao(pr.baixados) + ' / ' + fmtDuracao(pr.total)
      : fmtBytes(pr.baixados) + ' / ' + fmtBytes(pr.total);
    var elVel = q('[data-campo="velocidade"]');
    if (elVel && pr) elVel.textContent = fmtVelocidade(pr.velocidade_bps);
    var elEta = q('[data-campo="eta"]');
    if (elEta && pr) elEta.textContent = pr.eta_s === null || pr.eta_s === undefined ? '--' : fmtDuracao(pr.eta_s);
  }

  var avisoEl = q('[data-campo="aviso"]');
  if (avisoEl) {
    avisoEl.hidden = !j.aviso;
    if (j.aviso) avisoEl.querySelector('.nota__corpo').textContent = j.aviso;
  }
}

function estruturaJob(j, posicao) {
  var v = j.video || {};
  var html = '';

  html += '<div class="job__topo">';
  html += '<div class="thumb' + (v.thumbnail ? '' : ' thumb--vazia') + '">' +
    (v.thumbnail ? '<img src="' + esc(v.thumbnail) + '" alt="" loading="lazy" ' +
      'onerror="this.remove(); this.parentNode.classList.add(\'thumb--vazia\')">' : '') +
    '<span class="thumb__dur mono">' + fmtDuracao(v.duracao_s) + '</span></div>';

  html += '<div class="job__info">';
  // Na conversão o estado ainda é `baixando` (o arquivo final não existe),
  // mas dizer "Baixando" fez o editor achar que o DOWNLOAD estava lento.
  html += '<div class="row row--gap">' +
    selo(j.estado, j.fase === 'convertendo' ? 'Convertendo' : null) +
    (j.ja_existia ? '<span class="tag">já existia</span>' : '') +
    // Depois do download, a conversão para ProRes pode levar mais que ele:
    // sem isto o card ficaria parado em 100% sem explicação.
    (j.fase === 'convertendo' ? '<span class="tag">download pronto · convertendo para o Premiere</span>' : '') +
    '<span class="tag">' + esc(j.perfil) + '</span>' +
    '<span class="tag">' + esc(rotuloProjeto(j.projeto)) + '</span></div>';
  html += '<h3 class="titulo" title="' + esc(v.titulo) + '">' + esc(v.titulo) + '</h3>';
  html += '<div class="meta"><span class="meta__canal">' + esc(v.canal || 'canal desconhecido') + '</span>' +
    '<span class="meta__sep">/</span><span class="mono">' + esc(fmtDataHora(j.criado_em)) + '</span></div>';
  html += '</div>';

  // cancelar: só habilitado em na_fila (contrato §3.5)
  html += '<button class="btn btn--mini btn--perigo" data-acao="cancelar" data-id="' + esc(j.id) + '"' +
    (j.estado === 'na_fila' ? '' : ' disabled title="só é possível cancelar antes de começar"') +
    '>Cancelar</button>';
  html += '</div>';

  if (j.estado === 'na_fila') {
    html += '<div class="row row--gap"><span class="posicao" data-campo="posicao"></span></div>';
  }

  // `ja_existia` chega como concluido com progresso null: nada foi baixado,
  // então não há barra nenhuma a mostrar (uma barra indeterminada aqui daria
  // a impressão de download em andamento num job que já acabou)
  if (j.estado === 'baixando' || (j.estado === 'concluido' && j.progresso)) {
    html += '<div class="prog">' +
      '<div class="prog__linha">' +
        '<div class="prog__pct mono" data-campo="pct">--<small>%</small></div>' +
        '<div class="prog__nums">' +
          '<div><b data-campo="tamanho">--</b><span>' +
            (j.fase === 'convertendo' ? 'convertido' : 'baixado') + '</span></div>' +
          '<div><b data-campo="velocidade">--</b><span>velocidade</span></div>' +
          '<div><b data-campo="eta">--</b><span>restante</span></div>' +
        '</div>' +
      '</div>' +
      '<div class="barra" role="progressbar" aria-valuemin="0" aria-valuemax="100"><div class="barra__fill"></div></div>' +
      '</div>';
  }

  if (j.estado === 'falhou') {
    var podeTentar = MOTIVOS_RETENTAVEIS[j.motivo_falha] && (j.url || estado.urlPorJob[j.id]);
    html += '<div class="falha">' +
      '<div class="falha__cod">' + esc(j.motivo_falha || 'desconhecido') + '</div>' +
      '<p class="falha__msg">' + esc(j.mensagem_falha || 'Falha sem mensagem.') + '</p>' +
      (MOTIVOS_RETENTAVEIS[j.motivo_falha]
        ? '<div class="falha__acoes"><button class="btn btn--mini" data-acao="tentar" data-id="' + esc(j.id) + '"' +
          (podeTentar ? '' : ' disabled') + '>Tentar de novo</button></div>'
        : '') +
      '</div>';
  }

  if (j.estado === 'concluido' && j.caminho_final) {
    html += '<div class="job__rodape">' +
      '<span class="caminho" title="' + esc(j.caminho_final) + '">' + esc(j.caminho_final) + '</span>' +
      botoesDoCaminho(j.caminho_final) +
      '</div>';
  }

  // "já existia" é sucesso com ressalva — cor de informação, a mesma que o
  // preview usa para duplicata. O amarelo fica para o que pede atenção.
  html += '<div class="nota ' + (j.ja_existia ? 'nota--dup' : 'nota--aviso') +
    '" data-campo="aviso" hidden>' +
    '<span class="nota__tag">' + (j.ja_existia ? 'Já existia' : 'Aviso') +
    '</span><span class="nota__corpo"></span></div>';

  return html;
}

function cancelar(id) {
  api('DELETE', '/api/fila/' + encodeURIComponent(id)).then(function () {
    registrarSucessoApi();
    atualizarFila();
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
    atualizarFila();          // 404/409: a fila mudou, recarrega
  });
}

function tentarDeNovo(id) {
  var job = estado.jobs.filter(function (j) { return j.id === id; })[0];
  var url = (job && job.url) || estado.urlPorJob[id];
  if (!job || !url) return;
  api('POST', '/api/fila', { urls: [url], perfil: job.perfil, projeto: job.projeto, forcar: true })
    .then(function (r) {
      (r.ids || []).forEach(function (novo) { estado.urlPorJob[novo] = url; });
      atualizarFila();
    }).catch(function (e) {
      if (!registrarFalhaApi(e)) toast(e.message, 'erro');
    });
}

/* ------------------------------------------------------------
   9. HISTÓRICO
   ------------------------------------------------------------ */

function carregarHistorico() {
  var termo = $('#busca-historico').value.trim();
  var projeto = $('#filtro-projeto').value;
  var q = [];
  if (termo) q.push('termo=' + encodeURIComponent(termo));
  if (projeto) q.push('projeto=' + encodeURIComponent(projeto));
  q.push('limite=' + LIMITE_HISTORICO);

  return api('GET', '/api/historico?' + q.join('&')).then(function (r) {
    registrarSucessoApi();
    estado.historico = r.registros || [];
    renderHistorico();
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  });
}

/* Cada registro é uma TENTATIVA, não um vídeo (contrato §3.6): baixar o
   mesmo vídeo duas vezes no mesmo perfil dá duas linhas. Agrupa pela mesma
   chave que o banco usa — extractor + video_id + perfil — e mostra a
   tentativa mais recente; as anteriores ficam atrás de "+N tentativas", com
   o caminho de cada uma, que é o que se procura quando se procura um
   arquivo. */
function agruparHistorico(regs) {
  var ordem = [], por = {};
  regs.forEach(function (r) {
    var chave = (r.extractor || '') + '|' + (r.video_id || '') + '|' + r.perfil;
    if (!por[chave]) { por[chave] = []; ordem.push(por[chave]); }
    por[chave].push(r);
  });
  return ordem;   // a API já devolve mais recente primeiro
}

function renderHistorico() {
  var alvo = $('#lista-historico');
  var regs = estado.historico;
  var filtrando = $('#busca-historico').value.trim() || $('#filtro-projeto').value;
  var grupos = agruparHistorico(regs);

  $('#contador-historico').textContent = regs.length
    ? grupos.length + (grupos.length === 1 ? ' vídeo' : ' vídeos') + ' · ' +
      regs.length + (regs.length === LIMITE_HISTORICO ? '+ tentativas' :
        (regs.length === 1 ? ' tentativa' : ' tentativas'))
    : '';

  if (!regs.length) {
    alvo.innerHTML = filtrando
      ? vazio('Nenhum resultado', 'A busca ignora acento e maiúsculas. Tente um termo mais curto ou limpe o filtro de projeto.')
      : vazio('Histórico vazio', 'Cada download concluído entra aqui com o caminho do arquivo no disco.');
    return;
  }

  alvo.innerHTML = grupos.map(function (g) {
    var r = g[0], anteriores = g.slice(1);
    return '' +
      '<div class="hist">' +
        '<div class="hist__info">' +
          '<div class="row row--gap">' + selo(r.status) +
            (r.ja_existia ? '<span class="tag">já existia</span>' : '') +
            '<span class="tag">' + esc(r.perfil) + '</span>' +
            '<span class="tag">' + esc(rotuloProjeto(r.projeto)) + '</span>' +
            (anteriores.length
              ? '<button class="btn btn--mini btn--ghost" data-acao="expandir" ' +
                'data-n="' + anteriores.length + '" aria-expanded="false">' +
                rotuloTentativas(anteriores.length, false) + '</button>'
              : '') +
          '</div>' +
          '<h3 class="titulo titulo--1" title="' + esc(r.titulo) + '">' + esc(r.titulo) + '</h3>' +
          metaHistorico(r) +
          corpoHistorico(r) +
          notaHistorico(r) +
          (anteriores.length
            ? '<div data-campo="anteriores" hidden>' +
              anteriores.map(tentativaAnterior).join('') + '</div>'
            : '') +
        '</div>' +
        '<div class="hist__acoes">' + botaoCopiar(r) + '</div>' +
      '</div>';
  }).join('');
}

function rotuloTentativas(n, aberto) {
  return (aberto ? '−' : '+') + n + (n === 1 ? ' tentativa' : ' tentativas');
}

function metaHistorico(r) {
  return '<div class="meta">' +
    '<span class="meta__canal">' + esc(r.canal || 'canal desconhecido') + '</span>' +
    '<span class="meta__sep">/</span><span class="mono">' + fmtDuracao(r.duracao_s) + '</span>' +
    '<span class="meta__sep">/</span><span class="mono">' + esc(r.resolucao || '--') + '</span>' +
    '<span class="meta__sep">/</span><span class="mono">' + fmtBytes(r.tamanho_bytes) + '</span>' +
    '<span class="meta__sep">/</span><span class="mono">' + esc(fmtDataHora(r.concluido_em || r.criado_em)) + '</span>' +
    '</div>';
}

/* O caminho só aparece quando aponta para um arquivo que existe. Em `falhou`
   a API já devolve caminho null; em `interrompido` SEM aviso, a reconciliação
   da subida não achou arquivo nenhum no destino — mostrar o caminho ali seria
   o histórico mentindo sobre onde o arquivo está. */
function temArquivo(r) {
  return !!r.caminho && (r.status === 'concluido' ||
    (r.status === 'interrompido' && !!r.aviso));
}

function corpoHistorico(r) {
  if (r.status === 'falhou') {
    return '<p class="falha__msg">' + esc(r.mensagem_falha || 'Falha sem mensagem.') + '</p>';
  }
  if (!temArquivo(r)) {
    return '<p class="falha__msg">' + esc(r.status === 'interrompido'
      ? 'O programa fechou durante o download e não há arquivo no destino.'
      : 'Sem arquivo no disco.') + '</p>';
  }
  return '<span class="caminho caminho--1" title="' + esc(r.caminho) + '">' + esc(r.caminho) + '</span>';
}

/* O `aviso` do histórico não tinha lugar na tela — e é a única forma de o
   usuário saber que há um arquivo possivelmente truncado no destino
   (contrato §7). Em `interrompido` a decisão é dele: o botão baixa de novo
   com forcar, e o arquivo parcial não é tocado. */
function notaHistorico(r) {
  var interrompido = r.status === 'interrompido';
  if (!r.aviso && !interrompido) return '';
  var url = r.url_canonica || r.url_original || '';
  return '<div class="nota ' + (r.ja_existia ? 'nota--dup' : 'nota--aviso') + '">' +
    '<span class="nota__tag">' + (r.ja_existia ? 'Já existia' : 'Aviso') + '</span>' +
    '<span class="nota__corpo">' +
      esc(r.aviso || 'O programa fechou durante o download.') + '</span>' +
    (interrompido && url
      ? '<button class="btn btn--mini" data-acao="refazer" data-url="' + esc(url) +
        '" data-perfil="' + esc(r.perfil) + '" data-projeto="' + esc(r.projeto) +
        '">Baixar de novo</button>'
      : '') +
    '</div>';
}

function botaoCopiar(r) {
  return botoesDoCaminho(r.caminho, !temArquivo(r));
}

/* Os dois botões de caminho, juntos. "Abrir pasta" chama /api/abrir-pasta,
   que só aceita caminho dentro de um projeto configurado — um navegador não
   abre pasta do disco sozinho, quem abre é o back-end (contrato §3.7). */
function botoesDoCaminho(caminho, desabilitado) {
  var atributos = 'data-caminho="' + esc(caminho || '') + '"' +
    (desabilitado || !caminho ? ' disabled' : '');
  return '<button class="btn btn--mini" data-acao="copiar" ' + atributos +
      '>Copiar caminho</button>' +
    '<button class="btn btn--mini" data-acao="abrir" ' + atributos +
      '>Abrir pasta</button>';
}

/* Tentativa anterior: o mesmo componente da linha do histórico, sem repetir o
   título — o que muda de uma tentativa para a outra é a data e o caminho. */
function tentativaAnterior(r) {
  return '' +
    '<div class="hist">' +
      '<div class="hist__info">' +
        '<div class="row row--gap">' + selo(r.status) +
          (r.ja_existia ? '<span class="tag">já existia</span>' : '') +
          '<span class="tag">' + esc(rotuloProjeto(r.projeto)) + '</span>' +
          '<span class="mono">' + esc(fmtDataHora(r.concluido_em || r.criado_em)) + '</span>' +
        '</div>' +
        corpoHistorico(r) +
        notaHistorico(r) +
      '</div>' +
      '<div class="hist__acoes">' + botaoCopiar(r) + '</div>' +
    '</div>';
}

function alternarTentativas(botao) {
  var caixa = botao.closest('.hist').querySelector('[data-campo="anteriores"]');
  if (!caixa) return;
  caixa.hidden = !caixa.hidden;
  botao.setAttribute('aria-expanded', String(!caixa.hidden));
  botao.textContent = rotuloTentativas(Number(botao.dataset.n), !caixa.hidden);
}

/* Refaz um download interrompido. `forcar` porque a tentativa anterior já
   ocupa a chave no histórico; o arquivo parcial continua onde está, e o novo
   ganha sufixo " (2)" se o nome colidir. */
function refazer(dados) {
  api('POST', '/api/fila', {
    urls: [dados.url], perfil: dados.perfil, projeto: dados.projeto, forcar: true
  }).then(function (r) {
    (r.ids || []).forEach(function (id) { estado.urlPorJob[id] = dados.url; });
    toast('Na fila de novo.', 'ok');
    atualizarFila();
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  });
}

/* ------------------------------------------------------------
   9b. PROJETOS
   O destino sai do config/projetos.yaml, e agora dá para editá-lo
   pela tela. O back-end grava preservando os comentários do arquivo.
   ------------------------------------------------------------ */

/* Cookies do navegador: a saída para o "confirme que não é um robô" do
   YouTube. Fica aqui, e não num arquivo para editar à mão, porque quem
   precisa disso está no meio de um download que falhou. */
function preencherCookies(cookies) {
  var sel = $('#cookies-navegador');
  sel.innerHTML = '<option value="">Desativado</option>' +
    (cookies.navegadores || []).map(function (n) {
      return '<option value="' + esc(n) + '">' + esc(n) + '</option>';
    }).join('');
  sel.value = cookies.navegador || '';
  $('#cookies-perfil').value = cookies.perfil || '';

  var aviso = $('#aviso-cookies');
  aviso.hidden = !cookies.motivo;
  if (cookies.motivo) aviso.querySelector('.nota__corpo').textContent = cookies.motivo;
}

function salvarCookies() {
  var corpo = {
    navegador: $('#cookies-navegador').value || null,
    perfil: $('#cookies-perfil').value.trim() || null
  };
  $('#btn-salvar-cookies').disabled = true;
  api('POST', '/api/cookies', corpo).then(function (r) {
    registrarSucessoApi();
    preencherCookies(r.cookies);
    toast(r.cookies.ativo
      ? 'Cookies do ' + r.cookies.navegador + ' ligados.'
      : 'Cookies desligados.', 'ok');
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) erroProjeto(e.message);
  }).then(function () {
    $('#btn-salvar-cookies').disabled = false;
  });
}

function alternarProjetos(mostrar) {
  var painel = $('#projetos');
  painel.hidden = mostrar === undefined ? !painel.hidden : !mostrar;
  if (!painel.hidden) { renderProjetos(); $('#novo-nome').focus(); }
}

function renderProjetos() {
  var projetos = (estado.config && estado.config.projetos) || [];
  $('#lista-projetos').innerHTML = projetos.map(function (p) {
    return '' +
      '<div class="hist">' +
        '<div class="hist__info">' +
          '<div class="row row--gap">' +
            '<span class="tag">' + esc(p.nome) + '</span>' +
            '<strong>' + esc(p.rotulo) + '</strong>' +
            (p.valido ? '' : '<span class="tag">inválido: ' +
              esc(p.motivo || 'indisponível') + '</span>') +
          '</div>' +
          '<span class="caminho">' + esc(p.pasta) + '</span>' +
        '</div>' +
        '<div class="hist__acoes">' +
          '<button class="btn btn--mini btn--perigo" data-acao="remover-projeto" ' +
            'data-nome="' + esc(p.nome) + '">Remover</button>' +
        '</div>' +
      '</div>';
  }).join('') || vazio('Nenhum projeto',
    'Cadastre abaixo a pasta onde o footage deve cair.');
}

function erroProjeto(mensagem) {
  var caixa = $('#erro-projeto');
  caixa.hidden = !mensagem;
  if (mensagem) caixa.querySelector('.nota__corpo').textContent = mensagem;
}

function adicionarProjeto() {
  var corpo = {
    nome: $('#novo-nome').value.trim(),
    caminho: $('#novo-caminho').value.trim(),
    rotulo: $('#novo-rotulo').value.trim() || null
  };
  if (!corpo.nome || !corpo.caminho) {
    erroProjeto('Preencha o nome e a pasta de destino.');
    return;
  }
  erroProjeto('');
  $('#btn-adicionar-projeto').disabled = true;

  api('POST', '/api/projetos', corpo).then(function (r) {
    registrarSucessoApi();
    return recarregarConfig().then(function () {
      $('#novo-nome').value = '';
      $('#novo-rotulo').value = '';
      $('#novo-caminho').value = '';
      renderProjetos();
      toast('Projeto ' + r.projeto.nome + ' cadastrado.', 'ok');
    });
  }).catch(function (e) {
    // Erro de cadastro fica NO formulário, não num toast que some em 6 s:
    // é ali que o usuário vai corrigir o campo.
    if (!registrarFalhaApi(e)) erroProjeto(e.message);
  }).then(function () {
    $('#btn-adicionar-projeto').disabled = false;
  });
}

function removerProjeto(nome) {
  if (!window.confirm('Remover o projeto "' + nome + '"?\n\n' +
      'Os arquivos já baixados continuam no disco e no histórico. ' +
      'O que sai é o destino da lista.')) return;

  api('DELETE', '/api/projetos/' + encodeURIComponent(nome)).then(function () {
    registrarSucessoApi();
    return recarregarConfig().then(function () {
      renderProjetos();
      toast('Projeto ' + nome + ' removido.', 'ok');
    });
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) erroProjeto(e.message);
  });
}

/* Depois de cadastrar ou remover, os seletores de perfil/projeto da tela
   inteira precisam refletir a lista nova. */
function recarregarConfig() {
  return api('GET', '/api/config').then(function (cfg) {
    aplicarConfig(cfg);
    renderPreview();
    preencherSelectsPreview();
  });
}

/* O navegador não entrega caminho do disco (a File System Access API só dá
   um handle, sem caminho). Quem abre o seletor nativo é o back-end, que roda
   nesta mesma máquina — daí o endpoint. */
function procurarPasta(idCampo) {
  var botoes = document.querySelectorAll('[data-acao="procurar"]');
  botoes.forEach(function (b) { b.disabled = true; });
  api('POST', '/api/escolher-pasta').then(function (r) {
    registrarSucessoApi();
    if (r.caminho) {
      $('#' + idCampo).value = r.caminho;
      erroProjeto('');
    } else {
      toast('Nenhuma pasta escolhida.', null);
    }
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  }).then(function () {
    botoes.forEach(function (b) { b.disabled = false; });
  });
}

function colarNoCampo(idCampo) {
  var campo = $('#' + idCampo);
  if (navigator.clipboard && navigator.clipboard.readText) {
    navigator.clipboard.readText().then(function (texto) {
      campo.value = (texto || '').trim();
      campo.focus();
    }, function () {
      campo.focus();
      toast('O navegador não deixou ler a área de transferência. Use Ctrl+V.', 'erro');
    });
  } else {
    campo.focus();
    toast('Use Ctrl+V para colar aqui.', null);
  }
}

/* ------------------------------------------------------------
   10. TOASTS, VAZIOS, EVENTOS
   ------------------------------------------------------------ */

function vazio(titulo, texto) {
  return '<div class="vazio"><div class="vazio__marca"></div>' +
    '<p class="vazio__titulo">' + esc(titulo) + '</p>' +
    '<p class="vazio__texto">' + esc(texto) + '</p></div>';
}

function toast(msg, tom) {
  var el = document.createElement('div');
  el.className = 'toast';
  if (tom) el.dataset.tone = tom;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(function () { el.remove(); }, 6000);
}

/* O endpoint existe desde o T8; até então este botão só copiava o caminho. */
function abrirPasta(caminho) {
  api('POST', '/api/abrir-pasta', { caminho: caminho }).then(function () {
    registrarSucessoApi();
    toast('Pasta aberta no explorador.', 'ok');
  }).catch(function (e) {
    if (!registrarFalhaApi(e)) toast(e.message, 'erro');
  });
}

function copiarCaminho(caminho) {
  var ok = function () { toast('Caminho copiado.', 'ok'); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(caminho).then(ok, function () { copiaManual(caminho, ok); });
  } else {
    copiaManual(caminho, ok);
  }
}
function copiaManual(texto, ok) {
  var ta = document.createElement('textarea');
  ta.value = texto;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); ok(); } catch (e) { toast('Não foi possível copiar.', 'erro'); }
  ta.remove();
}

function ligarEventos() {
  $('#campo-links').addEventListener('input', atualizarContadorLinks);
  $('#campo-links').addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); inspecionar(); }
  });
  $('#btn-inspecionar').addEventListener('click', inspecionar);
  $('#btn-limpar-links').addEventListener('click', function () {
    $('#campo-links').value = '';
    atualizarContadorLinks();
    $('#campo-links').focus();
  });

  // lote: aplica a todos os cartões
  $('#lote-perfil').addEventListener('change', function () {
    estado.preview.forEach(function (p) { if (!p.enfileirado) p.perfil = $('#lote-perfil').value; });
    estado.escolhas.perfil = $('#lote-perfil').value; salvarEscolhas();
    renderPreview(); preencherSelectsPreview();
  });
  $('#lote-projeto').addEventListener('change', function () {
    estado.preview.forEach(function (p) { if (!p.enfileirado) p.projeto = $('#lote-projeto').value; });
    estado.escolhas.projeto = $('#lote-projeto').value; salvarEscolhas();
    renderPreview(); preencherSelectsPreview();
    atualizarCampoAvulso();
  });

  $('#btn-gerenciar-projetos').addEventListener('click', function () { alternarProjetos(); });
  $('#btn-fechar-projetos').addEventListener('click', function () { alternarProjetos(false); });
  $('#btn-adicionar-projeto').addEventListener('click', adicionarProjeto);
  $('#btn-salvar-cookies').addEventListener('click', salvarCookies);
  $('#btn-enfileirar-todos').addEventListener('click', function () {
    enfileirar(estado.preview.map(function (_, i) { return i; }));
  });

  // delegação: cartões de preview
  $('#lista-preview').addEventListener('change', function (e) {
    var s = e.target.closest('select[data-acao]');
    if (!s) return;
    var p = estado.preview[Number(s.dataset.i)];
    if (!p) return;
    p[s.dataset.acao] = s.value;
    if (s.dataset.acao === 'perfil') p.forcar = false;   // duplicata é por perfil
    renderPreview(); preencherSelectsPreview();
  });
  $('#lista-preview').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-acao]');
    if (!b) return;
    var i = Number(b.dataset.i);
    if (b.dataset.acao === 'enfileirar') enfileirar([i]);
    if (b.dataset.acao === 'forcar') {
      estado.preview[i].forcar = !estado.preview[i].forcar;
      renderPreview(); preencherSelectsPreview();
    }
  });

  // delegação: fila e histórico
  document.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-acao]');
    if (!b) return;
    if (b.dataset.acao === 'cancelar') cancelar(b.dataset.id);
    if (b.dataset.acao === 'tentar') tentarDeNovo(b.dataset.id);
    if (b.dataset.acao === 'copiar') copiarCaminho(b.dataset.caminho);
    if (b.dataset.acao === 'abrir') abrirPasta(b.dataset.caminho);
    if (b.dataset.acao === 'expandir') alternarTentativas(b);
    if (b.dataset.acao === 'colar') colarNoCampo(b.dataset.alvo);
    if (b.dataset.acao === 'procurar') procurarPasta(b.dataset.alvo);
    if (b.dataset.acao === 'remover-projeto') removerProjeto(b.dataset.nome);
    if (b.dataset.acao === 'refazer') refazer(b.dataset);
  });

  $('#busca-historico').addEventListener('input', debounce(carregarHistorico, 250));
  $('#filtro-projeto').addEventListener('change', carregarHistorico);
}

/* ------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', iniciar);
