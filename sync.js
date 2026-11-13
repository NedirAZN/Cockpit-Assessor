/* Cockpit BFR — sincronização do estado.
 *
 * O cockpit guarda CRM, metas, mix, produtos, boletas e simulações no
 * localStorage. Isso prende tudo a um navegador: o que você anota no PC some no
 * celular. Este arquivo troca o miolo do localStorage por um espelho do banco,
 * sem tocar em uma linha do cockpit — ele continua chamando getItem e setItem
 * como sempre, só que agora a escrita vai para o Postgres.
 *
 * Roda antes do script principal, então a primeira leitura já vê os dados certos.
 */
(function () {
  "use strict";

  var CHAVES = [
    "bfr_crm_v1", "bfr_overrides_v1", "bfr_metas_v1", "bfr_mix_v1",
    "bfr_produtos_v1", "bfr_produtos_v3_ago26", "bfr_boletas_jul26",
    "bfr_sim_v3", "bfr_atualizacao_v1", "bfr_brapi_token",
    "bfr_produtos_lixeira_v1"
  ];

  var memoria = Object.assign({}, window.__BFR_ESTADO__ || {});
  var pendentes = {};
  var timer = null;
  var salvandoAgora = false;

  var origGet = Storage.prototype.getItem;
  var origSet = Storage.prototype.setItem;
  var origDel = Storage.prototype.removeItem;

  function ehLocal(alvo) {
    try { return alvo === window.localStorage; } catch (e) { return false; }
  }
  function nossa(chave) { return CHAVES.indexOf(chave) !== -1; }

  Storage.prototype.getItem = function (chave) {
    if (ehLocal(this) && nossa(chave)) {
      return Object.prototype.hasOwnProperty.call(memoria, chave) ? memoria[chave] : null;
    }
    return origGet.call(this, chave);
  };

  Storage.prototype.setItem = function (chave, valor) {
    if (ehLocal(this) && nossa(chave)) {
      memoria[chave] = String(valor);
      pendentes[chave] = String(valor);
      try { origSet.call(this, chave, valor); } catch (e) {}  // cópia local de socorro
      agendar();
      return;
    }
    return origSet.call(this, chave, valor);
  };

  Storage.prototype.removeItem = function (chave) {
    if (ehLocal(this) && nossa(chave)) {
      delete memoria[chave];
      pendentes[chave] = null;
      try { origDel.call(this, chave); } catch (e) {}
      agendar();
      return;
    }
    return origDel.call(this, chave);
  };

  function agendar() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(salvar, 800);   // agrupa rajadas de escrita numa chamada só
    marcar("pendente");
  }

  function salvar() {
    if (salvandoAgora) { agendar(); return; }
    var lote = pendentes;
    if (!Object.keys(lote).length) return;
    pendentes = {};
    salvandoAgora = true;

    fetch("/api/estado", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lote),
      credentials: "same-origin"
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      marcar("salvo");
    }).catch(function (err) {
      Object.keys(lote).forEach(function (k) {
        if (!Object.prototype.hasOwnProperty.call(pendentes, k)) pendentes[k] = lote[k];
      });
      marcar("erro");
      console.error("[sync] falha ao gravar, vou tentar de novo:", err);
      setTimeout(salvar, 5000);
    }).finally(function () {
      salvandoAgora = false;
    });
  }

  window.addEventListener("beforeunload", function (e) {
    if (Object.keys(pendentes).length) {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/estado", new Blob(
          [JSON.stringify(pendentes)], { type: "application/json" }));
        return;
      }
      e.preventDefault();
      e.returnValue = "";
    }
  });

  var selo = null;
  function marcar(estado) {
    if (!selo) {
      selo = document.createElement("div");
      selo.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:9999;" +
        "font:12px ui-sans-serif,system-ui,sans-serif;padding:6px 10px;border-radius:8px;" +
        "border:1px solid #1F2E48;background:#111B2E;color:#8FA3C0;opacity:.9;" +
        "transition:opacity .4s";
      (document.body || document.documentElement).appendChild(selo);
    }
    if (estado === "pendente") { selo.textContent = "salvando…"; selo.style.color = "#8FA3C0"; selo.style.opacity = ".9"; }
    if (estado === "salvo")    { selo.textContent = "salvo"; selo.style.color = "#3DDC97"; setTimeout(function(){ selo.style.opacity = "0"; }, 1500); }
    if (estado === "erro")     { selo.textContent = "sem conexão — tentando de novo"; selo.style.color = "#FF6B6B"; selo.style.opacity = ".95"; }
  }
})();
