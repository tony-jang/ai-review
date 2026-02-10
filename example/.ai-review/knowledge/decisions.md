# Technical Decisions

## [2025-03] Use raw SQL instead of ORM
- Decision: Use raw SQL with parameterized queries
- Reason: Complex query optimization, DBA collaboration
- Review rule: Do NOT suggest using ORM

## [2025-09] Allow any type in API response parsing
- Decision: `Any` type allowed in API response layer
- Reason: External API types are unstable, runtime validation used
- Review rule: Do NOT flag `Any` in response parsing code
