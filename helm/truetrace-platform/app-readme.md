# TrueTrace Multi-Agent Compliance Platform

This chart deploys TrueTrace to Kubernetes:

- Spring Boot core API and PostgreSQL;
- Kafka/Redis for events and state;
- Three agents: Deepfake Inspector, Money-Trail Explorer, and AML Reporter;
- Customer portal and compliance command center;
- Nginx gateway and health/resource policies.

By default, agents run in `demo` mode so local installations do not require cloud credentials. For production, configure Alibaba Model Studio/eKYC endpoint, DashScope API key, identity registry gateway, and internal secrets via Kubernetes Secret or an external secret manager. STRs are always created as drafts for AML officers to review.

Requirements: Kubernetes 1.25+ and Helm 3.
