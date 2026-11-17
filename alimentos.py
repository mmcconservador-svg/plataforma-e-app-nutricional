"""Banco de alimentos (TACO/TBCA/IBGE/próprios), medidas caseiras, equivalentes e cálculo de cardápios.

As tabelas oficiais NÃO acompanham o sistema: o profissional importa os arquivos que tiver licença
para usar (ver `dados/LEIA-ME.md`). Valores ausentes ("NA", "*") ficam vazios; "Tr" (traço) vira 0.
"""
import csv
import json
import re
from pathlib import Path

from sqlalchemy import case, or_
from sqlalchemy.orm import selectinload

import referencias as REF
from models import Alimento, FoodSource, MedidaCaseira, agora, db
from security import normalizar

CAMPOS = ("energia_kcal", "proteina_g", "lipidio_g", "carbo_g", "fibra_g")
CRITERIOS = {"carboidrato": "carbo_g", "energia": "energia_kcal", "proteina": "proteina_g", "lipidio": "lipidio_g"}
LIM_DIAS, LIM_REFEICOES, LIM_ITENS = 30, 10, 40
TOLERANCIA = 0.10


# ───────────────────────── leitura de tabelas ─────────────────────────
def parse_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("\xa0", "")
    if not t:
        return None
    tl = t.lower()
    if tl in ("tr", "traço", "traco"):
        return 0.0
    if tl in ("na", "*", "-", "--", "nd", "n/a", "ne", "nan"):
        return None
    if "." in t and "," in t:
        t = t.replace(".", "")
    t = t.replace(",", ".").lstrip("<>~")
    try:
        return float(t)
    except ValueError:
        return None


def _linhas_csv(p):
    bruto = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            bruto = p.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if bruto is None:
        raise ValueError("Não foi possível ler o arquivo (codificação desconhecida).")
    try:
        dialeto = csv.Sniffer().sniff(bruto[:4096], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    return [list(l) for l in csv.reader(bruto.splitlines(), dialeto)]


def _abas(caminho):
    """Lista de abas, cada uma uma lista de linhas (listas de valores)."""
    p = Path(caminho)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        try:
            return [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
        finally:
            wb.close()
    if ext in (".csv", ".txt", ".tsv"):
        return [_linhas_csv(p)]
    raise ValueError("Formato não suportado. Use .csv, .tsv ou .xlsx.")


def _achar_cabecalho(linhas, palavras):
    for i, l in enumerate(linhas[:40]):
        cel = [normalizar(str(c)) for c in l if c not in (None, "")]
        if len(cel) >= 2 and any(any(w in c for w in palavras) for c in cel):
            return i
    return None


def _slug(t):
    return re.sub(r"[^a-z0-9]+", "_", normalizar(t)).strip("_")


def mapear(headers):
    """idx -> ('nome'|'codigo'|'grupo'|campo) ou ('micro', chave)."""
    m, usados, carbo = {}, set(), {}
    for i, h in enumerate(headers):
        orig = str(h or "").strip()
        n = normalizar(orig)
        if not n:
            continue
        if "nome" not in usados and ("descricao" in n or n in ("alimento", "nome", "nome do alimento")):
            m[i] = ("nome",); usados.add("nome")
        elif "codigo" not in usados and ("codigo" in n or "numero do alimento" in n):
            m[i] = ("codigo",); usados.add("codigo")
        elif "grupo" not in usados and any(k in n for k in ("categoria", "grupo", "classe")):
            m[i] = ("grupo",); usados.add("grupo")
        elif "energia" in n and "kcal" in n and "energia_kcal" not in usados:
            m[i] = ("energia_kcal",); usados.add("energia_kcal")
        elif n.startswith("proteina") and "proteina_g" not in usados:
            m[i] = ("proteina_g",); usados.add("proteina_g")
        elif n.startswith("lipid") and "lipidio_g" not in usados:
            m[i] = ("lipidio_g",); usados.add("lipidio_g")
        elif "carboidrato" in n:
            carbo[i] = n
        elif "fibra" in n and "fibra_g" not in usados:
            m[i] = ("fibra_g",); usados.add("fibra_g")
        else:
            mm = re.match(r"^(.+?)\s*\((mg|mcg|µg|μg|ug|g)\)", orig, re.I)
            if mm and "energia" not in n:
                un = mm.group(2).lower().replace("µg", "mcg").replace("μg", "mcg").replace("ug", "mcg")
                m[i] = ("micro", f"{_slug(mm.group(1))}_{un}")
    if carbo:  # prefere "carboidrato disponível" quando existir
        escolhido = next((i for i, n in carbo.items() if "disponivel" in n), next(iter(carbo)))
        m[escolhido] = ("carbo_g",)
    return m


# ───────────────────────── importação ─────────────────────────
def _fonte(codigo, nome, versao, usuario_id, obs):
    f = FoodSource.query.filter_by(codigo=codigo).first() or FoodSource(codigo=codigo, nome=nome)
    f.nome, f.versao = nome or f.nome, versao or f.versao
    f.licenca_confirmada, f.licenca_obs = True, (obs or "Licença de uso confirmada pelo profissional")[:255]
    f.importado_em, f.importado_por = agora(), usuario_id
    db.session.add(f)
    db.session.flush()
    return f


def importar_tabela(caminho, codigo_fonte, nome_fonte, versao="", usuario_id=None, obs=""):
    fonte = _fonte(codigo_fonte, nome_fonte, versao, usuario_id, obs)
    existentes = {(a.codigo or a.nome_norm): a
                  for a in Alimento.query.filter_by(fonte_id=fonte.id, owner_id=None).all()}
    st = {"novos": 0, "atualizados": 0, "ignorados": 0, "inconsistentes": 0, "abas": 0}
    for linhas in _abas(caminho):
        h = _achar_cabecalho(linhas, ("energia", "proteina"))
        if h is None:
            continue
        mapa = mapear(linhas[h])
        if not any(v == ("nome",) for v in mapa.values()):
            continue
        st["abas"] += 1
        idx_nome = next(i for i, v in mapa.items() if v == ("nome",))
        grupo_atual = None
        for row in linhas[h + 1:]:
            row = list(row) + [None] * (len(linhas[h]) - len(row))
            preenchidas = [c for c in row if c not in (None, "")]
            if not preenchidas:
                continue
            if len(preenchidas) == 1 and row[idx_nome] not in (None, ""):
                grupo_atual = str(row[idx_nome]).strip()[:120]  # linha de título de grupo
                continue
            nome = str(row[idx_nome] or "").strip()
            if not nome:
                continue
            dados, micros, codigo, grupo = {}, {}, None, grupo_atual
            for i, v in mapa.items():
                tipo = v[0]
                if tipo == "codigo":
                    codigo = (str(row[i]).strip()[:40] if row[i] not in (None, "") else None)
                elif tipo == "grupo":
                    grupo = (str(row[i]).strip()[:120] if row[i] not in (None, "") else grupo)
                elif tipo == "micro":
                    x = parse_num(row[i])
                    if x is not None:
                        micros[v[1]] = x
                elif tipo in CAMPOS:
                    dados[tipo] = parse_num(row[i])
            if all(dados.get(c) is None for c in CAMPOS):
                st["ignorados"] += 1
                continue
            k, p_, l_, c_ = (dados.get(x) for x in ("energia_kcal", "proteina_g", "lipidio_g", "carbo_g"))
            if None not in (k, p_, l_, c_) and k > 20 and abs(k - (4 * p_ + 4 * c_ + 9 * l_)) / k > 0.30:
                st["inconsistentes"] += 1
            chave = codigo or normalizar(nome)[:300]
            a = existentes.get(chave)
            if a is None:
                a = Alimento(fonte_id=fonte.id, codigo=codigo)
                existentes[chave] = a
                st["novos"] += 1
            else:
                st["atualizados"] += 1
            a.nome, a.nome_norm, a.grupo = nome[:300], normalizar(nome)[:300], grupo
            for c in CAMPOS:
                setattr(a, c, dados.get(c))
            a.micros = json.dumps(micros, ensure_ascii=False) if micros else None
            a.ativo = True
            db.session.add(a)
    db.session.commit()
    return st


def importar_medidas(caminho, codigo_fonte_medidas="IBGE-MEDIDAS", nome_fonte="Medidas caseiras",
                     versao="", usuario_id=None, obs="", fonte_alimentos=None):
    fonte = _fonte(codigo_fonte_medidas, nome_fonte, versao, usuario_id, obs)
    consulta = Alimento.query.filter(Alimento.owner_id.is_(None))
    if fonte_alimentos:
        consulta = consulta.join(FoodSource, Alimento.fonte_id == FoodSource.id).filter(FoodSource.codigo == fonte_alimentos)
    por_cod, por_nome = {}, {}
    for a in consulta.all():
        if a.codigo:
            por_cod[(a.fonte_id, a.codigo)] = a
            por_cod.setdefault(a.codigo, a)
        por_nome.setdefault(a.nome_norm, a)
    st = {"novas": 0, "atualizadas": 0, "nao_encontrados": 0, "exemplos_nao_encontrados": []}
    existentes = {(m.alimento_id, normalizar(m.descricao)): m for m in MedidaCaseira.query.all()}
    for linhas in _abas(caminho):
        h = _achar_cabecalho(linhas, ("medida", "gramas"))
        if h is None:
            continue
        i_cod = i_nome = i_med = i_g = None
        for i, c in enumerate(linhas[h]):
            n = normalizar(str(c or ""))
            if any(k in n for k in ("gramas", "peso", "quantidade g")):
                i_g = i
            elif "medida" in n:
                i_med = i
            elif "codigo" in n:
                i_cod = i
            elif any(k in n for k in ("alimento", "nome", "descricao")) and i_nome is None:
                i_nome = i
        if i_med is None or i_g is None or (i_cod is None and i_nome is None):
            continue
        for row in linhas[h + 1:]:
            row = list(row) + [None] * (len(linhas[h]) - len(row))
            desc = str(row[i_med] or "").strip()
            g = parse_num(row[i_g])
            if not desc or not g or g <= 0 or g > 5000:
                continue
            a = None
            if i_cod is not None and row[i_cod] not in (None, ""):
                a = por_cod.get(str(row[i_cod]).strip())
            if a is None and i_nome is not None and row[i_nome]:
                a = por_nome.get(normalizar(str(row[i_nome])))
            if a is None:
                st["nao_encontrados"] += 1
                if len(st["exemplos_nao_encontrados"]) < 20:
                    st["exemplos_nao_encontrados"].append(str(row[i_nome] or row[i_cod]))
                continue
            chave = (a.id, normalizar(desc))
            m = existentes.get(chave)
            if m is None:
                m = MedidaCaseira(alimento_id=a.id)
                existentes[chave] = m
                st["novas"] += 1
            else:
                st["atualizadas"] += 1
            m.descricao, m.gramas, m.fonte = desc[:120], g, codigo_fonte_medidas
            db.session.add(m)
    db.session.commit()
    return st


# ───────────────────────── busca e serialização ─────────────────────────
def _escopo(owner_id):
    return (Alimento.ativo.is_(True), or_(Alimento.owner_id.is_(None), Alimento.owner_id == owner_id))


def buscar(q, owner_id, limite=20):
    qn = normalizar(q)
    if not qn:
        return []
    consulta = Alimento.query.filter(*_escopo(owner_id)).options(selectinload(Alimento.medidas), selectinload(Alimento.fonte))
    for w in qn.split():
        consulta = consulta.filter(Alimento.nome_norm.like(f"%{w}%"))
    ordem = case((Alimento.nome_norm.like(f"{qn}%"), 0), else_=1)
    return consulta.order_by(ordem, db.func.length(Alimento.nome_norm), Alimento.nome_norm).limit(limite).all()


def obter(aid, owner_id):
    return (Alimento.query.filter(Alimento.id == aid, *_escopo(owner_id))
            .options(selectinload(Alimento.medidas), selectinload(Alimento.fonte)).first())


def serializar(a):
    return {
        "id": a.id, "nome": a.nome, "grupo": a.grupo, "fonte": a.fonte.codigo if a.fonte else "",
        "per100": {"kcal": a.energia_kcal, "p": a.proteina_g, "c": a.carbo_g, "l": a.lipidio_g, "f": a.fibra_g},
        "fc": a.fator_correcao or 1.0,
        "medidas": [{"id": m.id, "descricao": m.descricao, "gramas": m.gramas} for m in a.medidas],
    }


# ───────────────────────── equivalentes ─────────────────────────
def sugerir_medida(a, gramas):
    """Melhor medida caseira (múltiplos de 0,5) para aproximar `gramas`; None se não houver."""
    melhor = None
    for m in a.medidas:
        n = round(gramas / m.gramas * 2) / 2
        if n < 0.5 or n > 20:
            continue
        erro = abs(n * m.gramas - gramas) / gramas
        if melhor is None or erro < melhor[0]:
            melhor = (erro, n, m)
    if not melhor:
        return None
    _, n, m = melhor
    return {"qtd": n, "descricao": m.descricao, "gramas": round(n * m.gramas, 1), "medida_id": m.id}


def equivalentes(a, gramas, criterio, owner_id, mesmo_grupo=True, limite=12):
    campo = CRITERIOS.get(criterio)
    if not campo:
        raise ValueError("Critério inválido.")
    ref = getattr(a, campo)
    if not ref or ref <= 0:
        raise ValueError(f"O alimento escolhido não tem valor de {criterio} cadastrado (ou é zero).")
    alvo = ref * gramas / 100
    consulta = Alimento.query.filter(*_escopo(owner_id), Alimento.id != a.id,
                                     getattr(Alimento, campo) > 0).options(selectinload(Alimento.medidas), selectinload(Alimento.fonte))
    if mesmo_grupo and a.grupo:
        consulta = consulta.filter(Alimento.grupo == a.grupo)
    base = {"kcal": (a.energia_kcal or 0) * gramas / 100, "p": (a.proteina_g or 0) * gramas / 100,
            "c": (a.carbo_g or 0) * gramas / 100, "l": (a.lipidio_g or 0) * gramas / 100,
            "f": (a.fibra_g or 0) * gramas / 100}
    saida = []
    for c in consulta.limit(2000).all():
        g = alvo / (getattr(c, campo) / 100)
        if g < 5 or g > 1500:
            continue
        v = {"kcal": (c.energia_kcal or 0) * g / 100, "p": (c.proteina_g or 0) * g / 100,
             "c": (c.carbo_g or 0) * g / 100, "l": (c.lipidio_g or 0) * g / 100, "f": (c.fibra_g or 0) * g / 100}
        saida.append({"alimento": c, "gramas": round(g, 1), "medida": sugerir_medida(c, g), "valores": v,
                      "dif": {k: v[k] - base[k] for k in v}})
    saida.sort(key=lambda x: abs(x["dif"]["kcal"]))
    return {"alvo": alvo, "campo": campo, "base": base, "itens": saida[:limite]}


# ───────────────────────── cardápios ─────────────────────────
def validar_estrutura(est, owner_id):
    """Valida e normaliza o cardápio vindo do navegador; recalcula gramas a partir da medida caseira."""
    try:
        dias = est["dias"]
        if not isinstance(dias, list) or not 1 <= len(dias) <= LIM_DIAS:
            raise ValueError(f"Informe de 1 a {LIM_DIAS} dias.")
        ids = {int(it["a"]) for d in dias for r in d.get("refeicoes", [])[:LIM_REFEICOES]
               for it in r.get("itens", [])[:LIM_ITENS]}
        alimentos = {a.id: a for a in Alimento.query.filter(Alimento.id.in_(ids), *_escopo(owner_id))
                     .options(selectinload(Alimento.medidas)).all()} if ids else {}
        saida = {"dias": []}
        total_itens = 0
        for di, d in enumerate(dias):
            refs = []
            for r in d.get("refeicoes", [])[:LIM_REFEICOES]:
                horario = str(r.get("horario") or "")
                horario = horario if re.fullmatch(r"\d{2}:\d{2}", horario) else ""
                itens = []
                for it in r.get("itens", [])[:LIM_ITENS]:
                    a = alimentos.get(int(it["a"]))
                    if a is None:
                        raise ValueError("Alimento inexistente ou sem permissão de uso.")
                    m, q = None, None
                    if it.get("m"):
                        med = next((x for x in a.medidas if x.id == int(it["m"])), None)
                        if med is None:
                            raise ValueError(f"Medida caseira inválida para {a.nome}.")
                        q = float(it.get("q") or 0)
                        if not 0 < q <= 100:
                            raise ValueError("Quantidade de medida caseira fora do intervalo (0–100).")
                        g, m = q * med.gramas, med.id
                    else:
                        g = float(it.get("g") or 0)
                    if not 0 < g <= 5000:
                        raise ValueError("Quantidade fora do intervalo (0–5000 g).")
                    itens.append({"a": a.id, "g": round(g, 1), "m": m, "q": q})
                    total_itens += 1
                refs.append({"nome": str(r.get("nome") or "Refeição")[:60], "horario": horario, "itens": itens})
            saida["dias"].append({"nome": str(d.get("nome") or f"Dia {di + 1}")[:40], "refeicoes": refs})
        if total_itens == 0:
            raise ValueError("Inclua pelo menos um alimento.")
        return saida
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError("Cardápio inválido.") from e
    except ValueError as e:
        if str(e).startswith("could not convert") or "invalid literal" in str(e):
            raise ValueError("Cardápio inválido (valor numérico).") from e
        raise


def _zero():
    return {"kcal": 0.0, "p": 0.0, "c": 0.0, "l": 0.0, "f": 0.0}


def _somar(dest, v):
    for k in dest:
        dest[k] += v[k]


def calcular(est):
    """Enriquece a estrutura com nutrientes por item, refeição e dia (média diária e %VD)."""
    ids = {it["a"] for d in est["dias"] for r in d["refeicoes"] for it in r["itens"]}
    alimentos = {a.id: a for a in Alimento.query.filter(Alimento.id.in_(ids)).options(
        selectinload(Alimento.medidas), selectinload(Alimento.fonte)).all()} if ids else {}
    incompleto, dias_out = False, []
    micros_total, cobertura, n_itens = {}, {}, 0
    for d in est["dias"]:
        tot_dia, refs = _zero(), []
        for r in d["refeicoes"]:
            tot_ref, itens = _zero(), []
            for it in r["itens"]:
                a = alimentos.get(it["a"])
                if a is None:
                    continue
                f = it["g"] / 100
                if any(getattr(a, c) is None for c in CAMPOS):
                    incompleto = True
                v = {"kcal": (a.energia_kcal or 0) * f, "p": (a.proteina_g or 0) * f, "c": (a.carbo_g or 0) * f,
                     "l": (a.lipidio_g or 0) * f, "f": (a.fibra_g or 0) * f}
                med = next((m for m in a.medidas if m.id == it.get("m")), None)
                itens.append({"alimento": a, "g": it["g"], "q": it.get("q"), "medida": med, **v})
                _somar(tot_ref, v)
                REF.acumular_micros(a.micros_dict, f, micros_total, cobertura)
                n_itens += 1
            _somar(tot_dia, tot_ref)
            refs.append({"nome": r["nome"], "horario": r["horario"], "itens": itens, "total": tot_ref})
        dias_out.append({"nome": d["nome"], "refeicoes": refs, "total": tot_dia})
    media = _zero()
    for d in dias_out:
        _somar(media, d["total"])
    n = len(dias_out) or 1
    media = {k: v / n for k, v in media.items()}
    vd = REF.linhas_vd({k: v * n for k, v in media.items()}, micros_total, divisor=n, n_itens=n_itens, cobertura=cobertura)
    return {"dias": dias_out, "media": media, "incompleto": incompleto, "vd": vd}


def comparar(total, metas, tol=TOLERANCIA):
    """Meta x atingido por nutriente, com faixa de tolerância (padrão ±10%)."""
    pares = (("kcal", "kcal", "Energia (kcal)"), ("c", "carb_g", "Carboidratos (g)"),
             ("p", "prot_g", "Proteínas (g)"), ("l", "lip_g", "Lipídios (g)"))
    saida = []
    for k, mk, rot in pares:
        meta = metas.get(mk)
        if not meta:
            continue
        pct = total[k] / meta
        status = "ok" if abs(pct - 1) <= tol else ("abaixo" if pct < 1 else "acima")
        saida.append({"rotulo": rot, "atingido": total[k], "meta": meta, "pct": pct * 100, "status": status})
    return saida


def lista_compras(calc_est):
    """Soma de todos os dias; peso bruto = líquido × fator de correção."""
    ac = {}
    for d in calc_est["dias"]:
        for r in d["refeicoes"]:
            for it in r["itens"]:
                a = it["alimento"]
                x = ac.setdefault(a.id, {"alimento": a, "liquido": 0.0, "bruto": 0.0})
                x["liquido"] += it["g"]
                x["bruto"] += it["g"] * (a.fator_correcao or 1.0)
    return sorted(ac.values(), key=lambda x: ((x["alimento"].grupo or "zzz"), x["alimento"].nome))


def texto_quantidade(item):
    if item.get("medida") and item.get("q"):
        q = f"{item['q']:g}".replace(".", ",")
        return f"{q} × {item['medida'].descricao} ({item['g']:.0f} g)"
    return f"{item['g']:.0f} g"


def para_editor(calc_est):
    """Estado completo (com dados por 100 g e medidas) que o editor JavaScript precisa."""
    dias = []
    for d in calc_est["dias"]:
        refs = []
        for r in d["refeicoes"]:
            itens = []
            for it in r["itens"]:
                s = serializar(it["alimento"])
                itens.append({**s, "a": s["id"], "g": it["g"], "m": it["medida"].id if it["medida"] else None,
                              "q": it["q"]})
            refs.append({"nome": r["nome"], "horario": r["horario"], "itens": itens})
        dias.append({"nome": d["nome"], "refeicoes": refs})
    return {"dias": dias}


def importar_fatores(caminho, usuario_id=None):
    """CSV/XLSX com colunas: alimento (ou codigo), fator de correção (FC), fator de cocção (FCoc), método (opcional)."""
    st = {"atualizados": 0, "nao_encontrados": 0, "invalidos": 0, "exemplos_nao_encontrados": []}
    por_cod, por_nome = {}, {}
    for a in Alimento.query.filter(Alimento.owner_id.is_(None)).all():
        if a.codigo:
            por_cod.setdefault(a.codigo, a)
        por_nome.setdefault(a.nome_norm, a)
    for linhas in _abas(caminho):
        h = _achar_cabecalho(linhas, ("correcao", "coccao", "fc", "rendimento"))
        if h is None:
            continue
        i_cod = i_nome = i_fc = i_fcc = i_met = None
        for i, c in enumerate(linhas[h]):
            n = normalizar(str(c or ""))
            if "codigo" in n:
                i_cod = i
            elif "coccao" in n or "rendimento" in n or "conversao" in n or n in ("fcoc", "fcc", "ic"):
                i_fcc = i
            elif "correcao" in n or n == "fc":
                i_fc = i
            elif "metodo" in n or "preparo" in n:
                i_met = i
            elif any(k in n for k in ("alimento", "nome", "descricao")) and i_nome is None:
                i_nome = i
        if i_fc is None and i_fcc is None:
            continue
        for row in linhas[h + 1:]:
            row = list(row) + [None] * (len(linhas[h]) - len(row))
            a = None
            if i_cod is not None and row[i_cod] not in (None, ""):
                a = por_cod.get(str(row[i_cod]).strip())
            if a is None and i_nome is not None and row[i_nome]:
                a = por_nome.get(normalizar(str(row[i_nome])))
            if not any(c not in (None, "") for c in row):
                continue
            if a is None:
                st["nao_encontrados"] += 1
                if len(st["exemplos_nao_encontrados"]) < 20:
                    st["exemplos_nao_encontrados"].append(str(row[i_nome] if i_nome is not None else row[i_cod]))
                continue
            fc = parse_num(row[i_fc]) if i_fc is not None else None
            fcc = parse_num(row[i_fcc]) if i_fcc is not None else None
            if (fc is not None and not 1.0 <= fc <= 10) or (fcc is not None and not 0.1 <= fcc <= 10):
                st["invalidos"] += 1
                continue
            if fc is not None:
                a.fator_correcao = fc
            if fcc is not None:
                a.fator_coccao = fcc
            if i_met is not None and row[i_met]:
                a.metodo_preparo = str(row[i_met]).strip()[:60]
            db.session.add(a)
            st["atualizados"] += 1
    db.session.commit()
    return st
