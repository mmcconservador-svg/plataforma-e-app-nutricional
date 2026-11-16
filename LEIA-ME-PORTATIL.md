# Nutri Saúde Integral — versão portátil (HD externo)

## Como usar em qualquer computador
1. Instale o **Python 3.10 ou mais novo** (https://www.python.org/downloads/ — no Windows marque "Add to PATH").
2. Abra esta pasta e dê duplo clique em **`iniciar.bat`** (Windows) ou execute **`./iniciar.sh`** (Linux/macOS).
3. No primeiro uso o sistema cria seu usuário profissional (e-mail e senha) e abre `http://127.0.0.1:5000`.
4. Para encerrar, feche a janela do terminal.

Na primeira vez em **cada computador novo** é preciso internet para instalar as bibliotecas (ficam no PC, não no HD).
Para uso 100% offline, coloque as bibliotecas baixadas na pasta `wheels/` (o script usa essa pasta automaticamente).

## Onde ficam seus dados
| Pasta | Conteúdo |
|---|---|
| `dados/` | banco de dados dos pacientes (criptografado nos campos sensíveis) |
| `chaves/` | chaves de criptografia — **sem elas os dados ficam ilegíveis** |

## Segurança do HD (LGPD)
- **Criptografe o HD inteiro** (BitLocker no Windows, VeraCrypt, LUKS/FileVault). Se o HD for perdido ou roubado, é um incidente com dados de saúde.
- **Guarde a pasta `chaves/` separada do `dados/`** (outro pendrive) e faça cópia das duas em locais diferentes.
- Faça **backup periódico** copiando `dados/` e `chaves/` e teste restaurá-los.
- Não deixe o servidor aberto para a rede: por padrão ele só aceita acesso deste computador (`127.0.0.1`).
- Ao usar em computador de terceiros, encerre o sistema e **não salve** senhas no navegador.

## Conteúdo do pacote
Código-fonte completo (`app.py`, `calc.py`, `automacao.py`, `modulos_clinicos.py`, `report.py`, `security.py`, `models.py`, `templates/`, `static/`), testes (`tests/`) e este guia.
Para testar: `python -m unittest discover -s tests`.
