# Diagrama do Banco de Dados

Diagrama ER das principais tabelas usadas pelo sistema de apontamentos.

```mermaid
erDiagram
    AUTH_USER {
        int id PK
        string username
        string first_name
        string last_name
        string email
        bool is_staff
        bool is_active
        datetime date_joined
    }

    USER_PROFILE {
        int id PK
        int user_id FK
        bool is_gerente_projetos
        bool is_pmo
        bool exportacsv
        int codigoerp
        bool must_change_password
    }

    ORCAMENTO {
        int id PK
        int responsavel_id FK
        int pmo_id FK
        string codigo UK
        string codigo_cliente
        string numero_chamado
        string nome
        decimal horas
        decimal horas_adicionais
        decimal horas_apontadas
        bool ativo
        datetime criado_em
    }

    FASE {
        int id PK
        string codigo UK
        string descricao
        datetime criado_em
    }

    SERVICO {
        int id PK
        string codigo UK
        string descricao
        datetime criado_em
    }

    REGISTRO {
        int id PK
        int user_id FK
        int orcamento_id FK
        int fase_id FK
        int servico_id FK
        date data
        time hora_inicio
        time hora_fim
        string processado
        text descricao
        datetime criado_em
        datetime atualizado_em
    }

    AGENDA_ATIVIDADE {
        int id PK
        int user_id FK
        int criado_por_id FK
        int orcamento_id FK
        int servico_id FK
        string cliente
        string numero_chamado
        string produto
        string titulo
        text descricao
        date data_inicio
        date data_fim
        time hora_inicio
        time hora_fim
        decimal total_horas_maximo
        datetime criado_em
        datetime atualizado_em
    }

    SOLICITACAO_HORAS {
        int id PK
        int solicitante_id FK
        int orcamento_id FK
        decimal quantidade_horas
        text motivo
        string situacao
        text motivo_reprovacao
        int decidido_por_id FK
        datetime criado_em
        datetime decidido_em
    }

    ESTIMATIVA {
        int id PK
        int user_id FK
        string cliente
        string solicitante
        string projeto
        string sistema
        datetime criado_em
        datetime atualizado_em
    }

    ESTIMATIVA_ITEM {
        int id PK
        int estimativa_id FK
        int ordem
        string modulo_processo
        string recurso
        text escopo
        decimal horas_analise
        decimal horas_atividade
        decimal horas_gp
        decimal horas_estimadas
    }

    AUTH_USER ||--|| USER_PROFILE : possui

    AUTH_USER ||--o{ ORCAMENTO : responsavel
    AUTH_USER ||--o{ ORCAMENTO : pmo
    AUTH_USER ||--o{ REGISTRO : aponta
    AUTH_USER ||--o{ AGENDA_ATIVIDADE : agenda_para
    AUTH_USER ||--o{ AGENDA_ATIVIDADE : cria
    AUTH_USER ||--o{ SOLICITACAO_HORAS : solicita
    AUTH_USER ||--o{ SOLICITACAO_HORAS : decide
    AUTH_USER ||--o{ ESTIMATIVA : cria

    ORCAMENTO ||--o{ REGISTRO : recebe
    ORCAMENTO ||--o{ AGENDA_ATIVIDADE : planeja
    ORCAMENTO ||--o{ SOLICITACAO_HORAS : recebe

    FASE ||--o{ REGISTRO : classifica
    SERVICO ||--o{ REGISTRO : classifica
    SERVICO ||--o{ AGENDA_ATIVIDADE : classifica

    ESTIMATIVA ||--o{ ESTIMATIVA_ITEM : possui
```

## Observacoes

- `REGISTRO.fase_id` pode ficar vazio.
- `REGISTRO.servico_id` e obrigatorio na regra da aplicacao, embora o campo aceite nulo para compatibilidade de dados.
- `AGENDA_ATIVIDADE.servico_id` e obrigatorio na regra da aplicacao, embora o campo aceite nulo para compatibilidade de dados.
- `ORCAMENTO.responsavel_id` e `ORCAMENTO.pmo_id` podem ficar vazios.
- `ORCAMENTO.pmo_id` deve apontar para um usuario com `USER_PROFILE.is_pmo = true`.
- `USER_PROFILE.exportacsv` controla exportacao CSV, filtro por usuario em registros e processamento dos registros.
- Tabelas internas do Django, como grupos, permissoes e sessoes, foram omitidas para manter o diagrama focado no dominio do sistema.
