"""USDA FoodData Central: importação do pacote CSV (SR Legacy / Foundation) e consulta pela API.

Os dados do FDC são de domínio público (CC0). A API exige chave gratuita (https://fdc.nal.usda.gov/api-key-signup)
em USDA_API_KEY. Nomes ficam em inglês. Somente o termo de busca é enviado à API — nenhum dado de paciente.
"""
import csv
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from models import Alimento, FoodSource, MedidaCaseira, agora, db
from security import normalizar

API = "https://api.nal.usda.gov/fdc/v1"
TIPOS_PADRAO = ("foundation_food", "sr_legacy_food")

# nutrient_nbr (nº clássico SR) -> (campo|None, chave micro, unidade esperada)
MAPA = {
    "208": ("energia_kcal", None, "kcal"), "203": ("proteina_g", None, "g"), "204": ("lipidio_g", None, "g"),
    "205": ("carbo_g", None, "g"), "291": ("fibra_g", None, "g"),
    "301": (None, "calcio_mg", "mg"), "303": (None, "ferro_mg", "mg"), "304": (None, "magnesio_mg", "mg"),
    "305": (None, "fosforo_mg", "mg"), "306": (None, "potassio_mg", "mg"), "307": (None, "sodio_mg", "mg"),
    "309": (None, "zinco_mg", "mg"), "312": (None, "cobre_mg", "mg"), "315": (None, "manganes_mg", "mg"),
    "317": (None, "selenio_mcg", "mcg"), "320": (None, "rae_mcg", "mcg"), "323": (None, "vitamina_e_mg", "mg"),
    "328": (None, "vitamina_d_mcg", "mcg"), "401": (None, "vitamina_c_mg", "mg"), "404": (None, "tiamina_mg", "mg"),
    "405": (None, "riboflavina_mg", "mg"), "406": (None, "niacina_mg", "mg"), "415": (None, "piridoxina_mg", "mg"),
    "418": (None, "vitamina_b12_mcg", "mcg"), "430": (None, "vitamina_k_mcg", "mcg"),
    "435": (None, "folato_dfe_mcg", "mcg"), "601": (None, "colesterol_mg", "mg"), "606": (None, "saturados_g", "g"),
}
ENERGIA_ALT = ("957", "958")  # Atwater, usados quando 208 não existe


def _unidade(u):
    u = (u or "").lower()
    return {"ug": "mcg", "µg": "mcg", "μg": "mcg"}.get(u, u)


def _fonte(usuario_id, versao):
    f = FoodSource.query.filter_by(codigo="USDA").first() or FoodSource(codigo="USDA", nome="USDA FoodData Central")
    f.nome, f.versao = "USDA FoodData Central", versao or f.versao
    f.licenca_confirmada, f.licenca_obs = True, "Domínio público (CC0) — USDA FDC"
    f.importado_em, f.importado_por = agora(), usuario_id
    db.session.add(f)
    db.session.flush()
    return f


def _aplicar(a, valores):
    """valores: {nbr: (valor, unidade)} -> campos e micros do alimento (unidade divergente é ignorada)."""
    micros, energia_alt = {}, None
    for nbr, (v, un) in valores.items():
        un = _unidade(un)
        if nbr in ENERGIA_ALT:
            if un == "kcal":
                energia_alt = v
            continue
        campo, chave, esperado = MAPA[nbr]
        if un and un != esperado:
            continue
        if campo:
            setattr(a, campo, v)
        else:
            micros[chave] = v
    if a.energia_kcal is None and energia_alt is not None:
        a.energia_kcal = energia_alt
    a.micros = json.dumps(micros, ensure_ascii=False) if micros else None


def _novo_ou_existente(fonte, codigo, nome, grupo):
    a = Alimento.query.filter_by(fonte_id=fonte.id, codigo=codigo, owner_id=None).first() or Alimento(fonte_id=fonte.id, codigo=codigo)
    a.nome, a.nome_norm, a.grupo, a.ativo = nome[:300], normalizar(nome)[:300], (grupo or None), True
    for c in ("energia_kcal", "proteina_g", "lipidio_g", "carbo_g", "fibra_g"):
        setattr(a, c, None)
    return a


def _desc_medida(amount, unidade, modificador, descricao):
    partes = [p for p in (descricao, modificador) if p]
    if not partes:
        partes = [f"{amount:g} {unidade}".strip()] if amount else [unidade or "porção"]
    elif unidade and unidade.lower() not in ("undetermined", ""):
        partes.insert(0, f"{amount:g} {unidade}" if amount else unidade)
    return " ".join(partes)[:120]


def importar_csv(pasta, usuario_id=None, tipos=TIPOS_PADRAO):
    pasta = Path(pasta)
    for obrig in ("food.csv", "nutrient.csv", "food_nutrient.csv"):
        if not (pasta / obrig).exists():
            raise ValueError(f"Arquivo ausente na pasta: {obrig}")
    versao = ""
    fonte = _fonte(usuario_id, versao)

    def ler(nome):
        with open(pasta / nome, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)

    nutr = {r["id"]: (r["nutrient_nbr"], r["unit_name"]) for r in ler("nutrient.csv") if r.get("nutrient_nbr")}
    desejados = {nid for nid, (nbr, _u) in nutr.items() if nbr in MAPA or nbr in ENERGIA_ALT}
    cats = {r["id"]: r.get("description", "") for r in ler("food_category.csv")} if (pasta / "food_category.csv").exists() else {}
    foods = {}
    for r in ler("food.csv"):
        if r.get("data_type") in tipos:
            foods[r["fdc_id"]] = (r["description"], cats.get(r.get("food_category_id", ""), r.get("food_category_id") or ""))
    vals = {fid: {} for fid in foods}
    for r in ler("food_nutrient.csv"):
        fid, nid = r.get("fdc_id"), r.get("nutrient_id")
        if fid in vals and nid in desejados:
            try:
                v = float(r["amount"])
            except (ValueError, KeyError):
                continue
            nbr, un = nutr[nid]
            vals[fid][nbr] = (v, un)
    st = {"novos": 0, "atualizados": 0, "medidas": 0}
    existentes = {a.codigo: a for a in Alimento.query.filter_by(fonte_id=fonte.id, owner_id=None).all()}
    objs = {}
    for fid, (nome, grupo) in foods.items():
        novo = fid not in existentes
        a = existentes.get(fid) or Alimento(fonte_id=fonte.id, codigo=fid)
        a.nome, a.nome_norm, a.grupo, a.ativo = nome[:300], normalizar(nome)[:300], (grupo or None)[:120] if grupo else None, True
        for c in ("energia_kcal", "proteina_g", "lipidio_g", "carbo_g", "fibra_g"):
            setattr(a, c, None)
        _aplicar(a, vals[fid])
        db.session.add(a)
        objs[fid] = a
        st["novos" if novo else "atualizados"] += 1
    db.session.flush()
    if (pasta / "food_portion.csv").exists():
        unidades = {r["id"]: (r.get("name") or "") for r in ler("measure_unit.csv")} if (pasta / "measure_unit.csv").exists() else {}
        ja = {(m.alimento_id, normalizar(m.descricao)) for m in MedidaCaseira.query.filter_by(fonte="USDA").all()}
        for r in ler("food_portion.csv"):
            a = objs.get(r.get("fdc_id"))
            if a is None:
                continue
            try:
                gw = float(r["gram_weight"])
                amount = float(r.get("amount") or 1) or 1.0
            except (ValueError, KeyError):
                continue
            if not 0 < gw <= 5000:
                continue
            desc = _desc_medida(amount, unidades.get(r.get("measure_unit_id", ""), ""), r.get("modifier", ""),
                                r.get("portion_description", ""))
            chave = (a.id, normalizar(desc))
            if chave in ja:
                continue
            ja.add(chave)
            db.session.add(MedidaCaseira(alimento_id=a.id, descricao=desc, gramas=round(gw, 2), fonte="USDA"))
            st["medidas"] += 1
    db.session.commit()
    return st


# ───────────────────────── API ─────────────────────────
def _chave():
    k = os.environ.get("USDA_API_KEY", "").strip()
    if not k:
        raise ValueError("Defina USDA_API_KEY no arquivo .env (chave gratuita em fdc.nal.usda.gov/api-key-signup).")
    return k


def _get(caminho, params):
    params = dict(params, api_key=_chave())
    url = f"{API}{caminho}?{urllib.parse.urlencode(params)}"
    if not url.startswith("https://api.nal.usda.gov/"):
        raise ValueError("URL inválida.")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "nutri-saude-integral"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (host fixo, HTTPS)
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Falha ao consultar a API do USDA: {type(e).__name__}") from None


def buscar_api(q, limite=15):
    d = _get("/foods/search", {"query": q, "pageSize": limite, "dataType": "Foundation,SR Legacy"})
    return [{"fdc_id": f.get("fdcId"), "nome": f.get("description", ""), "tipo": f.get("dataType", ""),
             "categoria": f.get("foodCategory", "")} for f in d.get("foods", [])]


def _valores_api(food):
    out = {}
    for n in food.get("foodNutrients", []):
        info = n.get("nutrient") or {}
        nbr = str(info.get("number") or n.get("nutrientNumber") or "")
        v = n.get("amount", n.get("value"))
        un = info.get("unitName") or n.get("unitName") or ""
        if nbr and (nbr in MAPA or nbr in ENERGIA_ALT) and isinstance(v, (int, float)):
            out[nbr] = (float(v), un)
    return out


def importar_api(fdc_id, usuario_id=None):
    food = _get(f"/food/{int(fdc_id)}", {})
    fonte = _fonte(usuario_id, "")
    cat = (food.get("foodCategory") or {})
    grupo = cat.get("description") if isinstance(cat, dict) else str(cat or "")
    a = _novo_ou_existente(fonte, str(food["fdcId"]), food.get("description", "sem nome"), grupo)
    _aplicar(a, _valores_api(food))
    db.session.add(a)
    db.session.flush()
    ja = {normalizar(m.descricao) for m in a.medidas}
    for p in food.get("foodPortions", []):
        try:
            gw = float(p["gramWeight"])
            amount = float(p.get("amount") or 1) or 1.0
        except (ValueError, KeyError, TypeError):
            continue
        if not 0 < gw <= 5000:
            continue
        desc = _desc_medida(amount, (p.get("measureUnit") or {}).get("name", ""), p.get("modifier", ""),
                            p.get("portionDescription", ""))
        if normalizar(desc) not in ja:
            ja.add(normalizar(desc))
            a.medidas.append(MedidaCaseira(descricao=desc, gramas=round(gw, 2), fonte="USDA"))
    db.session.commit()
    return a
