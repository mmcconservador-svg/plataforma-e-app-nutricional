// Editor de cardápio: busca de alimentos, medidas caseiras, trocas equivalentes e totais em tempo real.
// Todo texto vindo do servidor entra via textContent (sem innerHTML) para evitar XSS.
(function () {
  var form = document.getElementById("form-cardapio");
  if (!form) return;
  var raiz = document.getElementById("editor");
  var REF = [["Café da manhã", "07:00"], ["Lanche da manhã", "10:00"], ["Almoço", "12:30"],
             ["Lanche da tarde", "15:30"], ["Jantar", "19:00"], ["Ceia", "21:30"]];
  var estado;
  try { estado = JSON.parse(form.getAttribute("data-estado")); } catch (e) { estado = null; }
  if (!estado || !estado.dias || !estado.dias.length) estado = { dias: [novoDia(1)] };
  var atualizadores = [];

  function novoDia(n) {
    return { nome: "Dia " + n, refeicoes: REF.map(function (r) { return { nome: r[0], horario: r[1], itens: [] }; }) };
  }
  function el(tag, props, kids) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "text") e.textContent = props[k];
      else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), props[k]);
      else e.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function fmt(n, d) { var f = Math.pow(10, d || 0); return (Math.round(n * f) / f).toString().replace(".", ","); }
  function zero() { return { kcal: 0, p: 0, c: 0, l: 0, f: 0 }; }
  function soma(a, b) { for (var k in a) a[k] += b[k]; }
  function gramas(it) {
    if (it.m) {
      var md = (it.medidas || []).filter(function (x) { return x.id === it.m; })[0];
      return md ? (parseFloat(it.q) || 0) * md.gramas : 0;
    }
    return parseFloat(it.g) || 0;
  }
  function nutr(it) {
    var f = gramas(it) / 100, p = it.per100 || {};
    return { kcal: (p.kcal || 0) * f, p: (p.p || 0) * f, c: (p.c || 0) * f, l: (p.l || 0) * f, f: (p.f || 0) * f };
  }
  function meta(nome) {
    var v = parseFloat((form.elements[nome].value || "").replace(",", "."));
    return isNaN(v) ? 0 : v;
  }
  function status(valor, alvo) {
    if (!alvo) return "";
    var r = valor / alvo;
    return Math.abs(r - 1) <= 0.1 ? " ✔" : (r < 1 ? " ↓" : " ↑");
  }
  function atualizar() { atualizadores.forEach(function (f) { f(); }); }

  function linhaItem(it, ref, dia) {
    var span = el("span", { class: "suave pequeno" });
    var gSpan = el("span", { class: "pequeno" });
    atualizadores.push(function () {
      var n = nutr(it);
      gSpan.textContent = fmt(gramas(it), 0) + " g";
      span.textContent = fmt(n.kcal, 0) + " kcal · P " + fmt(n.p, 1) + " · C " + fmt(n.c, 1) + " · L " + fmt(n.l, 1);
    });
    var sel = el("select", { "aria-label": "Unidade", onchange: function () {
      var v = sel.value;
      if (v === "g") { it.g = gramas(it) || 100; it.m = null; it.q = null; }
      else { it.m = parseInt(v, 10); it.q = 1; }
      render();
    } }, [el("option", { value: "g", text: "gramas" })].concat((it.medidas || []).map(function (m) {
      var o = el("option", { value: String(m.id), text: m.descricao + " (" + fmt(m.gramas, 0) + " g)" });
      if (it.m === m.id) o.setAttribute("selected", "selected");
      return o;
    })));
    if (!it.m) sel.value = "g";
    var qtd = el("input", { type: "number", min: "0", step: "any", "aria-label": "Quantidade", value: String(it.m ? (it.q || 1) : (it.g || 100)),
      oninput: function () { if (it.m) it.q = parseFloat(qtd.value) || 0; else it.g = parseFloat(qtd.value) || 0; atualizar(); } });
    var linha = el("div", { class: "item" }, [
      el("div", {}, [el("strong", { text: it.nome }), el("br"), span]),
      sel, qtd, gSpan,
      el("span", {}, [
        el("button", { type: "button", class: "btn sec pequeno", text: "Trocas", onclick: function () { trocas(it); } }),
        el("button", { type: "button", class: "btn sec pequeno", text: "✕", "aria-label": "Remover", onclick: function () {
          ref.itens.splice(ref.itens.indexOf(it), 1); render(); } })
      ])
    ]);
    var bloco = el("div", {}, [linha]);
    if (it._trocas) {
      var lista = el("ul", { class: "busca-res" });
      if (it._trocas.erro) lista.appendChild(el("li", { text: it._trocas.erro }));
      (it._trocas.itens || []).forEach(function (s) {
        var txt = s.nome + " — " + fmt(s.gramas, 0) + " g" + (s.sugestao ? " (≈ " + fmt(s.sugestao.qtd, 1) + " × " + s.sugestao.descricao + ")" : "") +
                  " · Δ " + fmt(s.dif_kcal, 0) + " kcal";
        lista.appendChild(el("li", { text: txt, onclick: function () {
          var i = ref.itens.indexOf(it);
          ref.itens[i] = { a: s.id, id: s.id, nome: s.nome, grupo: s.grupo, fonte: s.fonte, per100: s.per100, fc: s.fc, medidas: s.medidas,
                           g: s.gramas, m: s.sugestao ? s.sugestao.medida_id : null, q: s.sugestao ? s.sugestao.qtd : null };
          render();
        } }));
      });
      bloco.appendChild(lista);
    }
    return bloco;
  }

  function trocas(it) {
    var crit = document.getElementById("criterio").value;
    var url = "/api/equivalentes?alimento=" + encodeURIComponent(it.a) + "&gramas=" + encodeURIComponent(gramas(it) || 100) +
              "&criterio=" + encodeURIComponent(crit) + "&grupo=" + (document.getElementById("mesmo-grupo").checked ? "1" : "0");
    fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      it._trocas = d.erro ? { erro: d.erro } : d; render();
    }).catch(function () { it._trocas = { erro: "Falha ao buscar equivalentes." }; render(); });
  }

  function buscaRefeicao(ref) {
    var res = el("ul", { class: "busca-res", hidden: "hidden" });
    var timer = null;
    var campo = el("input", { type: "search", placeholder: "Buscar alimento para adicionar…", "aria-label": "Buscar alimento",
      oninput: function () {
        clearTimeout(timer);
        var q = campo.value.trim();
        if (q.length < 2) { res.hidden = true; return; }
        timer = setTimeout(function () {
          fetch("/api/alimentos?q=" + encodeURIComponent(q), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (lista) {
            res.textContent = "";
            if (!lista.length) res.appendChild(el("li", { text: "Nenhum alimento encontrado. Importe uma tabela ou cadastre um alimento próprio." }));
            lista.forEach(function (a) {
              res.appendChild(el("li", { text: a.nome + " · " + (a.per100.kcal == null ? "?" : fmt(a.per100.kcal, 0)) + " kcal/100 g · " + a.fonte, onclick: function () {
                var m0 = a.medidas.length ? a.medidas[0] : null;
                ref.itens.push({ a: a.id, id: a.id, nome: a.nome, grupo: a.grupo, fonte: a.fonte, per100: a.per100, fc: a.fc, medidas: a.medidas,
                                 g: 100, m: m0 ? m0.id : null, q: m0 ? 1 : null });
                render();
              } }));
            });
            res.hidden = false;
          });
        }, 250);
      } });
    return el("div", {}, [campo, res]);
  }

  function render() {
    atualizadores = [];
    raiz.textContent = "";
    estado.dias.forEach(function (dia, di) {
      var totDia = el("p", { class: "pequeno" });
      var caixa = el("section", { class: "cartao" });
      caixa.appendChild(el("div", { class: "barra" }, [
        el("input", { value: dia.nome, maxlength: "40", "aria-label": "Nome do dia", oninput: function (e) { dia.nome = e.target.value; } }),
        el("span", {}, [
          el("button", { type: "button", class: "btn sec pequeno", text: "Duplicar dia", onclick: function () {
            if (estado.dias.length >= 30) return;
            var c = JSON.parse(JSON.stringify(dia, function (k, v) { return k === "_trocas" ? undefined : v; }));
            c.nome = "Dia " + (estado.dias.length + 1); estado.dias.push(c); render(); } }),
          estado.dias.length > 1 ? el("button", { type: "button", class: "btn sec pequeno", text: "Remover dia", onclick: function () {
            estado.dias.splice(di, 1); render(); } }) : null
        ])
      ]));
      dia.refeicoes.forEach(function (ref) {
        var tot = el("span", { class: "suave pequeno" });
        var bloco = el("div", { class: "refeicao" }, [
          el("div", { class: "barra" }, [
            el("span", {}, [el("input", { value: ref.nome, maxlength: "60", "aria-label": "Refeição", oninput: function (e) { ref.nome = e.target.value; } })]),
            el("input", { type: "time", value: ref.horario || "", "aria-label": "Horário", oninput: function (e) { ref.horario = e.target.value; } }),
            tot
          ])
        ]);
        ref.itens.forEach(function (it) { bloco.appendChild(linhaItem(it, ref, dia)); });
        bloco.appendChild(buscaRefeicao(ref));
        caixa.appendChild(bloco);
        atualizadores.push(function () {
          var t = zero(); ref.itens.forEach(function (it) { soma(t, nutr(it)); });
          tot.textContent = fmt(t.kcal, 0) + " kcal · P " + fmt(t.p, 1) + " · C " + fmt(t.c, 1) + " · L " + fmt(t.l, 1);
        });
      });
      caixa.appendChild(totDia);
      atualizadores.push(function () {
        var t = zero();
        dia.refeicoes.forEach(function (r) { r.itens.forEach(function (it) { soma(t, nutr(it)); }); });
        totDia.textContent = "Total do dia: " + fmt(t.kcal, 0) + " kcal" + status(t.kcal, meta("kcal")) +
          " · P " + fmt(t.p, 1) + " g" + status(t.p, meta("prot_g")) + " · C " + fmt(t.c, 1) + " g" + status(t.c, meta("carb_g")) +
          " · L " + fmt(t.l, 1) + " g" + status(t.l, meta("lip_g")) + " · Fibras " + fmt(t.f, 1) + " g   (✔ dentro de ±10% da meta)";
      });
      raiz.appendChild(caixa);
    });
    atualizar();
  }

  document.getElementById("add-dia").addEventListener("click", function () {
    if (estado.dias.length < 30) { estado.dias.push(novoDia(estado.dias.length + 1)); render(); }
  });
  ["kcal", "carb_g", "prot_g", "lip_g"].forEach(function (n) { form.elements[n].addEventListener("input", atualizar); });

  form.addEventListener("submit", function (ev) {
    var vazio = false;
    var minimo = { dias: estado.dias.map(function (d) {
      return { nome: d.nome, refeicoes: d.refeicoes.map(function (r) {
        return { nome: r.nome, horario: r.horario, itens: r.itens.map(function (it) {
          if (gramas(it) <= 0) vazio = true;
          return it.m ? { a: it.a, m: it.m, q: parseFloat(it.q) || 0 } : { a: it.a, g: parseFloat(it.g) || 0 };
        }) };
      }) };
    }) };
    if (vazio) { ev.preventDefault(); alert("Há alimento com quantidade zero. Corrija ou remova."); return; }
    form.elements["estrutura"].value = JSON.stringify(minimo);
    form.elements["estado"].value = JSON.stringify(estado, function (k, v) { return k === "_trocas" ? undefined : v; });
  });
  render();
})();
