# API de scraping assíncrono — Sintegra GO

API em **FastAPI** que enfileira consultas por **CNPJ** na página pública do **Sintegra – Goiás**, processadas por **workers** consumindo **RabbitMQ**, com **status e resultados** armazenados temporariamente no **Redis**. O ambiente completo sobe com **Docker Compose** (API, worker, RabbitMQ e Redis).

## Checklist dos requisitos

| Item do desafio | Como está atendido neste repositório |
|-----------------|--------------------------------------|
| **1. API FastAPI** | `app/main.py` (`FastAPI`), OpenAPI em `/docs`. |
| **2a. `POST /scrape`** | `app/api/routes.py`: valida CNPJ (14 dígitos), cria tarefa no Redis (`pending`), publica JSON `{task_id, cnpj}` na fila RabbitMQ (mensagem persistente). Resposta **202** com `{ "task_id": "..." }`. Se a publicação falhar, a tarefa é removida do Redis e a API responde **503** (evita tarefa órfã em `pending`). |
| **2b. `GET /results/{task_id}`** | Mesmo arquivo: lê o registro no Redis; **404** se não existir; caso contrário devolve `status`, `result`, `error`, timestamps e `cnpj`. |
| **3. RabbitMQ (assíncrono)** | API publica na fila durável `scrape_tasks` (configurável via `QUEUE_NAME`). `worker/main.py` consome com `aio_pika`, `prefetch_count=5`, `requeue=False` após processar. Escalar: `docker compose up --scale worker=N`. |
| **4. Redis (status e resultado)** | `app/services/task_store.py`: chaves `scrape:task:{uuid}` com TTL (`TASK_RESULT_TTL_SECONDS`). Estados: `pending` → `processing` → `completed` \| `failed`. |
| **5. Docker / Compose** | `docker-compose.yml`: `api`, `worker`, `redis`, `rabbitmq` com healthchecks e `depends_on` condicional. `Dockerfile.api` e `Dockerfile.worker`. |
| **Scraping Sintegra GO** | `scraper/sintegra_go.py`: formulário público, POST e parse de campos (razão social, endereço, situação cadastral, etc.). Variáveis `SINTEGRA_*` no `.env.example`. |
| **Estrutura modular** | Pastas `app/`, `worker/`, `scraper/`, `tests/`, manifests Docker na raiz. |
| **Testes automatizados** | `pytest`: validação de schema, parser/builder de formulário com fixtures HTML, `TaskStore`, rotas da API (Redis/`publish` simulados), fluxo do worker (`handle_payload`). E2E opcional com rede: `RUN_E2E=1 pytest tests/test_e2e_live_sintegra.py`. |
| **Documentação** | Este README: arquitetura (diagrama), tabelas de serviços e env, exemplos `curl`, limites operacionais do site. |

### Critérios de avaliação (mapa rápido)

1. **Funcionalidade:** fluxo completo API → Redis + fila → worker → Redis → consulta por `task_id`; scraping com tratamento de erro (`failed` + mensagem).
2. **Qualidade de código:** tipagem, módulos por responsabilidade, validação Pydantic, logging no worker.
3. **Docker / escalabilidade:** serviços separados; workers stateless; fila compartilhada e Redis como fonte de verdade do estado da tarefa.
4. **Testes:** suite acima (unitários e de integração leve sem Rabbit/Redis reais nos testes de API/worker/store).
5. **Documentação:** seções de execução local, Compose, endpoints e variáveis de ambiente.

## Arquitetura

1. **Cliente** → `POST /scrape` com CNPJ (14 dígitos, com ou sem máscara).
2. A **API** gera um `task_id`, grava no Redis (`pending`) e publica uma mensagem JSON na fila `scrape_tasks`.
3. Um ou mais **workers** consomem a fila, atualizam o Redis (`processing`), executam o scraping HTTP e gravam `completed` ou `failed` com payload ou mensagem de erro.
4. **Cliente** → `GET /results/{task_id}` lê o estado consolidado no Redis.

```mermaid
flowchart LR
  Client[Cliente HTTP] --> API[FastAPI]
  API --> Redis[(Redis)]
  API --> RMQ[(RabbitMQ)]
  RMQ --> W[Worker]
  W --> Web[Sintegra GO]
  W --> Redis
  Client --> API
```

Escalabilidade: aumente réplicas do serviço `worker` com `docker compose up --scale worker=N`.

## Requisitos locais (sem Docker)

- Python 3.12+ recomendado
- Redis e RabbitMQ acessíveis (ou use apenas Docker para infra)

```bash
python -m venv .venv
.\.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env     # ajuste URLs se necessário
```

Suba Redis e RabbitMQ (por exemplo via Docker apenas para eles) e defina `REDIS_URL` e `RABBITMQ_URL` no `.env`.

Em dois terminais:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
python -m worker.main
```

## Docker Compose

Na raiz do projeto:

```bash
docker compose up --build
```

Serviços:

| Serviço    | Porta host | Descrição              |
|-----------|------------|-------------------------|
| `api`     | 8000       | FastAPI + OpenAPI em `/docs` |
| `redis`   | 6379       | Armazenamento de tarefas |
| `rabbitmq`| 5672, 15672| Fila AMQP e painel management |
| `worker`  | —          | Consumidor da fila      |

Escalar workers:

```bash
docker compose up --build --scale worker=3
```

## Endpoints

### `POST /scrape`

Corpo JSON:

```json
{ "cnpj": "00006486000175" }
```

Resposta **202 Accepted** com:

```json
{ "task_id": "uuid" }
```
![img.png](img.png)

### `GET /results/{task_id}`

Retorna o registro da tarefa: `status` (`pending` \| `processing` \| `completed` \| `failed`), `result` (mapa de campos extraídos da página, quando houver), `error` (mensagem se falhou).
![img_3.png](img_3.png)
## Variáveis de ambiente

| Variável | Exemplo | Descrição |
|----------|---------|-----------|
| `REDIS_URL` | `redis://redis:6379/0` | Redis |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | RabbitMQ |
| `SINTEGRA_BASE_URL` | `https://appasp.sefaz.go.gov.br` | Host da SEFAZ-GO |
| `SINTEGRA_ENTRY_PATH` | `/Sintegra/Consulta/default.html` | Caminho da página do formulário (pode ser `.asp` conforme o ambiente) |
| `SINTEGRA_VERIFY_SSL` | `false` | Desativa verificação TLS se o certificado legado estiver inválido (**use com cautela**) |
| `SINTEGRA_TIMEOUT_SECONDS` | `60` | Timeout HTTP |
| `QUEUE_NAME` | `scrape_tasks` | Nome da fila |
| `TASK_RESULT_TTL_SECONDS` | `604800` | TTL das chaves no Redis (padrão 7 dias) |

## Scraping

O módulo `scraper/sintegra_go.py` carrega a página do formulário, reaproveita campos ocultos (por exemplo `__VIEWSTATE` em páginas ASP), tenta marcar a opção **CNPJ** (radio ou `<select>`) e envia o POST. A resposta é interpretada como tabela de pares rótulo/valor (razão social, endereço, situação cadastral, etc.).

**Observações operacionais:** o site público pode responder `403`, exigir ajustes de cabeçalhos ou bloquear datacenters; certificados TLS em hosts legados podem estar vencidos. O projeto permite `SINTEGRA_VERIFY_SSL=false` para contornar apenas o problema de certificado, documentado acima.

## Testes

```bash
pytest
```

Inclui testes do validador de CNPJ, parser/builder de formulário (fixtures HTML, sem rede), `TaskStore`, rotas `POST /scrape` e `GET /results/{task_id}` (com Redis e publicação na fila simulados) e o processamento `handle_payload` do worker.

## Estrutura de pastas

```
app/           # FastAPI, config, rotas, Redis (tarefas)
worker/        # Consumidor RabbitMQ + orquestração do scraping
scraper/       # Lógica de consulta e parse HTML
tests/         # Pytest
docker-compose.yml
Dockerfile.api
Dockerfile.worker
```

## Exemplo rápido (curl)

```bash
curl -s -X POST http://localhost:8000/scrape -H "Content-Type: application/json" -d "{\"cnpj\":\"00006486000175\"}"
curl -s http://localhost:8000/results/<task_id>
```

CNPJs de exemplo sugeridos no enunciado: `00006486000175`, `00012377000160`, `00022244000175`.

## Licença e uso

Use apenas para fins educacionais ou integrações explicitamente permitidas pela SEFAZ-GO. O tráfego automatizado pode violar políticas do órgão; este repositório é um modelo de arquitetura (filas + cache + API).
