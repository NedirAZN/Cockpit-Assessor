# Cockpit BFR

Cockpit de carteiras da BFR Investimentos, agora servido por um backend em vez
de um arquivo HTML solto. Mesma tela de sempre — mudou o que está por trás dela.

**Estado atual:** 94 contas, ref. 31/08/2026, AUM R$ 32.223.697,59.

---

## O que mudou

Antes, os dados das carteiras ficavam cravados dentro do `index.html` e suas
anotações ficavam no localStorage do navegador. Atualizar o lote exigia
reescrever o arquivo, e o que você anotava no PC não existia no celular.

Agora:

- As carteiras vivem no Postgres. O servidor injeta os dados na página a cada
  acesso, então **a lógica do cockpit não mudou em nenhuma linha** — ele continua
  lendo a mesma variável `RAW`.
- Suas anotações (CRM, metas, mix, produtos, boletas, simulações) também vivem no
  Postgres. O `app/static/sync.js` troca o miolo do `localStorage` por um espelho
  do banco, sem tocar no código do cockpit.
- Os PDFs do XPerformance entram por upload e um agente processa em lote.

```
navegador ──▶ FastAPI ──▶ Postgres
                 │            ▲
                 │            │
              uploads    agente de lote (cron)
                            agente de backup (cron)
```

---

## Subir no GitHub

Pelo navegador, sem terminal:

1. **New repository**, privado — isto tem dado de cliente.
2. **uploading an existing file**.
3. Arraste **o conteúdo da pasta** `cockpit-bfr` (as pastas `app`, `agentes`,
   `scripts`, `dados` e os arquivos soltos). Não arraste a pasta de fora: o
   `Dockerfile` precisa ficar na raiz do repositório.
4. **Commit changes**. O upload leva um minuto — `dados/seed_carteiras.json` tem
   688 KB.

Se os arquivos acabarem dentro de uma subpasta, não precisa refazer: aponte o
**Root Directory** do serviço no Railway para essa subpasta.

---

## Subir no Railway

1. **New Project** → **Deploy from GitHub repo**.
2. **New** → **Database** → **Add PostgreSQL**, no mesmo projeto.
3. No serviço da aplicação, aba **Variables** → **Raw Editor**:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=<64 caracteres aleatórios>
COCKPIT_USUARIO=<seu usuário>
COCKPIT_SENHA=<senha longa, exclusiva>
PORT=8000
```

   O `DATABASE_URL` **não** aparece sozinho: ele precisa ser criado apontando
   para o banco, com as chaves duplas exatamente como acima.

4. **Settings** → **Networking** → **Generate Domain**, com a porta em **8000**.
5. Abra a URL. Na primeira subida o banco está vazio e as 94 contas entram
   sozinhas — nos logs: `gravadas 94 contas · AUM R$ 32.223.697,59`.

---

## Os dois agentes

Cada um é um **serviço cron** no Railway, apontando para o mesmo repositório.
Em **New** → **GitHub Repo** → mesmo repo, e então em **Settings**:

| Serviço | Start command | Cron schedule |
|---|---|---|
| Lote | `python -m agentes.lote` | `*/30 * * * *` |
| Backup | `python -m agentes.backup` | `0 6 * * *` |

Os dois precisam das mesmas variáveis da aplicação, e do mesmo Root Directory.

O serviço de lote acorda a cada 30 minutos, olha se há PDF pendente e volta a
dormir. Um cron que termina rápido custa centavos; um processo vigiando pasta 24
horas por dia custaria mais que o resto do sistema junto.

O backup roda às 6h UTC (3h em Porto Alegre) e guarda os últimos 30 retratos.

---

## Dividendos e proventos

O reconhecimento de provento lê o extrato de movimentações. A regra antiga
procurava `JUROS S/CAP` sem espaço, `RENDIMENTOS DE CLIENTES` no plural, e exigia
um ticker tipo `XPAG11` — o que deixava invisível quase 80% dos proventos da base
(R$ 20.903,84 reconhecidos contra R$ 78.942,80 ignorados).

Agora são reconhecidos:

| No extrato aparece como | O que é |
|---|---|
| `DIVIDENDOS DE CLIENTES VALE3` | dividendo de ação |
| `RENDIMENTOS DE CLIENTES XPML11` | rendimento de FII listado |
| `RENDIMENTO FUNDO FECHADO BALCÃO <nome>` | fundo cetipado, sem ticker |
| `JUROS S/ CAPITAL DE CLIENTES ITUB4` | JCP, com ou sem espaço após o `S/` |
| `CRÉDITO REF. TAXA DE REMUNERAÇÃO BTC VALE` | aluguel de ações |
| `PGTO JUROS \| NTN-B AGO/2050`, `CUPOM` | cupom de título e de COE |

Ficam de fora, de propósito: IRRF e débitos, estorno, reembolso de evento, ajuste
de day trade, liquidação, TED e transferência. E **amortização**, que é devolução
de principal, não renda — contá-la inflaria a conta de renda recorrente.

Resultado na base atual: de 36 para **66 contas** com provento identificado, e de
R$ 20.903,84 para **R$ 88.835,76** por mês.

---

## Primeiro acesso

**Traga suas anotações do navegador antigo.** Acesse `/importar` — a página
explica os cinco passos. Leva dois minutos e só precisa ser feito uma vez. Se
pular, o cockpit mostra as carteiras certas e as anotações vazias.

---

## Atualizar as carteiras

Na barra do cabeçalho, ao lado de Backup e Restaurar, fica o botão
**↑ Enviar relatórios**.
Abre um painel onde você solta os PDFs do XPerformance — ou um `.zip` com todos
eles — acompanha a leitura e recarrega no fim.

**Envie .zip, não .rar.** Abrir RAR exige um programa proprietário que não existe
na imagem do servidor; ZIP o Python lê sozinho. No Windows: botão direito na
pasta → "Compactar em arquivo ZIP". Se um .rar for selecionado, o painel avisa
antes de enviar.

Por que recarregar atualiza tudo: os dados são injetados pelo servidor a cada
acesso, então um reload refaz o cockpit inteiro de uma vez — KPIs, gráficos,
estratégias, posições, simulação. Não existe tela que fique para trás.

O agente grava em transação única. Se mais de 20% dos relatórios falharem, o lote
inteiro é recusado e nada é gravado. Meia importação é pior que nenhuma.

Pela linha de comando, o mesmo motor: `python -m agentes.lote`.

---

## O mapa do XPerformance

O `agentes/parse_xp.py` está calibrado contra o relatório real de 13 páginas
(ref. 30/09/2026) e passa na conferência sem avisos.

| Página | O que o cockpit retira dali |
|---|---|
| 1 | conta, segmento, assessor, data de referência |
| 2 | patrimônio, rent./ganho do mês e 24M, CDI, Ibovespa, IPCA, Dólar, resumo por período, estatísticas |
| 3 | rentabilidade mês a mês dos últimos 3 anos |
| 4 | 12 meses de patrimônio, movimentações, IR, IOF, ganho e %CDI |
| 5 | saldo e rentabilidade por estratégia |
| 7+ | ativo a ativo: saldo, quantidade, alocação, rentabilidade, %CDI |
| 10+ | movimentações do mês |

A página 4 é a mais valiosa: **um único relatório já preenche doze meses de
histórico**, então o gráfico de trajetória nasce cheio em vez de esperar doze
lotes.

### As três armadilhas, com o diagnóstico certo

1. **Quantidade não usa vírgula.** Dinheiro é `R$ 7.527,71` (ponto de milhar,
   vírgula decimal); quantidade é `3722.2` (ponto decimal) e aceita negativo
   (`-200`). Duas gramáticas numéricas na mesma linha — por isso `dinheiro()` e
   `quantidade()` são funções separadas.
2. **O gráfico invade a tabela.** Na pág. 2 o rótulo do eixo entra na linha:
   `30,00% Portfólio 1,89% ...`. Na pág. 5 o `99,66%` do donut vira prefixo. A
   defesa é ancorar no rótulo conhecido, nunca contar colunas da esquerda.
3. **Nome longo quebra em três linhas**, com os números no meio. Por isso a
   linha de números é reconhecida por começar com `R$`, e o nome é remontado com
   a linha de cima e a de baixo — só quando a linha realmente quebrou.

Para conferir um relatório isolado:

```
python -m agentes.parse_xp "XPerformance - 4786614 - Ref 30.09.pdf" --conferir
```

---

## Pedir melhorias pelo celular

Com o repositório no GitHub, abra o app do Claude, aba **Code**, escolha o repo e
descreva o que quer. O agente altera, comita, e o Railway redeploia sozinho. Se
quebrar, `git revert` no commit e volta em dois minutos.

Nunca editar arquivo direto no servidor: é o Git que te dá o botão de desfazer.

---

## Rodar na sua máquina

```
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Em `http://localhost:8000`, com `COOKIE_SEGURO=0` no `.env`.

---

## O que ficou de fora, e por quê

- **Alerta de vencimento.** Nenhuma das 1.578 posições tem data de vencimento
  preenchida — o campo nunca foi extraído do PDF. Um cron hoje não teria o que
  ler. Quando o parser capturar esse campo, o agente vira dez linhas.
- **Cotações por cron.** O cockpit já busca BRAPI e SGS do navegador e funciona.
- **Multiusuário.** Um login só. Se mais alguém for usar, isso precisa virar
  tabela de usuários — hoje vocês dividiriam a mesma senha, sem auditoria.

## Estrutura

Layout plano de propósito: **nenhum arquivo dentro de pasta**. O uploader web do
GitHub achata pastas em alguns navegadores, e isso quebrou o deploy três vezes.
Sem pastas, não há estrutura para ele estragar — arraste os 21 arquivos de uma
vez para a raiz do repositório.

```
main.py                    servidor: login, injeção dos dados na página, API
auth.py                    sessão por cookie assinado
db.py                      pool de conexões
util.py                    formatação em português
schema.sql                 tabelas (roda sozinho no start)

cockpit.html               o cockpit v6, com o bloco de dados retirado
sync.js                    espelha o localStorage no banco
upload.js                  botão "Enviar relatórios" no cabeçalho
login.html, importar.html  telas de apoio

parse_xp.py                leitor do PDF do XPerformance
importador.py              motor do lote: desempacota, valida, grava
lote.py                    agente cron do lote
backup.py                  agente cron do backup diário
seed_carteiras.py          carga inicial das 94 contas
importar_localstorage.py   versão de linha de comando da página /importar
seed_carteiras.json        as 94 contas extraídas do v6

Dockerfile  railway.json  requirements.txt  README.md
```

No Railway, o campo **Root Directory** fica **vazio**.

Os scripts do navegador são servidos por uma rota própria (`/static/sync.js` e
`/static/upload.js`), não por um diretório estático — num layout plano, publicar
a pasta inteira exporia o código-fonte.
