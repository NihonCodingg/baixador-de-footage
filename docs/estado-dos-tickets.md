# Estado dos tickets (T1–T8)

Movido do README em 10/09/2026: é registro de execução do plano, não vitrine.
O plano em si está em [PLAN.md](../PLAN.md); a especificação em [SPEC.md](../SPEC.md).


**Todos os tickets implementados.** Domínio, adapter, histórico, fila, API, interface e linha de comando estão prontos. O visual foi desenhado fora do repositório a partir do `CONTRATO-API.md` e integrado no T7.

O que existe hoje:

| Item | Estado |
|---|---|
| [RESEARCH.md](RESEARCH.md) — pesquisa técnica | pronto |
| [SPEC.md](SPEC.md) — especificação normativa | pronto |
| [PLAN.md](PLAN.md) — tickets T1 a T8 | pronto |
| `spike.py` — protótipo descartável | executado; gerou o `spike_meta.json` usado como fixture |
| Estrutura de pastas e stubs | pronta |
| Teste de arquitetura | passando (23 testes) |
| T1 — adapter do yt-dlp, tradução de erros, ffmpeg | **implementado** |
| T2 — validação de link, modelos, perfis | **implementado** |
| T3 — nomenclatura e sanitização | **implementado** |
| T4 — histórico SQLite | **implementado** |
| T5 — fila, worker e progresso | **implementado** |
| T6 — pipeline, API e `CONTRATO-API.md` | **implementado** |
| T7 — integração do front, validada no navegador | **implementado** |
| Smoke test com download real | **passou**: 1080x1920 H.264 + AAC |
| T8 — CLI e `POST /api/abrir-pasta` | **implementado** |

As instruções de execução abaixo descrevem o que funciona hoje.
