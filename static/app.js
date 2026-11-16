(function () {
  // Campo espiritual só habilita com o consentimento específico marcado.
  var chk = document.getElementById("c_espiritual");
  var campo = document.getElementById("campo_espiritual");
  if (chk && campo) {
    chk.addEventListener("change", function () { campo.disabled = !chk.checked; });
  }

  // Objetivo → sugestão EDITÁVEL de ajuste energético e distribuição de macros (decisão final é do profissional).
  var PRESETS = {
    manutencao: { ajuste: 0, c: 50, p: 20, l: 30 },
    emagrecimento: { ajuste: -500, c: 45, p: 25, l: 30 },
    ganho: { ajuste: 300, c: 50, p: 20, l: 30 },
    hipertrofia: { ajuste: 300, c: 50, p: 25, l: 25 }
  };
  var sel = document.getElementById("objetivo_preset");
  if (sel) {
    sel.addEventListener("change", function () {
      var p = PRESETS[sel.value];
      if (!p) return;
      document.querySelector("[name=ajuste_kcal]").value = p.ajuste;
      document.querySelector("[name=pct_carb]").value = p.c;
      document.querySelector("[name=pct_prot]").value = p.p;
      document.querySelector("[name=pct_lip]").value = p.l;
    });
  }

  // PWA: registra o service worker (não guarda páginas autenticadas).
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }
})();
