# TrueTrace Deployment

Docker Compose orchestration for the TrueTrace Multi-Agent Deepfake & AML Compliance System.

## Quick Start

```bash
cp .env.example .env
# Edit .env with your configuration
docker-compose up -d
```

## Services

| Service | Port | Description |
|---|---|---|
| `nginx` | 80 | API Gateway & Reverse Proxy |
| `backend` | 8080 | Spring Boot Banking + AML API |
| `agent-engine` | - | Python Multi-Agent Orchestrator |
| `dashboard-backend` | 8082 | Go Dashboard API |
| `dashboard-frontend` | 3001 | React Compliance Dashboard |
| `web-client` | 3000 | Next.js Customer Portal |
| `kafka` | 29092 | Event Streaming |
| `kafka-ui` | 9000 | Kafka Message Browser |
| `postgres` | 5432 | Database |
| `redis` | 6379 | Agent State Cache |

## Kafka Topics

| Topic | Description |
|---|---|
| `truetrace.kyc.submissions` | New KYC verification requests |
| `truetrace.transactions` | Real-time bank transfers |
| `truetrace.findings.deepfake` | Agent 1 deepfake analysis results |
| `truetrace.findings.money_trail` | Agent 2 money laundering findings |
| `truetrace.reports.str` | Agent 3 generated STR reports |
| `truetrace.alerts` | System-wide compliance alerts |

## Architecture

```
                    ┌──────────┐
                    │  Nginx   │ :80
                    └────┬─────┘
             ┌───────────┼───────────┐
             ▼           ▼           ▼
        ┌─────────┐ ┌─────────┐ ┌─────────┐
        │Web Client│ │Dashboard│ │Dashboard│
        │(Next.js) │ │Frontend │ │Backend  │
        │  :3000   │ │ :3001   │ │ :8082   │
        └─────────┘ └─────────┘ └────┬────┘
                                     │
                          ┌──────────┴──────────┐
                          ▼                     ▼
                    ┌──────────┐          ┌──────────┐
                    │ Backend  │◄────────►│  Kafka   │
                    │(Spring)  │          │          │
                    │  :8080   │          └────┬─────┘
                    └────┬─────┘               │
                         │              ┌──────┴──────┐
                         ▼              ▼             ▼
                    ┌──────────┐  ┌──────────┐ ┌──────────┐
                    │PostgreSQL│  │Agent Eng.│ │  Redis   │
                    │  :5432   │  │(Python)  │ │  :6379   │
                    └──────────┘  └──────────┘ └──────────┘
```
