# Arquitetura

Consulte o diagrama e a regra de fonte de verdade no README. Os limites são: visão computacional (`recognition_pipeline.py`), domínio/persistência local (`database.py`), HTTP/autorização (`main.py`), integrações (`messaging*.py`, `integrations/`) e sincronização unidirecional (`sync_service.py`).

## Modelo de ameaça resumido

Chaves roubadas, callback forjado/replay, associação cross-tenant, vazamento biométrico e indisponibilidade central são mitigados respectivamente por hash/revogação e TLS, segredo+event ID, validações+triggers, minimização/exclusão e SQLite+outbox. SHA-256 é adequado para chaves aleatórias de alta entropia, não para senhas humanas. O proxy deve aplicar HTTPS, rate limit e allowlist do Control iD.
