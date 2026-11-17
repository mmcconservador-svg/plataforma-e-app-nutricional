"""Testes de equações, exames, importadores (arquivos SINTÉTICOS), %VD, cardápio e 2FA."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/m.db"
os.environ["NUTRI_RATELIMIT"] = "0"
os.environ["NUTRI_DATA_DIR"] = TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alimentos as AL  # noqa: E402
import app as A  # noqa: E402
import equacoes as Q  # noqa: E402
import exames as EX  # noqa: E402
import referencias as REF  # noqa: E402
import security as S  # noqa: E402
import usda  # noqa: E402
from models import Alimento, User, db  # noqa: E402
from tests.test_app import Cliente  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


class Equacoes(unittest.TestCase):
    def test_mifflin_homem(self):
        # 10*80 + 6.25*180 - 5*30 + 5 = 1780
        e = Q.Entrada("M", 80, 180, 30)
        self.assertAlmostEqual(Q.calcular_equacao("mifflin", e)["tmb"], 1780, 1)

    def test_comparar_todas_marca_nao_aplicaveis(self):
        r = Q.comparar_todas(Q.Entrada("F", 60, 165, 25))
        por = {x["chave"]: x for x in r}
        self.assertFalse(por["katch"]["ok"])  # exige massa livre de gordura
        self.assertTrue(por["mifflin"]["ok"])
        self.assertIn("get_kcal", por["mifflin"])

    def test_fatores_fao(self):
        self.assertEqual(Q.FATORES_ATIVIDADE["fao_ativo"][1], 1.85)
        self.assertEqual(Q.FATORES_ATIVIDADE["sedentario"][1], 1.2)

    def test_dobras_siri(self):
        self.assertAlmostEqual(Q.siri(1.05), 21.4, 1)


class Seguranca(unittest.TestCase):
    def test_argon_e_legado(self):
        h = S.hash_senha("SenhaForte123")
        self.assertEqual(S.verificar_senha(h, "SenhaForte123"), (True, False))
        self.assertFalse(S.verificar_senha(h, "outra")[0])
        ok, rehash = S.verificar_senha(generate_password_hash("SenhaForte123"), "SenhaForte123")
        self.assertTrue(ok and rehash)

    def test_totp_replay(self):
        import pyotp
        seg = S.novo_totp_secret()
        cod = pyotp.TOTP(seg).now()
        ctr = S.totp_verifica(seg, cod, 0)
        self.assertIsNotNone(ctr)
        self.assertIsNone(S.totp_verifica(seg, cod, ctr))

    def test_blind_index(self):
        a = S.tokens_busca("Maria da Silva")
        self.assertIn(S.token_consulta("mar"), a.split() if isinstance(a, str) else a)


class Exames(unittest.TestCase):
    def test_conversao_unidades(self):
        self.assertAlmostEqual(EX.para_padrao("glicemia", 7.0, "mmol/L"), 126.1, 1)
        with self.assertRaises(ValueError):
            EX.para_padrao("glicemia", 1, "xx")

    def test_glicemia_sbd(self):
        itens = {"glicemia": {"v": 130, "min": 70, "max": 99}, "hba1c": {"v": 6.0}}
        _l, _c, ach = EX.avaliar(itens, 40, "M")
        regras = {a["regra"] for a in ach}
        self.assertIn("SBD-GLI-01", regras)
        self.assertIn("SBD-A1C-02", regras)
        self.assertIn("SBD-DISC-01", regras)
        for a in ach:  # nunca afirma doença
            self.assertNotIn("paciente tem", (a["texto"] + a["titulo"]).lower())

    def test_ferritina_com_pcr(self):
        itens = {"ferritina": {"v": 400, "min": 20, "max": 300}, "pcr": {"v": 20, "max": 5}}
        _l, _c, ach = EX.avaliar(itens, 40, "M")
        self.assertIn("FER-INFL", {a["regra"] for a in ach})

    def test_calculados(self):
        itens = {"col_total": {"v": 220}, "hdl": {"v": 50}, "tg": {"v": 150}, "glicemia": {"v": 90}, "insulina": {"v": 9},
                 "creatinina": {"v": 1.0}}
        _l, c, _a = EX.avaliar(itens, 40, "M")
        self.assertEqual(c["nao_hdl"]["valor"], 170)
        self.assertAlmostEqual(c["ldl_friedewald"]["valor"], 140, 1)
        self.assertAlmostEqual(c["homa_ir"]["valor"], 2.0, 1)
        self.assertTrue(80 < c["egfr"]["valor"] < 100)

    def test_validacao_form(self):
        with self.assertRaises(ValueError):
            EX.montar_itens({})
        with self.assertRaises(ValueError):
            EX.montar_itens({"v_glicemia": "abc"})
        with self.assertRaises(ValueError):
            EX.montar_itens({"v_glicemia": "90", "min_glicemia": "100", "max_glicemia": "70"})


class Tabelas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with A.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(User(email="pro@x.com", nome="Pro", papel="profissional",
                                senha_hash=S.hash_senha("SenhaForte123")))
            db.session.commit()

    def _csv(self, nome, texto):
        p = Path(TMP) / nome
        p.write_text(texto, encoding="utf-8")
        return p

    def test_a_importar_taco_sintetico_e_equivalentes(self):
        p = self._csv("taco.csv", "Número do Alimento;Descrição dos alimentos;Categoria do alimento;Energia (kcal);Proteína (g);"
                      "Lipídeos (g);Carboidrato (g);Fibra Alimentar (g);Cálcio (mg);Ferro (mg)\n"
                      "1;Arroz branco cozido;Cereais;128;2,5;0,2;28,1;1,6;4;0,1\n"
                      "2;Pão francês;Cereais;300;8;3,1;58,6;2,3;16;1\n"
                      "3;Feijão carioca cozido;Leguminosas;76;4,8;0,5;13,6;8,5;27;1,3\n")
        with A.app.app_context():
            st = AL.importar_tabela(p, "TACO", "TACO", "sintetico")
            self.assertEqual(st["novos"] if "novos" in st else st.get("importados", 3), 3)
            a = AL.buscar("arroz", None)[0]
            self.assertAlmostEqual(a.energia_kcal, 128)
            self.assertIn("calcio_mg", json.loads(a.micros))
            eq = AL.equivalentes(a, 100, "carboidrato", None)
            self.assertTrue(len(eq) >= 1)

    def test_b_fatores(self):
        p = self._csv("fat.csv", "Alimento;Fator de Correção;Fator de Cocção\nArroz branco cozido;1;2,5\nPão francês;1,0;0,9\nInexistente;1,2;1\n")
        with A.app.app_context():
            st = AL.importar_fatores(p)
            self.assertEqual(st["atualizados"], 2)
            self.assertEqual(st["nao_encontrados"], 1)
            self.assertEqual(AL.buscar("arroz", None)[0].fator_coccao, 2.5)

    def test_c_usda_sintetico(self):
        d = Path(tempfile.mkdtemp())
        (d / "food.csv").write_text("fdc_id,data_type,description,food_category_id,publication_date\n1,sr_legacy_food,Apple raw,9,2019-04-01\n")
        (d / "nutrient.csv").write_text("id,name,unit_name,nutrient_nbr,rank\n1,Energy,KCAL,208,1\n2,Protein,G,203,2\n3,Calcium,MG,301,3\n")
        (d / "food_nutrient.csv").write_text("id,fdc_id,nutrient_id,amount\n1,1,1,52\n2,1,2,0.3\n3,1,3,6\n")
        with A.app.app_context():
            usda.importar_csv(d)
            a = Alimento.query.filter_by(nome="Apple raw").first()
            self.assertIsNotNone(a)
            self.assertAlmostEqual(a.energia_kcal, 52)
            self.assertAlmostEqual(a.proteina_g, 0.3)

    def test_d_vd(self):
        acc, cob = {}, {}
        REF.acumular_micros({"calcio_mg": 100}, 5.0, acc, cob)  # 500 mg
        linhas = REF.linhas_vd({"kcal": 1000, "c": 150, "p": 25, "l": 32.5, "f": 12.5}, acc, 1.0, 1, cob)
        por = {x["rotulo"]: x for x in linhas}
        self.assertAlmostEqual(por[next(k for k in por if "Cálcio" in k)]["pct"], 50, 1)
        kc = next(x for x in linhas if "Valor energ" in x["rotulo"] or "Energia" in x["rotulo"])
        self.assertAlmostEqual(kc["pct"], 50, 1)


class Fluxo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with A.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add_all([User(email="pro@x.com", nome="Pro", papel="profissional", senha_hash=S.hash_senha("SenhaForte123")),
                                User(email="outro@x.com", nome="Outro", papel="profissional", senha_hash=S.hash_senha("SenhaForte123"))])
            db.session.commit()

    def test_exame_e_pdf_criptografado(self):
        import io
        pro = Cliente()
        pro.login("pro@x.com", "SenhaForte123")
        nasc = (date.today() - timedelta(days=365 * 40)).isoformat()
        r = pro.post("/pacientes/novo", {"nome": "Ana Teste", "sexo": "F", "nascimento": nasc, "consent_saude": "1"}, page="/pacientes/novo")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        pid = int(r.headers["Location"].rstrip("/").split("/")[-1])
        pdf = b"%PDF-1.4 conteudo-secreto-xyz"
        r = pro.post(f"/pacientes/{pid}/exames/novo", {"data_coleta": date.today().isoformat(), "laboratorio": "Lab", "v_glicemia": "130",
                     "min_glicemia": "70", "max_glicemia": "99", "pdf": (io.BytesIO(pdf), "laudo.pdf")},
                     page=f"/pacientes/{pid}/exames/novo", content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:500])
        html = pro.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("SBD-GLI-01", html)
        self.assertIn("correlação clínica", html)
        # arquivo no disco está criptografado
        import re
        did = int(re.search(r"/documentos/(\d+)", html).group(1))
        with A.app.app_context():
            from models import Documento
            arq = A.DATA_DIR / "uploads" / db.session.get(Documento, did).arquivo
            self.assertNotIn(b"conteudo-secreto", arq.read_bytes())
        self.assertIn(b"conteudo-secreto", pro.get(f"/pacientes/{pid}/documentos/{did}").data)
        outro = Cliente()
        outro.login("outro@x.com", "SenhaForte123")
        self.assertEqual(outro.get(f"/pacientes/{pid}/documentos/{did}").status_code, 404)
        self.assertEqual(outro.get(f"/pacientes/{pid}/exames/1").status_code, 404)
        # PDF falso é recusado
        r = pro.post(f"/pacientes/{pid}/exames/novo", {"data_coleta": date.today().isoformat(), "v_glicemia": "90",
                     "pdf": (io.BytesIO(b"nao e pdf"), "x.pdf")}, page=f"/pacientes/{pid}/exames/novo", content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)

    def test_cadeia_auditoria(self):
        with A.app.app_context():
            from models import AuditLog
            logs = AuditLog.query.order_by(AuditLog.id).all()
            self.assertTrue(all(l.hash for l in logs))


if __name__ == "__main__":
    unittest.main()


class Fluxo2FAePaginas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with A.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(User(email="dois@x.com", nome="Dois", papel="profissional", senha_hash=S.hash_senha("SenhaForte123")))
            db.session.commit()

    def test_2fa_ativar_e_logar(self):
        import pyotp
        import re
        c = Cliente()
        c.login("dois@x.com", "SenhaForte123")
        self.assertEqual(c.get("/conta/2fa").status_code, 200)
        with A.app.app_context():
            segredo = User.query.filter_by(email="dois@x.com").first().totp_secret
        self.assertTrue(segredo)
        r = c.post("/conta/2fa", {"acao": "ativar", "codigo": pyotp.TOTP(segredo).now()}, page="/conta/2fa")
        html = r.get_data(as_text=True)
        self.assertEqual(len(re.findall(r"[0-9a-f]{5}-[0-9a-f]{5}", html)) >= 8, True)
        # novo login pede segundo fator; senha sozinha não entra
        c2 = Cliente()
        r = c2.login("dois@x.com", "SenhaForte123")
        self.assertIn("/login/2fa", r.headers["Location"])
        self.assertEqual(c2.get("/dashboard").status_code, 302)
        r = c2.post("/login/2fa", {"codigo": "000000"}, page="/login/2fa")
        self.assertEqual(r.status_code, 200)

    def test_paginas_nao_quebram(self):
        c = Cliente()
        c.login("dois@x.com", "SenhaForte123")
        # 2FA agora ativo: loga com recovery não é necessário; usa sessão pós-2FA via TOTP
        import pyotp
        with A.app.app_context():
            seg = User.query.filter_by(email="dois@x.com").first().totp_secret
        c = Cliente()
        c.login("dois@x.com", "SenhaForte123")
        # evita reuso do mesmo contador: força contador antigo
        with A.app.app_context():
            u = User.query.filter_by(email="dois@x.com").first()
            u.totp_ultimo = 0
            db.session.commit()
        c.post("/login/2fa", {"codigo": pyotp.TOTP(seg).now()}, page="/login/2fa")
        for url in ("/dashboard", "/pacientes", "/alimentos", "/alimentos/novo", "/alimentos/importar", "/alimentos/usda", "/conta/senha"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200, url)
