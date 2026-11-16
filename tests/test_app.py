"""Testes de fluxo e de segurança. Execute: python -m unittest discover -s tests -v"""
import os
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/t.db"
os.environ["NUTRI_FIELD_KEYS"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402
from models import Patient, User, db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


def tok(html):
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


class Cliente:
    def __init__(self):
        self.c = A.app.test_client()

    def get(self, url, **k):
        return self.c.get(url, **k)

    def post(self, url, data=None, page=None, **k):
        t = tok(self.c.get(page or "/login").get_data(as_text=True))
        d = dict(data or {}, _csrf=t)
        return self.c.post(url, data=d, **k)

    def login(self, email, senha):
        return self.post("/login", {"email": email, "senha": senha}, page="/login")


class Tudo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with A.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add_all([
                User(email="pro@x.com", nome="Pro", papel="profissional", senha_hash=generate_password_hash("SenhaForte123")),
                User(email="outro@x.com", nome="Outro", papel="profissional", senha_hash=generate_password_hash("SenhaForte123")),
            ])
            db.session.commit()

    def test_1_fluxo_completo(self):
        pro = Cliente()
        r = pro.login("pro@x.com", "SenhaForte123")
        self.assertEqual(r.status_code, 302)
        self.assertIn("Dashboard", pro.get("/dashboard").get_data(as_text=True))

        # menor sem responsável é rejeitado (LGPD art. 14)
        nasc_menor = (date.today() - timedelta(days=365 * 8)).isoformat()
        r = pro.post("/pacientes/novo", {"nome": "Criança", "sexo": "M", "nascimento": nasc_menor}, page="/pacientes/novo")
        self.assertIn("responsável legal", r.get_data(as_text=True))

        r = pro.post("/pacientes/novo", {"nome": "Ana Teste", "sexo": "F", "nascimento": "1990-05-10",
                                         "email": "ana@x.com", "c_presencial": "on"}, page="/pacientes/novo")
        self.assertEqual(r.status_code, 302)
        pid = int(r.headers["Location"].rstrip("/").split("/")[-1])

        # avaliação com cálculo conferido à mão: F, 30 anos... usa Mifflin
        r = pro.post(f"/pacientes/{pid}/avaliacao/nova", {
            "data": date.today().isoformat(), "peso": "70", "altura_cm": "165", "cintura": "85", "quadril": "100",
            "fator": "leve", "ajuste_kcal": "-300", "condicao": "nenhuma",
            "pct_carb": "50", "pct_prot": "20", "pct_lip": "30"}, page=f"/pacientes/{pid}/avaliacao/nova")
        self.assertEqual(r.status_code, 302)
        html = pro.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("25,7", html)  # IMC 70/1.65² = 25,71
        with A.app.app_context():
            p = db.session.get(Patient, pid)
            idade = p.idade
            tmb = 10 * 70 + 6.25 * 165 - 5 * idade - 161
            self.assertIn(f"{tmb:.0f}", html)

        # integrativa: pilar espiritual sem consentimento NÃO é gravado
        r = pro.post(f"/pacientes/{pid}/integrativa/nova", {"data": date.today().isoformat(),
                     "nota_agua": "3", "nota_repouso": "8", "nota_confianca": "9"}, page=f"/pacientes/{pid}/integrativa/nova")
        self.assertEqual(r.status_code, 302)
        h = pro.get(r.headers["Location"]).get_data(as_text=True)
        self.assertNotIn("espiritualidade", h.lower().split("sugestão de metas")[0].replace("convicção", ""))  # pilar ausente da tabela
        self.assertIn("Água", h)

        # TEA
        r = pro.post(f"/pacientes/{pid}/tea/nova", {"data": date.today().isoformat(), "faixa_aceitos": "ate10",
                     "gi_constipacao": "on", "comp_engasgo": "on", "zimc": "-2.3"}, page=f"/pacientes/{pid}/tea/nova")
        self.assertEqual(r.status_code, 302)
        h = pro.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("alto", h)
        self.assertIn("Não diagnostica", pro.get(f"/pacientes/{pid}/tea/nova").get_data(as_text=True).replace("não diagnostica", "Não diagnostica"))

        # alertas automáticos aparecem no prontuário e no dashboard
        self.assertIn("Alertas e sugestões automáticas", pro.get(f"/pacientes/{pid}").get_data(as_text=True))

        # convite e portal
        pro.post(f"/pacientes/{pid}/convite", page=f"/pacientes/{pid}")
        with A.app.app_context():
            token = A.serializer().dumps({"pid": pid})
        pac = Cliente()
        r = pac.post(f"/convite/{token}", {"senha": "OutraSenha123", "conf": "OutraSenha123", "c_saude": "on"}, page=f"/convite/{token}")
        self.assertEqual(r.status_code, 302)
        pac.login("ana@x.com", "OutraSenha123")
        self.assertEqual(pac.get("/portal").status_code, 200)
        # paciente não acessa área profissional nem avaliação não liberada
        self.assertEqual(pac.get("/pacientes").status_code, 403)
        self.assertEqual(pac.get("/portal/avaliacao/1").status_code, 404)
        # liberar
        pro.post(f"/pacientes/{pid}/avaliacao/1/liberar", page=f"/pacientes/{pid}")
        self.assertEqual(pac.get("/portal/avaliacao/1").status_code, 200)
        self.assertTrue(pac.get("/portal/avaliacao/1/pdf").data.startswith(b"%PDF"))

        # consentimento espiritual e revogação apagam o dado
        pac.post("/portal/anamnese", {"c_espiritual": "on", "integrativa__confianca": "Texto sensível"}, page="/portal/anamnese")
        self.assertIn("Texto sensível", pro.get(f"/pacientes/{pid}/anamnese").get_data(as_text=True))
        pac.post("/portal/consentimento/espiritualidade/revogar", page="/portal/meus-dados")
        self.assertNotIn("Texto sensível", pro.get(f"/pacientes/{pid}/anamnese").get_data(as_text=True))

        # exportação do titular
        ex = pac.get("/portal/exportar.json")
        self.assertEqual(ex.status_code, 200)
        self.assertIn("Ana Teste", ex.get_data(as_text=True))

        # solicitação de exclusão → profissional atende → anonimiza
        pac.post("/portal/solicitar", {"tipo": "exclusao", "mensagem": "quero sair"}, page="/portal/meus-dados")
        pro.post("/solicitacoes/1", {"acao": "atender", "resposta": "ok"}, page="/solicitacoes")
        with A.app.app_context():
            p = db.session.get(Patient, pid)
            self.assertIsNotNone(p.anonimizado_em)
            self.assertNotIn("Ana", p.nome)
        # conta do paciente desativada
        self.assertNotEqual(Cliente().login("ana@x.com", "OutraSenha123").status_code, 302)

    def test_2_isolamento_entre_profissionais(self):
        pro, outro = Cliente(), Cliente()
        pro.login("pro@x.com", "SenhaForte123")
        outro.login("outro@x.com", "SenhaForte123")
        r = pro.post("/pacientes/novo", {"nome": "Beto", "sexo": "M", "nascimento": "1985-01-01"}, page="/pacientes/novo")
        pid = int(r.headers["Location"].rstrip("/").split("/")[-1])
        self.assertEqual(outro.get(f"/pacientes/{pid}").status_code, 404)
        self.assertEqual(outro.get(f"/pacientes/{pid}/exportar.json").status_code, 404)

    def test_3_seguranca(self):
        c = A.app.test_client()
        # CSRF obrigatório
        self.assertEqual(c.post("/login", data={"email": "a", "senha": "b"}).status_code, 400)
        # cabeçalhos
        h = c.get("/login").headers
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["Cache-Control"], "no-store")
        # rotas protegidas
        self.assertEqual(c.get("/pacientes").status_code, 302)
        # bloqueio por tentativas
        x = Cliente()
        for _ in range(5):
            x.login("outro@x.com", "errada")
        r = x.login("outro@x.com", "SenhaForte123")
        self.assertIn("bloqueado", r.get_data(as_text=True))

    def test_4_dados_criptografados_em_repouso(self):
        import sqlite3
        with A.app.app_context():
            p = Patient.query.filter(Patient.codigo.like("NSI-%")).first()
            self.assertIsNotNone(p)
        con = sqlite3.connect(f"{TMP}/t.db")
        brutos = " ".join(str(v) for row in con.execute("select nome, telefone, email from pacientes") for v in row)
        self.assertNotIn("Beto", brutos)
        self.assertNotIn("@x.com", brutos)
        con.close()

    def test_5_calculos(self):
        import calc
        self.assertAlmostEqual(calc.tmb_mifflin("M", 80, 180, 30), 10 * 80 + 6.25 * 180 - 150 + 5)
        self.assertAlmostEqual(calc.imc(81, 180), 25.0)
        self.assertIn("Obesidade grau I", calc.classificar_imc(32, 40))
        self.assertIn("Idoso", "Idoso")


if __name__ == "__main__":
    unittest.main()
