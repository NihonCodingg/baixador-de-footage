"""Taxonomia de falhas — regra de produto, não do yt-dlp.

O enum e as mensagens vivem aqui porque são decisões sobre o que o usuário lê
e sobre o que vale a pena tentar de novo (SPEC 5.6).

A tabela que traduz exceção do yt-dlp para este enum vive em
src/download/traducao_erros.py, porque precisa conhecer as classes do yt-dlp.
"""

from enum import Enum


class MotivoFalha(str, Enum):
    INDISPONIVEL = "indisponivel"
    PRIVADO = "privado"
    RESTRICAO_IDADE = "restricao_idade"
    BLOQUEIO_REGIONAL = "bloqueio_regional"
    DRM = "drm"
    SITE_NAO_SUPORTADO = "site_nao_suportado"
    REDE = "rede"
    RATE_LIMIT = "rate_limit"
    BLOQUEIO_BOT = "bloqueio_bot"
    COOKIES = "cookies"
    SEM_FFMPEG = "sem_ffmpeg"
    DISCO = "disco"
    DESCONHECIDO = "desconhecido"


# Mensagem legível por motivo. RESEARCH.md 6.4.
MENSAGENS = {
    MotivoFalha.INDISPONIVEL: "Vídeo indisponível ou removido.",
    MotivoFalha.PRIVADO: "Vídeo privado. Só o dono tem acesso.",
    MotivoFalha.RESTRICAO_IDADE: "Vídeo com restrição de idade — exige conta autenticada.",
    MotivoFalha.BLOQUEIO_REGIONAL: "Bloqueado na sua região.",
    MotivoFalha.DRM: "Conteúdo protegido por DRM. Fora do escopo desta ferramenta.",
    MotivoFalha.SITE_NAO_SUPORTADO: "Este site não é suportado pelo yt-dlp.",
    MotivoFalha.REDE: "Falha de rede.",
    MotivoFalha.RATE_LIMIT: "O site limitou a taxa de requisições. Aguarde.",
    MotivoFalha.BLOQUEIO_BOT: (
        "O YouTube pediu confirmação de que você não é um robô. Ative os "
        "cookies do navegador em Ajustes: a ferramenta passa a usar a sessão "
        "que você já tem aberta, e o site para de pedir."
    ),
    MotivoFalha.COOKIES: (
        "Não foi possível ler os cookies do navegador configurado. Feche o "
        "navegador e tente de novo — com ele aberto o arquivo de cookies fica "
        "travado. Se continuar, desative os cookies em Ajustes."
    ),
    MotivoFalha.SEM_FFMPEG: "ffmpeg não encontrado — não é possível juntar vídeo e áudio.",
    MotivoFalha.DISCO: "Falha ao gravar no disco.",
    MotivoFalha.DESCONHECIDO: "Falha não classificada.",
}

# Motivos em que repetir a tentativa faz sentido.
RETENTAVEIS = frozenset({MotivoFalha.REDE, MotivoFalha.RATE_LIMIT})


class ErroDeDominio(Exception):
    """Erro de regra de negócio. Nunca embrulha exceção de I/O."""


class LinkInvalido(ErroDeDominio):
    pass


class PerfilInvalido(ErroDeDominio):
    pass


class ProjetoInvalido(ErroDeDominio):
    pass


class TransicaoIlegal(ErroDeDominio):
    pass


class EspacoInsuficiente(ErroDeDominio):
    """O destino não tem espaço para o download + conversão + folga.

    Recusar ANTES é a regra: o disco cheio no meio fazia o ffmpeg falhar com
    "Conversion failed!", sem dizer por quê, e deixava arquivo parcial.
    """


class NomeImpossivel(ErroDeDominio):
    """A pasta do projeto é tão profunda que não sobra espaço nem para o custo
    fixo do nome (data + id + extensão). SPEC 8.3."""
