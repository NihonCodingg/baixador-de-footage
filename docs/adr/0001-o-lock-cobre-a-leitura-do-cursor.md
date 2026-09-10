# ADR 0001 — O lock do histórico cobre a leitura, não só a chamada

- **Status:** aceito (10/09/2026)
- **Afeta:** `src/storage/historico.py`, `tests/test_historico_concorrencia.py`, o job `concorrencia-linux` da CI
- **Relacionado:** [issue #1](https://github.com/NihonCodingg/baixador-de-footage/issues/1), uma segunda corrida, de ordenação, que este ADR **não** corrige

## Sintoma

Na primeira execução da CI em Linux com Python 3.14, `test_escritas_concorrentes_de_varias_threads` reprovou:

```
AssertionError: assert [IndexError('tuple index out of range')] == []
```

Vinte threads gravando e lendo o histórico SQLite, uma delas levantou `IndexError`. A mesma suíte passava 744/744 na máquina do autor (Windows, 3.14) e em Windows na CI, em todas as versões.

## Causa

O `Historico` tem **uma conexão** e **um `RLock`**. O comentário da classe prometia: *"a serialização é o lock desta classe, não o do sqlite3"*. O lock estava assim:

```python
def _executar(self, sql, params=()):
    with self._lock:
        cursor = self._con.execute(sql, params)
        self._con.commit()
        return cursor  # o lock termina aqui
```

E as linhas eram lidas **depois**, fora dele — `cursor.fetchone()` em `obter_por_id` e `ja_baixado`, `cursor.fetchall()` em `buscar`.

`fetchone()` e `fetchall()` não são leitura de memória: chamam `sqlite3_step()` no C, e o módulo `sqlite3` do CPython **solta o GIL** nessa hora. Duas threads podiam estar dentro do SQLite, na mesma conexão, ao mesmo tempo — uma lendo, outra fazendo `execute` + `commit`.

**Por que `IndexError` e não erro do sqlite3:** a biblioteca C é thread-safe e não reclama. Quem quebra é a camada Python: `sqlite3.Row` traduz nome de coluna para índice pelo `description` do cursor, e uma linha montada no meio da corrida pode sair com menos colunas do que o `description` anuncia. `row["campo"]` indexa além do fim.

**Por que o Windows escondeu:** escalonamento. O agendador do Windows dá fatias de tempo longas e a thread termina `execute` + `fetch` antes de ser preemptada. O Linux troca de thread com mais frequência e atravessa a janela. Não é bug de plataforma — é a mesma falha, com sorte diferente.

## O que foi assumido errado

A suposição foi tratar **`execute()` como a operação** e o cursor devolvido como **dado inerte**. Ele é um *handle vivo*: ainda vai executar código C quando alguém pedir as linhas. O lock foi dimensionado para "a chamada que envia o SQL", quando precisava cobrir "a conversa inteira com a conexão, até a última linha lida".

A regra que sai disto: **não devolva, de dentro de um lock, um objeto que ainda vai tocar o recurso protegido.** O escopo do lock acabava no `return`; a vida do objeto, não.

## Reprodução

O sintoma não se deixou reproduzir sob demanda:

| Onde | O quê | Resultado |
|---|---|---|
| Windows local, 3.14 | seis configurações, até 32 threads sobre 5.000 linhas, `setswitchinterval` mínimo | 0 falhas |
| Linux na CI, 3.14 | 26 rodadas de três testes de estresse (76 execuções) | 0 falhas |

`sys.setswitchinterval` quase não influencia: a janela está em código C com o GIL solto, e o intervalo de troca só governa threads rodando bytecode. Quem decide é o escalonador do SO.

A saída foi testar o **invariante** em vez do sintoma: um dublê embrulha a conexão real e anota toda chamada ao SQLite feita sem o lock seguro (`RLock._is_owned()`, o mesmo gancho que `threading.Condition` usa por dentro). É determinístico, em qualquer sistema e versão:

```
antes:  assert ['fetchone', 'fetchone', 'fetchall'] == []   FAILED em 0,12s
depois: passed
```

Exatamente as três leituras que estavam fora do lock. Os testes de estresse ficaram como complemento, porque são a única chance de ver o sintoma.

## Opções consideradas

| | Opção | Por que não / por que sim |
|---|---|---|
| **A1** | `_executar` lê as linhas **dentro** do lock e devolve dados, nunca o cursor | **Escolhida.** Remove o erro por construção: nenhum handle vivo sai do lock. Custo medido: lock seguro por mais 0,34 ms no `LIMIT 100` padrão, 4,9 ms no teto de 1.000 da API, 0,07 ms num `fetchone`. App de um usuário: imperceptível. |
| A2 | `with self._lock:` em volta dos três métodos de leitura | Diff menor, mas o cursor continua escapando de `_executar`; só o teste de invariante impede a próxima pessoa de cair. |
| B | Conexão por thread (`threading.local`) + WAL | Muda `fechar()`, cria N conexões (worker + threadpool do uvicorn), arquivos `-wal`/`-shm` ao lado do banco. Não cabe em duas frases para um app de uma pessoa. |
| C | WAL sozinho | **Não corrige.** O defeito é estado Python compartilhado numa conexão só; WAL muda o travamento *entre* conexões. |
| D | Serializar só a escrita | **Não corrige.** O lado desprotegido era a leitura. |

## Decisão

A1. Em duas frases: *o `_executar` devolvia o cursor com o lock já solto; agora devolve as linhas, lidas dentro dele.* Ninguém fora da classe dependia do cursor — `_executar` é privado, e os usos internos de `rowcount`, `lastrowid` e iteração já aconteciam sob o lock.

## Verificação

- Teste de invariante: reprova antes, passa depois, em Windows e Linux.
- Suíte completa passando nas três versões da matriz (3.12, 3.13, 3.14) em `windows-latest`.
- Job `concorrencia-linux`, **permanente e bloqueante**, rodando `tests/test_historico_concorrencia.py` em `ubuntu-latest` + 3.14 — o único lugar onde o sintoma apareceu.

## O que este ADR não resolve

Durante a investigação apareceu uma **segunda** corrida, de ordenação: o worker marca a fila como terminal antes de gravar o histórico (`worker.py`, `_concluir` e `_falhar`), e um leitor que espera pela fila e em seguida lê o histórico vê `baixando`. Não é uma troca de ordem simples — a chamada à fila funciona como guarda de transição. Está descrita na [issue #1](https://github.com/NihonCodingg/baixador-de-footage/issues/1).
