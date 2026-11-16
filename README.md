# Nutri Saúde Integral — plataforma de avaliação nutricional (PC + app)

Aplicativo web responsivo (Flask) que funciona no computador e **instala como app no celular (PWA)**.
Dois perfis: **profissional** e **paciente** (portal com login individual).

## Como rodar

```bash
pip install -r requirements.txt
flask --app app criar-profissional     # cria seu usuário (senha mín. 10 caracteres, letras e números)
python app.py                          # http://localhost:5000
python -m unittest discover -s tests   # testes
```

Instalar no celular: abra o endereço (em produção, via HTTPS) no navegador → "Adicionar à tela inicial".
Para Play Store/Android nativo, o mesmo site pode ser empacotado depois (TWA/Capacitor).

## Módulos

- **Dashboard**: indicadores, estado nutricional, pendências e alertas.
- **Prontuário individual**: cadastro, histórico de versões, exportação JSON, PDF.
- **Anamnese** em 16 abas + saúde integrativa; o paciente pode preencher no portal; você revisa.
- **Antropometria e cálculos**: IMC, RCQ, RCE, TMB (Mifflin-St Jeor), GET, VET, macros, fibras, água, avisos de limitação (crianças, idosos, gestantes, atletas).
- **Avaliação integrativa**: oito remédios naturais (notas 0–10, comparação entre datas, metas sugeridas).
- **Avaliação alimentar no TEA**: repertório, seletividade, sensorial, comportamento, GI, escore-z (OMS) e triagem interna.
- **Automação inteligente**: regras transparentes (reavaliação em 30 dias, variação de peso, pendências, prazos LGPD…). Não usa IA externa, não diagnostica e nada vai ao paciente sem sua liberação.

## LGPD e segurança implementadas

| Requisito | Como está feito |
|---|---|
| Dados sensíveis de saúde | Campos criptografados em repouso (Fernet/AES); chaves em `NUTRI_FIELD_KEYS` ou `instance/field.key` |
| Consentimento | Registro imutável com tipo, versão, origem e data; revogar = nova linha |
| Dado espiritual (religião) | Consentimento específico e opcional; sem ele o campo nem é gravado; revogar apaga de todas as versões |
| Menores (art. 14) | Responsável legal obrigatório para menores de 18 anos |
| Direitos do titular (art. 18) | Portal "Meus dados e direitos": baixar dados, corrigir, informar, excluir, revogar; fila de solicitações com prazo |
| Eliminação | Anonimização irreversível (confirmação por código) |
| Controle de acesso | Papéis; cada paciente só vê o próprio portal; cada profissional só vê seus pacientes |
| Validação profissional | Paciente só vê avaliações que você liberar |
| Autenticação | Hash de senha, bloqueio após 5 tentativas, sessão expira em 30 min, CSRF, cookies HttpOnly/SameSite |
| Auditoria | Log de acessos e alterações (sem conteúdo clínico) |
| Navegador/app | CSP restritiva, `no-store`, service worker que nunca guarda páginas de saúde |
| Política de privacidade | Modelo em `/privacidade` (**revisar com advogado**) |

## Antes de usar com pacientes reais (produção)

1. Hospedar com **HTTPS** e `NUTRI_ENV=production`; rodar com `gunicorn app:app`.
2. Definir `SECRET_KEY` e `NUTRI_FIELD_KEYS` por variável de ambiente e **guardar as chaves em local separado do banco** (sem elas os dados ficam ilegíveis). Para rotacionar, coloque a nova chave primeiro, separadas por vírgula.
3. Configurar `DPO_CONTATO` (e-mail do canal do titular), `PRO_NOME`, `PRO_REGISTRO`.
4. Backup automático **criptografado** e testado de `instance/` (ou do MySQL).
5. Revisar a política de privacidade, prazos de guarda do prontuário (CFN) e o RIPD com apoio jurídico.
6. Convites do portal hoje geram um link para você enviar; integrar e-mail/WhatsApp é a próxima etapa.

## Limites conhecidos

- Banco de alimentos (TACO/TBCA), cardápios, exames e pagamentos ainda não estão nesta versão (etapas 2–4 do seu plano).
- Escore-z pediátrico é digitado (OMS); curvas completas não estão embutidas.
- A triagem TEA e os escores integrativos são **ferramentas internas de apoio**, não instrumentos validados nem diagnósticos.
