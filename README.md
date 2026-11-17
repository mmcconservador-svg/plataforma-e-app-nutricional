# Nutri Saúde Integral — plataforma de avaliação nutricional (PC + app/PWA)

Flask + SQLite (ou PostgreSQL). Roda no PC e instala como app no celular (PWA). Foco em LGPD.

## Início rápido
```
pip install -r requirements.txt
cp .env.example .env        # edite identidade do profissional e SECRET
python -m flask --app app criar-profissional     # cria o acesso
python servir.py                                  # http://localhost:5000
```
Windows: `iniciar.bat` · Linux/macOS: `iniciar.sh`. Testes: `python -m pytest tests -q`.

## Módulos
- **Avaliação antropométrica**: 11 equações de TMB/GET (cada uma marcada *verificada* ou *literatura*), fatores de atividade (clássicos e FAO/OMS 2004), fatores de injúria, 6 protocolos de dobras, circunferências/AMB, comparação de todas as equações.
- **Alimentos e cardápios**: TACO, TBCA, IBGE (medidas caseiras), USDA FDC e alimentos próprios; fatores de correção (FC) e cocção (FCoc); equivalentes por nutriente; metas × atingido (±10%); lista de compras em peso bruto; **%VD ANVISA** (IN 75/2020, Anexo II).
- **Exames**: entrada manual com intervalo do laboratório, conversão de unidades, valores calculados (não-HDL, Friedewald, HOMA-IR, TFG CKD-EPI 2021), achados **para correlação clínica** com fonte e versão das regras; PDF anexado e criptografado.
- **Integrativa (8 remédios naturais)** e **TEA** (triagem alimentar).
- **Dashboard** e alertas por regras transparentes (sem IA generativa; nada sai a terceiros).
- **Portal do paciente**: só vê o que o profissional validar e liberar.

## LGPD e segurança
Campos sensíveis criptografados (Fernet, rotação de chaves), índice cego para busca por nome, consentimentos imutáveis, consentimento específico para dado espiritual, responsável legal para menores (art. 14), exportação e anonimização, trilha de auditoria encadeada por hash, Argon2id, 2FA TOTP com códigos de recuperação, bloqueio por tentativas, CSRF, CSP, sessão com expiração. Veja `docs/`.

## Tabelas de alimentos — IMPORTANTE
As tabelas oficiais **não vêm embutidas** (licenças próprias). Baixe nas fontes e importe em *Alimentos → Importar*, confirmando a licença:
[TACO/NEPA-UNICAMP](https://www.gov.br/agricultura/pt-br/assuntos/inspecao/produtos-vegetal/legislacao-programas-nacionais-e-seguranca-dos-alimentos-1/legislacao/legislacao-vinhos-e-bebidas/tabela-brasileira-de-composicao-de-alimentos_taco_2011.pdf) · [TBCA/USP](https://www.tbca.net.br/) · [IBGE POF](https://www.ibge.gov.br/) · [USDA FoodData Central](https://fdc.nal.usda.gov/download-datasets). Modelos de planilha em `modelos_importacao/`. USDA: `flask --app app importar-usda PASTA` ou busca online com `USDA_API_KEY`.
Fatores FC/FCoc: planilha com colunas alimento, fator de correção, fator de cocção (`importar-fatores`).

## Aviso
Ferramenta de apoio ao profissional. Não faz diagnóstico nosológico (Lei 8.234/1991; Rec. CFN 005/2016). Regras e fórmulas devem ser revisadas periodicamente. Faça revisão jurídica (LGPD, prontuário, eventual enquadramento como software médico — ANVISA RDC 657/2022) antes do uso com pacientes reais.
