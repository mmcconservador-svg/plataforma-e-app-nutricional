# Relatório de Impacto (RIPD) — resumo modelo (LGPD art. 38)
Modelo inicial a ser completado e validado pelo controlador com apoio jurídico/DPO.
- **Controlador:** profissional/clínica (definir). **Encarregado (DPO):** definir canal de contato.
- **Dados:** identificação; saúde (antropometria, anamnese, exames, cardápios) — sensíveis (art. 11); espiritualidade — sensível, opcional, consentimento específico; menores — art. 14.
- **Bases legais:** tutela da saúde (art. 11, II, f) e consentimento (art. 11, I); obrigação regulatória de guarda de prontuário — confirmar prazos com CFN/jurídico.
- **Medidas:** criptografia de campos e anexos, índice cego, auditoria encadeada, 2FA, CSRF/CSP, minimização, anonimização, exportação, retenção definida.
- **Riscos residuais:** perda do dispositivo, vazamento de chaves (guardar `keys/` separado do banco), erro humano. Faça backups criptografados.
