# Smoke test

Execute `python scripts/smoke_poc.py`: banco novo, duas escolas, autenticação hash, professor, turma, aluno, matrícula, restrição, nota, presença Control iD idempotente, isolamento, dashboard/cozinha mock, queda e retorno do central/outbox. Não usa câmera, WhatsApp, porta ou PostgreSQL reais.

Depois execute os `curl` do README com `MOCK_MESSAGES=true`. Testes físicos e credenciais reais estão explicitamente no checklist do README. Supabase não faz parte desta arquitetura.
